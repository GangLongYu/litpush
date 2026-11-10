"""文本处理小工具：分句、方法/现象识别、术语对照、关键数据抽取。"""

from __future__ import annotations

import re

# ------------------------------------------------------------------ 分句
_NUL = "\x00"
_PROTECT = [
    (r"\b(e\.g|i\.e|et al|Fig|Figs|Eq|Eqs|approx|vs|cf|Ref|Refs|resp|al|No|"
     r"Phys|Rev|Lett|Proc|Natl|Acad|Sci|Eng|Mech|Fluid|Comput|Appl|Int|Univ|"
     r"Dept|Inst|Tech|Chem|Math|Stat|vol|pp|Sec|Tab|Ch|Chap)\.", r"\1" + _NUL),
    (r"\b([A-Z])\.", r"\1" + _NUL),          # 人名缩写 J. Smith
    (r"(\d)\.(\d)", r"\1" + _NUL + r"\2"),   # 小数
    (r"(\d)\.(?=\s*(?:%|kHz|MHz|mm|km|s\b))", r"\1" + _NUL),
]


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    work = " ".join(text.split())
    for pattern, repl in _PROTECT:
        work = re.sub(pattern, repl, work)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", work)
    out = []
    for part in parts:
        sentence = part.replace("\x00", ".").strip()
        if len(sentence) > 3:
            out.append(sentence)
    return out


def trim(text: str, limit: int = 240) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + " …"


# ------------------------------------------------------------------ 方法识别
METHODS: list[tuple[str, str, str]] = [
    (r"\bdeep learning\b", "deep-learning", "深度学习"),
    (r"\breinforcement learning\b", "rl", "强化学习"),
    (r"\bsupervised learning\b", "supervised", "监督学习"),
    (r"\bself[\s-]*supervised", "ssl", "自监督学习"),
    (r"\btransfer learning\b", "transfer", "迁移学习"),
    (r"\bgraph neural network|\bGNN\b", "gnn", "图神经网络（GNN）"),
    (r"\bconvolutional neural network|\bCNN\b", "cnn", "卷积网络（CNN）"),
    (r"\brecurrent neural network|\bRNN\b|\bLSTM\b", "rnn", "循环网络（RNN/LSTM）"),
    (r"\btransformer\b", "transformer", "Transformer"),
    (r"\bdiffusion model\b", "diffusion", "扩散模型"),
    (r"\battention\b", "attention", "注意力机制"),
    (r"\bfine[\s-]*tun", "finetune", "微调（fine-tuning）"),
    (r"\bpre[\s-]*train", "pretrain", "预训练"),
    (r"\bprompt\b", "prompt", "提示（prompt）"),
    (r"\bretrieval[\s-]*augmented|\bRAG\b", "rag", "检索增强（RAG）"),
    (r"\bembedding", "embedding", "嵌入表示"),
    (r"\brandom forest\b|\bXGBoost\b|\bgradient boosting\b", "tree", "树模型/集成学习"),
    (r"\bbayesian\b", "bayes", "贝叶斯方法"),
    (r"\bmonte carlo\b", "mcmc", "蒙特卡洛"),
    (r"\bA/?B test|\bablation\b", "ablation", "消融实验"),
    (r"\bbenchmark\b", "benchmark", "基准测试"),
    (r"\bevaluation metric|\baccuracy\b|\bF1\b", "metric", "评价指标"),
    (r"\bdataset\b|\bcorpus\b", "dataset", "数据集/语料"),
]

METHOD_RE = [(re.compile(p, re.I), tag, zh) for p, tag, zh in METHODS]

# ------------------------------------------------------------------ 术语对照
GLOSSARY: list[tuple[str, str]] = [
    (r"\bmachine learning\b", "机器学习"),
    (r"\bdeep learning\b", "深度学习"),
    (r"\bneural network\b", "神经网络"),
    (r"\blarge language model\b|\bLLM\b", "大语言模型"),
    (r"\bfoundation model\b", "基础模型"),
    (r"\btransformer\b", "Transformer 架构"),
    (r"\battention mechanism\b", "注意力机制"),
    (r"\bfine[\s-]*tuning\b", "微调"),
    (r"\bpre[\s-]*training\b", "预训练"),
    (r"\binference\b", "推理"),
    (r"\bembedding\b", "嵌入表示"),
    (r"\bbenchmark\b", "基准测试"),
    (r"\bstate[\s-]*of[\s-]*the[\s-]*art\b|\bSOTA\b", "当前最优（SOTA）"),
    (r"\bdataset\b", "数据集"),
    (r"\bground truth\b", "真值/标注"),
    (r"\bgeneralization\b", "泛化能力"),
    (r"\boverfitting\b", "过拟合"),
    (r"\bregression\b", "回归"),
    (r"\bclassification\b", "分类"),
    (r"\bclustering\b", "聚类"),
    (r"\bunsupervised\b", "无监督"),
    (r"\bsupervised\b", "有监督"),
    (r"\bprompt engineering\b", "提示工程"),
    (r"\bchain[\s-]*of[\s-]*thought\b", "思维链"),
    (r"\bhallucination\b", "幻觉"),
    (r"\bknowledge graph\b", "知识图谱"),
    (r"\bdata augmentation\b", "数据增广"),
    (r"\buncertainty quantification\b", "不确定性量化"),
]

GLOSSARY_RE = [(re.compile(p, re.I), zh) for p, zh in GLOSSARY]

# ------------------------------------------------------------------ 关键数据
NUMBER_PATTERNS = [
    r"\bRe\s*[=≈~]\s*[0-9][0-9.,×x\^\s]{0,14}",
    r"\bMach\s*[0-9]+(?:\.[0-9]+)?",
    r"\bM\s*[=≈]\s*[0-9]+(?:\.[0-9]+)?",
    r"\bSt\s*[=≈]\s*[0-9][0-9.,×x\^\s]{0,10}",
    r"\b[0-9]+(?:\.[0-9]+)?\s*(?:%|per cent)",
    r"\b[0-9]+(?:\.[0-9]+)?\s*(?:kHz|MHz|Hz)\b",
    r"\b[0-9]+(?:\.[0-9]+)?\s*(?:°|deg)\b",
    r"\b[0-9]+(?:\.[0-9]+)?\s*(?:mm|cm|m/s|K)\b",
]

OBJECTS: list[tuple[str, str]] = [
    (r"\bdataset\b", "数据集"),
    (r"\bbenchmark\b", "基准"),
    (r"\bmodel\b", "模型"),
    (r"\bcorpus\b", "语料库"),
    (r"\bknowledge base\b", "知识库"),
]


def detect_methods(text: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for rx, tag, zh in METHOD_RE:
        if tag in seen:
            continue
        if rx.search(text):
            seen.add(tag)
            out.append((tag, zh))
    return out


def method_short(zh: str) -> str:
    """把"直接数值模拟（DNS）"压成"DNS"，用于卡片标题行。"""
    match = re.search(r"[（(]([^）)]+)[）)]", zh)
    return match.group(1) if match else zh


def detect_terms(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for rx, zh in GLOSSARY_RE:
        if zh in seen:
            continue
        m = rx.search(text)
        if m:
            seen.add(zh)
            out.append((m.group(0), zh))
    return out


def detect_objects(text: str) -> list[str]:
    found: list[str] = []
    for pattern, zh in OBJECTS:
        if re.search(pattern, text, re.I) and zh not in found:
            found.append(zh)
    return found


def detect_numbers(text: str, limit: int = 6) -> list[str]:
    found: list[str] = []
    for pattern in NUMBER_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            value = " ".join(m.group(0).split()).strip(" ,;:")
            value = value.replace(" %", "%").replace(" K", " K")
            if value and value not in found:
                found.append(value)
            if len(found) >= limit:
                return found
    return found
