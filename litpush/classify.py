"""方向匹配与打分。

打分 = 方向相关度 + 期刊层级 + 新颖性线索 + 引用关注学者加成 - 预印本折扣。
阈值把结果分成 A（精读）/ B（选读）/ C（存档）三档。
"""

from __future__ import annotations

import re
from typing import Iterable

from .config import Config, Topic
from .model import Paper, TopicMatch

NOVELTY_CUES: list[tuple[str, str, str]] = [
    (r"\bfor the first time\b|\bfirst[\s-]time\b|\bfirst experimental\b|\bfirst direct\b",
     "first", "首次报道 / 首次直接观测"),
    (r"\bnew\s+(mechanism|scaling|model|insight|framework|theory)\b|\bnovel\b|\bwe propose\b",
     "new", "提出新机制/新模型"),
    (r"\bscal(e|ing)\s+law\b|\buniversal\b|\buniversality\b|\bself[\s-]similar\b",
     "scaling", "给出标度律/普适性关系"),
    (r"\bmechanism\b|\bunderlying\b|\bcauses?\b|\borigin of\b",
     "mechanism", "追问机理（而非只做算例）"),
    (r"\bvalidat(e|ed|ion)\b|\bexcellent agreement\b|\bgood agreement\b|\bbenchmark\b",
     "validation", "与实验/理论定量对比验证"),
    (r"\bdatabase\b|\bdataset\b|\bopen[\s-]data\b|\bmeasurements? of\b",
     "data", "提供可复用的数据/测量"),
    (r"\bcontrol\b|\bsuppress\b|\bdelay\b|\bmitigat", "control", "与控制/抑制/延迟相关"),
    (r"\breview\b|\bsurvey\b|\boverview\b|\bperspective\b", "review", "综述，适合快速建立框架"),
]
_NOVELTY_RE = [(re.compile(p, re.I), tag, label) for p, tag, label in NOVELTY_CUES]


def _sentences(text: str) -> list[str]:
    from .textutil import split_sentences

    return split_sentences(text)


class TopicMatcher:
    def __init__(self, topics: Iterable[Topic]):
        self.topics = list(topics)
        self._compiled = [
            (topic, [re.compile(p, re.I) for p in topic.patterns]) for topic in self.topics
        ]
        self._guards = {
            topic.id: (re.compile(topic.guard, re.I) if topic.guard else None)
            for topic in self.topics
        }

    def match(self, paper: Paper) -> list[TopicMatch]:
        title = paper.title or ""
        abstract = paper.abstract or ""
        text = f"{title}. {abstract}"
        out: list[TopicMatch] = []
        for topic, patterns in self._compiled:
            guard = self._guards.get(topic.id)
            if guard and not guard.search(text):
                continue          # 语境不对（如物理学里的 phase transition），跳过
            item = TopicMatch(topic_id=topic.id, label=topic.label,
                              weight=topic.weight, priority=topic.priority)
            for rx in patterns:
                for m in rx.finditer(title):
                    snippet = m.group(0).strip()
                    if snippet and snippet.lower() not in [s.lower() for s in item.title_hits]:
                        item.title_hits.append(snippet)
                if len(item.abstract_hits) >= 6:
                    continue
                for m in rx.finditer(abstract):
                    snippet = m.group(0).strip()
                    if snippet and snippet.lower() not in [s.lower() for s in item.abstract_hits]:
                        item.abstract_hits.append(snippet)
                        if len(item.abstract_hits) >= 6:
                            break
            if item.total:
                out.append(item)
        return out


def novelty_cues(paper: Paper) -> list[tuple[str, str]]:
    text = f"{paper.title}. {paper.abstract}"
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for rx, tag, label in _NOVELTY_RE:
        if tag in seen:
            continue
        if rx.search(text):
            found.append((tag, label))
            seen.add(tag)
    return found


def score_paper(paper: Paper, cfg: Config) -> Paper:
    relevance = 0.0
    for match in paper.matches:
        # 标题命中才算"文章主线"；只出现在摘要里的，降权为 0.55
        factor = 1.0 if match.title_hits else 0.55
        repeat = 1 + 0.15 * min(max(len(match.title_hits) - 1, 0), 2)
        value = match.weight * factor * repeat
        relevance += min(value, match.weight * 1.3)

    tier_bonus = {1: cfg.scoring.tier1_bonus,
                  2: cfg.scoring.tier2_bonus}.get(paper.tier, cfg.scoring.tier3_bonus)
    # 重点方向（高权重 core 方向）命中标题：这篇文章很可能"正好在做你那件事"
    focus = any(m.priority == "core" and m.weight >= 4.0 and m.title_hits
                for m in paper.matches)
    focus_bonus = cfg.scoring.focus_title_bonus if focus else 0.0
    novelty = min(0.35 * len(novelty_cues(paper)), 1.05)
    advisor = cfg.advisor.bonus if paper.has_advisor_cite else 0.0
    penalty = cfg.scoring.preprint_penalty if paper.is_preprint else 0.0

    total = relevance + tier_bonus + focus_bonus + novelty + advisor - penalty
    paper.score = round(total, 2)
    paper.score_parts = {
        "relevance": round(relevance, 2),
        "journal": round(tier_bonus, 2),
        "focus": round(focus_bonus, 2),
        "novelty": round(novelty, 2),
        "advisor": round(advisor, 2),
        "preprint": round(-penalty, 2),
    }
    if paper.score >= cfg.scoring.a_threshold:
        paper.priority = "A"
    elif paper.score >= cfg.scoring.b_threshold:
        paper.priority = "B"
    elif paper.score >= cfg.scoring.c_threshold:
        paper.priority = "C"
    else:
        paper.priority = "D"
    return paper


def is_relevant(paper: Paper, cfg: Config) -> bool:
    core = [m for m in paper.matches if m.priority == "core"]
    if core:
        return True
    if paper.has_advisor_cite and cfg.advisor.keep_offtopic:
        return True
    return sum(m.weight for m in paper.matches) >= 2.2
