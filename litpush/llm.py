"""可选的大模型润色（OpenAI 兼容接口）。

只在 [llm] enabled = true 且环境变量里有 key 时启用；任何失败都会静默回退到
规则版摘要，保证每日任务不因为模型服务抖动而失败。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .config import Config
from .model import Paper
from .summarize import Summary, merge_llm

SYSTEM_PROMPT = (
    "你是一位科研工作者，正在给自己的研究笔记写文献速读卡片。"
    "要求：只依据给出的标题与摘要，不臆造数据、不夸大结论；"
    "中文表述，术语首次出现可保留英文；判断要具体（指出它到底做了什么、结论是什么、为什么与读者相关）。"
    "输出严格为 JSON，不要任何多余文字。"
)

USER_TEMPLATE = """请阅读下面这篇文献，输出 JSON（字段全部用中文填写）：

标题：{title}
期刊：{journal}（{year}）
匹配到的方向：{topics}
摘要原文：
{abstract}

JSON 字段要求：
{{
  "one_liner": "一句话结论，40-80 字，说清做了什么+最关键的发现",
  "highlights": ["特色/亮点，2-4 条，每条一句话"],
  "relation": ["与读者的关系，2-3 条：命中哪个方向、工况是否可比、方法是否可迁移"],
  "why_read": ["为什么值得读，2-3 条：期刊分量、新意、可用性"],
  "problem": "它要解决的具体问题，1-2 句",
  "methods_line": "用了什么方法/实验手段，简短列举",
  "setup_line": "构型与工况（Mach/Re/攻角等），没有就写"摘要未给出"",
  "results": [{{"aspect": "机理|验证|控制效果|标度关系|结果", "text": "结论原句的中文转述，保留关键数字"}}],
  "reuse": ["对读者可复用的点，1-2 条"],
  "caveats": ["局限/存疑，0-2 条，没有就给空数组"],
  "read_plan": "建议怎么读：精读/选读/速览 + 读哪几节"
}}"""


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def polish(paper: Paper, summary: Summary, cfg: Config) -> Summary | None:
    if not cfg.llm.enabled:
        return None
    key = cfg.llm.api_key()
    if not key:
        return None
    if not paper.abstract:
        return None

    topics = "、".join(m.label for m in paper.matches[:3]) or "无"
    prompt = USER_TEMPLATE.format(
        title=paper.title,
        journal=paper.journal,
        year=paper.year or "",
        topics=topics,
        abstract=paper.abstract[:4000],
    )
    body = {
        "model": cfg.llm.model,
        "temperature": cfg.llm.temperature,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    url = cfg.llm.base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg.llm.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        payload = _extract_json(content)
        if not payload:
            return None
        return merge_llm(summary, payload)
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError, OSError):
        return None


def _describe_http_error(exc: Exception) -> str:
    """把 HTTPError 的响应体读出来，否则只有一句 'HTTP Error 401: Unauthorized'。"""
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
                detail = (
                    (payload.get("error") or {}).get("message")
                    if isinstance(payload.get("error"), dict)
                    else payload.get("error") or payload.get("message") or raw
                )
            except json.JSONDecodeError:
                detail = raw
        except Exception:  # noqa: BLE001
            pass
        hints = {
            401: "→ key 无效、被撤销，或前缀不是该服务商要求的格式",
            403: "→ key 没有该模型/该端点的权限，或账号未实名/未充值",
            404: "→ base_url 或路径不对（应形如 https://host/v1，不要带 /chat/completions）",
            429: "→ 触发限流或余额/额度不足",
        }
        hint = hints.get(exc.code, "→ 检查 base_url、model 名称与服务商文档")
        return f"HTTP {exc.code} {exc.reason}｜{str(detail)[:160]} {hint}"
    if isinstance(exc, urllib.error.URLError):
        return f"连接失败：{exc.reason}（DNS / 代理 / TLS 问题）"
    return f"{type(exc).__name__}: {exc}"[:220]


def health_check(cfg: Config) -> tuple[bool, str]:
    """测试模型连通性，供 `litpush doctor` 使用。"""
    key = cfg.llm.api_key()
    if not key:
        return False, (
            f"当前进程读不到环境变量 {cfg.llm.api_key_env}。"
            "若刚设置过，需要重开终端（或注销重新登录）才会生效。"
        )
    body = {
        "model": cfg.llm.model,
        "messages": [{"role": "user", "content": "回复 ok"}],
        "max_tokens": 8,
    }
    url = cfg.llm.base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"].get("content", "")
        shown = str(content).strip() or "（HTTP 200，返回内容为空，接口可用）"
        return True, f"{cfg.llm.base_url} · {cfg.llm.model} · 回复：{shown[:40]}"
    except Exception as exc:  # noqa: BLE001 - 诊断用，返回给用户看
        return False, _describe_http_error(exc)


def list_models(cfg: Config) -> tuple[bool, object]:
    """拉取 /models，用于确认 model 名称是否可用。"""
    key = cfg.llm.api_key()
    if not key:
        return False, f"当前进程读不到环境变量 {cfg.llm.api_key_env}"
    url = cfg.llm.base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("data") if isinstance(data, dict) else data
        return True, [str(item.get("id")) for item in items if isinstance(item, dict)]
    except Exception as exc:  # noqa: BLE001
        return False, _describe_http_error(exc)
