"""项目根目录启动器：python run.py [子命令]

这样即使不设置 PYTHONPATH，也能从任意工作目录启动。
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit(
        "litpush 需要 Python 3.11+（用到标准库 tomllib，3.11 才加入）。\n"
        f"当前解释器：{sys.executable} ({sys.version.split()[0]})\n"
        "Windows 上可以直接用项目自带的 litpush.cmd <命令>（会自动找 3.11+）；"
        "或者用 py -3.12 run.py <命令>。"
    )

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litpush.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
