"""自动精读卡片生成。

不依赖大模型也能给出结构化的中文解读：中文由"分析框架 + 英文证据"组成，
所有论断都能追溯到摘要原句，避免编造。开启 [llm] 后，会用大模型把这些
字段润色成更连贯的中文（原文摘要句仍然保留，便于核对）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .classify import novelty_cues
from .config import Config
from .model import Paper
from .textutil import (
    detect_methods,
    detect_numbers,
    detect_objects,
    detect_terms,
    method_short,
    split_sentences,
    trim,
)

RESULT_CUES = re.compile(
    r"\b(show|shows|shown|demonstrat|found|find|reveal|indicat|observ|"
    r"agree|predict|reduc|increas|decreas|improv|delay|suppress|enhanc|"
    r"achiev|obtain|conclud|suggest|attribute|correlat|scal)\w*",
    re.I,
)
PROBLEM_CUES = re.compile(
    r"\b(we|here|this (paper|study|work)|present|investigat|examin|analyz|"
    r"study|consider|aim|focus|report|propose|develop)\b",
    re.I,
)
CONTROL_CUES = re.compile(r"\b(reduc|suppress|delay|mitigat|control|improv)\w*", re.I)
VALIDATION_CUES = re.compile(r"\b(agree|validat|benchmark|compar)\w*", re.I)
MECHANISM_CUES = re.compile(r"\b(mechanism|caus|origin|underlying|because|due to|driven)\w*", re.I)
SCALING_CUES = re.compile(r"\b(scal|universal|self[\s-]similar|∝)\w*", re.I)

TIER_TEXT = {
    1: "你的核心期刊",
    2: "领域内认可期刊",
    3: "扩展/交叉期刊",
}

# 方向 id → 一句"和你什么关系"的定性描述。
# 键名要和 config.toml 里 [[topics]] 的 id 对应；没配的走下面的默认句式。
TOPIC_RELATION: dict[str, str] = {}    # 例：{"machine-learning": "落在你关注的「机器学习」主线上"}


@dataclass
class Summary:
    one_liner: str = ""
    subject: str = ""
    key_quote: str = ""
    highlights: list[str] = field(default_factory=list)
    relation: list[str] = field(default_factory=list)
    why_read: list[str] = field(default_factory=list)
    problem: str = ""
    methods_line: str = ""
    setup_line: str = ""
    results: list[tuple[str, str]] = field(default_factory=list)   # (aspect, sentence)
    numbers: list[str] = field(default_factory=list)
    terms: list[tuple[str, str]] = field(default_factory=list)
    reuse: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    read_plan: str = ""
    generated_by: str = "rule"     # rule | llm


def _aspect(sentence: str) -> str:
    if CONTROL_CUES.search(sentence):
        return "控制效果"
    if VALIDATION_CUES.search(sentence):
        return "验证"
    if SCALING_CUES.search(sentence):
        return "标度关系"
    if MECHANISM_CUES.search(sentence):
        return "机理"
    return "结果"


def _pick_problem(sentences: list[str]) -> str:
    for sentence in sentences[:3]:
        if PROBLEM_CUES.search(sentence):
            return trim(sentence, 260)
    return trim(sentences[0], 260) if sentences else ""


def _pick_results(sentences: list[str], problem: str, limit: int = 3) -> list[tuple[str, str]]:
    scored: list[tuple[float, int, str]] = []
    for idx, sentence in enumerate(sentences):
        if sentence == problem or len(sentence) < 30:
            continue
        score = 0.0
        if RESULT_CUES.search(sentence):
            score += 2.0
        if re.search(r"\d", sentence):
            score += 1.0
        if CONTROL_CUES.search(sentence) or MECHANISM_CUES.search(sentence):
            score += 0.5
        if idx >= len(sentences) - 2:
            score += 0.8      # 结尾常是结论
        if score >= 2.0:
            scored.append((score, idx, sentence))
    scored.sort(key=lambda item: (-item[0], item[1]))
    picked = sorted(scored[:limit], key=lambda item: item[1])
    return [(_aspect(sentence), trim(sentence, 280)) for _, _, sentence in picked]


def _topic_line(paper: Paper) -> str:
    labels = [m.label for m in paper.matches[:2]]
    return " / ".join(labels) if labels else "领域相关"


def _relation(paper: Paper, cfg: Config, methods: list[tuple[str, str]]) -> list[str]:
    bullets: list[str] = []
    # 标题命中的方向才是文章主线，优先展示
    ordered = sorted(
        paper.matches,
        key=lambda m: (0 if m.title_hits else 1,
                       0 if m.priority == "core" else 1,
                       -m.weight),
    )
    for idx, match in enumerate(ordered[:2]):
        head = TOPIC_RELATION.get(match.topic_id, f"与「{match.label}」相关")
        if idx == 1:
            bullets.append(f"另外也涉及「{match.label}」。")
            continue
        terms: list[str] = []
        for term in (match.title_hits[:2] + match.abstract_hits[:1]):
            if term.lower() not in [t.lower() for t in terms]:
                terms.append(term)
        evidence = "、".join(f"`{t}`" for t in terms)
        if match.title_hits:
            bullets.append(f"{head}：标题直接点题，出现 {evidence}。")
        else:
            bullets.append(f"{head}：摘要中出现 {evidence}（标题未提，需确认是否为文章主线）。")

    objects = detect_objects(paper.title) or detect_objects(f"{paper.title}. {paper.abstract[:500]}")
    if objects:
        bullets.append("研究对象为" + "、".join(objects[:3]) + "。")

    numbers = detect_numbers(f"{paper.title}. {paper.abstract}")
    if numbers:
        bullets.append(f"摘要给出的量级参考：{'、'.join(numbers[:3])}，便于判断是否与你的数据可比。")

    if paper.has_advisor_cite:
        cited = paper.advisor_cites[0]
        bullets.append(f"引用了关注学者工作《{cited.label()}》，处在同一学术脉络上。")
    return bullets[:4]


def _why_read(paper: Paper, cfg: Config, methods: list[tuple[str, str]]) -> list[str]:
    bullets: list[str] = []
    rel = paper.score_parts.get("relevance", 0)
    core_hit = any(m.priority == "core" for m in paper.matches)
    if core_hit:
        bullets.append(f"**相关度 {rel:.1f}**：命中核心方向，且"
                       + ("标题直接点题，通常不是边角工作。"
                          if any(m.title_hits and m.priority == "core" for m in paper.matches)
                          else "摘要中多处呼应你的问题。"))
    else:
        bullets.append(f"**相关度 {rel:.1f}**：属于外围相关，按需选读。")

    cues = novelty_cues(paper)
    if cues:
        bullets.append("**新意信号**：" + "；".join(label for _, label in cues[:3]) + "。")

    if paper.has_advisor_cite:
        bullets.append(f"**与课题组对话**：引用了关注学者 {len(paper.advisor_cites)} 篇工作，"
                       f"可看它如何接续/挑战这些结论。")

    if any(m[0] in {"benchmark", "dataset", "metric"} for m in methods):
        bullets.append("**可作对照**：带数据集/基准评测，能直接和你的结果横向比较。")
    elif any(m[0] in {"ablation", "finetune", "pretrain"} for m in methods):
        bullets.append("**方法可复用**：训练/消融流程可以搬到你的任务上。")

    if paper.is_preprint:
        bullets.append("**载体**：arXiv 预印本，尚未同行评审——可抢先看思路，引用前确认是否已正式发表。")
    elif paper.tier <= 2:
        bullets.append(f"**期刊层级**：{paper.journal}（{TIER_TEXT.get(paper.tier, '领域期刊')}），"
                       f"适合作为可引用的方法/结果来源。")
    else:
        bullets.append(f"**期刊层级**：{paper.journal}（{TIER_TEXT.get(paper.tier, '扩展期刊')}）——"
                       f"内容贴切时仍值得读，引用时注意分量。")

    if paper.priority == "C":
        bullets.append("**提醒**：综合分偏低，先看摘要与结论，确认是否值得投入整篇时间。")
    return bullets[:5]


def _reuse(paper: Paper, methods: list[tuple[str, str]], numbers: list[str]) -> list[str]:
    bullets: list[str] = []
    tags = {m[0] for m in methods}
    if tags & {"benchmark", "metric", "dataset"}:
        bullets.append("对照点：它用的数据集与评价指标，可以直接搬来做你的 baseline 对比。")
    if tags & {"ablation", "finetune", "pretrain"}:
        bullets.append("方法侧：训练/微调/消融这套流程可以套用到你自己的任务上。")
    if tags & {"rag", "prompt", "embedding"}:
        bullets.append("工程侧：检索或提示词的组织方式，可以作为你搭原型的起点。")
    if paper.is_review:
        bullets.append("综述：适合放进引言框架，快速补齐领域脉络与代表性文献。")
    if numbers:
        bullets.append(f"量级参照：{'、'.join(numbers[:3])}，可用于判断你的数据是否处于同一区间。")
    if not bullets:
        bullets.append("暂未从摘要中识别出可直接复用的数据/方法，建议读正文图表后再判断。")
    return bullets[:3]


def _caveats(paper: Paper, cfg: Config) -> list[str]:
    out: list[str] = []
    if paper.is_preprint:
        out.append("预印本：未经同行评审，结论可能修改，引用前确认是否已正式发表。")
    if not paper.abstract:
        out.append("OpenAlex 未收录摘要：以上判断仅基于标题与元数据，务必打开原文核对。")
    elif not detect_numbers(paper.abstract):
        out.append("摘要未给出定量结果，具体增益需读正文。")
    if paper.tier >= 3:
        out.append("非核心期刊：方法细节与结论强度需更谨慎评估。")
    if paper.has_advisor_cite and paper.score_parts.get("relevance", 0) < 2.5:
        out.append("与你方向关联较弱，仅因引用关注学者工作而收录。")
    return out[:3]


def _read_plan(paper: Paper, methods: list[tuple[str, str]]) -> str:
    method_text = "、".join(m[1] for m in methods[:2]) or "其方法节"
    if paper.priority == "A":
        return (f"**精读（60–90 分钟）**：先看 Fig. 1 的构型与流场示意，再读引言末尾的问题陈述；"
                f"重点核对{method_text}的边界条件与来流参数是否与你的算例同量级；"
                f"最后逐条比对结论节与你自己观察到的现象。")
    if paper.priority == "B":
        return (f"**选读（约 30 分钟）**：摘要 → 方法概述 → 结论与图注；"
                f"重点看它与你方向的交叉点，其余部分按需回查。")
    if paper.has_advisor_cite:
        return "**定向阅读（15 分钟）**：直接找正文里引用关注学者工作的位置，看引用动机与后续展开。"
    return "**速览（5 分钟）**：只看摘要与结论，决定是否留档。"


def summarize(paper: Paper, cfg: Config) -> Summary:
    text = paper.abstract or ""
    sentences = split_sentences(text)
    methods = detect_methods(f"{paper.title}. {text}")
    numbers = detect_numbers(text)
    problem = _pick_problem(sentences)
    results = _pick_results(sentences, problem)

    summary = Summary()
    summary.problem = problem
    summary.methods_line = "、".join(m[1] for m in methods) if methods else ""
    summary.results = results
    summary.numbers = numbers
    summary.terms = detect_terms(f"{paper.title}. {text}")[:10]
    summary.highlights = [label for _, label in novelty_cues(paper)][:4]
    summary.relation = _relation(paper, cfg, methods)
    summary.why_read = _why_read(paper, cfg, methods)
    summary.reuse = _reuse(paper, methods, numbers)
    summary.caveats = _caveats(paper, cfg)
    summary.read_plan = _read_plan(paper, methods)

    objects = detect_objects(paper.title) or detect_objects(f"{paper.title}. {paper.abstract[:500]}")
    setup_parts: list[str] = []
    if objects:
        setup_parts.append("、".join(objects[:3]))
    if numbers:
        setup_parts.append("、".join(numbers[:4]))
    summary.setup_line = "；".join(setup_parts)

    topic_line = _topic_line(paper)
    subject = "、".join(objects[:2]) if objects else topic_line
    summary.subject = subject
    methods_short = "、".join(method_short(m[1]) for m in methods[:2])
    if results:
        summary.key_quote = results[0][1]
    elif problem:
        summary.key_quote = problem
    else:
        summary.key_quote = ""

    if summary.key_quote:
        head = subject + (f"（{methods_short}）" if methods_short and methods_short != subject else "")
        summary.one_liner = f"{head}：{trim(summary.key_quote, 170)}"
    else:
        summary.one_liner = f"{subject}｜{paper.journal}｜（暂无摘要，建议直接看原文）"

    if not summary.highlights:
        summary.highlights = ["摘要未给出显式的新颖性表述，可按常规论文阅读。"]
    return summary


def merge_llm(summary: Summary, payload: dict) -> Summary:
    """把大模型返回的字段覆盖到规则结果上（缺字段则保留原值）。"""
    def _str(key: str) -> str:
        value = payload.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else ""

    def _list(key: str) -> list[str]:
        value = payload.get(key)
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    for attr in ("one_liner", "problem", "methods_line", "setup_line", "read_plan"):
        value = _str(attr)
        if value:
            setattr(summary, attr, value)
    for attr in ("highlights", "relation", "why_read", "reuse", "caveats", "numbers"):
        value = _list(attr)
        if value:
            setattr(summary, attr, value)

    results = payload.get("results")
    if isinstance(results, list):
        parsed: list[tuple[str, str]] = []
        for item in results:
            if isinstance(item, dict) and item.get("text"):
                parsed.append((str(item.get("aspect") or "结果"), str(item["text"])))
            elif isinstance(item, str):
                parsed.append(("结果", item))
        if parsed:
            summary.results = parsed
    summary.generated_by = "llm"
    return summary
