"""PyInstaller 入口 —— 启动 mj.clientd sidecar (临时打包用,勿引用)。"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from mj.clientd.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())