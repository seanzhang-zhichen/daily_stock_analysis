# -*- coding: utf-8 -*-
"""Backend package bootstrap.

这个包既会被 ``python backend/...`` 形式直接执行，也会被测试、
Uvicorn、兼容 shim 以包导入方式加载。这里统一把 ``backend/``
目录加入 ``sys.path``，确保历史代码中大量 ``from src...``、
``from api...`` 的绝对导入在不同启动方式下都能解析到真实后端实现。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    # 保持 backend 内部模块优先于同名根目录兼容 shim，避免导入到旧入口。
    sys.path.insert(0, str(BACKEND_ROOT))
