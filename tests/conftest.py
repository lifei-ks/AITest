"""全局测试夹具：任何 app 模块导入前，把 DATABASE_URL 指向临时库。

pytest 先收集 conftest 再导入测试模块，因此这里设置的环境变量一定生效。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="aicase-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
