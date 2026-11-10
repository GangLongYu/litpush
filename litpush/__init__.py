"""litpush — 学术文献每日推送工具。

扫描核心期刊与关注学者引用情况，按研究方向打分，生成 Obsidian 中文速读卡片。
"""

__version__ = "1.0.0"

from .config import Config, load  # noqa: F401

__all__ = ["Config", "load", "__version__"]
