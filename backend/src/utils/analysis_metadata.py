# -*- coding: utf-8 -*-
"""分析请求共用的元数据常量。"""

from __future__ import annotations


SELECTION_SOURCES: tuple[str, ...] = ("manual", "autocomplete", "import", "image")
SELECTION_SOURCE_PATTERN = "^(" + "|".join(SELECTION_SOURCES) + ")$"
