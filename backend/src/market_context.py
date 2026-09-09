# -*- coding: utf-8 -*-
"""股票市场上下文识别与多市场 LLM 提示词注入。

根据股票代码自动识别所属市场（A 股 / 港股 / 美股），并返回对应的
"角色描述 + 分析准则"片段，避免在 LLM prompt 中把不同市场混为一谈。

设计要点：

- **单一职责**：本模块只负责"代码 → 市场 → 提示词片段"的映射；
- **多市场适配**：A 股的涨跌停、T+1；港股的 T+0、南北向资金；美股的熔断、
  盘前盘后均通过 ``_MARKET_GUIDELINES`` 配置注入；
- **多语言**：所有片段同时提供中文 / 英文两套，由调用方通过 ``lang`` 切换；
- **回退策略**：无法识别市场时按 A 股处理（默认）。

修复 issue：https://github.com/ZhuLinsen/daily_stock_analysis/issues/644
"""

import re
from typing import Optional


def detect_market(stock_code: Optional[str]) -> str:
    """根据股票代码识别所属市场。

    判定规则（按优先级匹配）：

    1. ``HK`` 前缀或 ``.HK`` 后缀 → 港股；
    2. 5 位纯数字 → 港股（A 股是 6 位）；
    3. 1-5 个大写字母（可带 ``.A`` / ``.B`` 后缀） → 美股；
    4. 其余（含 6 位数字 / 无法识别） → 默认 A 股。

    Args:
        stock_code: 股票代码字符串；为空时直接返回 ``cn``。

    Returns:
        str: ``cn`` / ``hk`` / ``us`` 之一。
    """
    if not stock_code:
        return "cn"

    # 统一大小写，方便同时匹配 HK / hk 等大小写变体
    code = stock_code.strip().upper()

    # 港股显式前缀：HK00700 或 00700.HK
    if code.startswith("HK") or code.endswith(".HK"):
        return "hk"
    lower = code.lower()
    if lower.endswith(".hk"):
        return "hk"
    # 5 位纯数字默认港股（A 股是 6 位，避免与 A 股数字冲突）
    if code.isdigit() and len(code) == 5:
        return "hk"

    # 美股代码：1-5 个大写字母，可选 .A/.B 之类的次级后缀
    if re.match(r'^[A-Z]{1,5}(\.[A-Z]{1,2})?$', code):
        return "us"

    # 默认按 A 股处理：6 位数字（600519 / 000001）也走这里
    return "cn"


# -- 市场专属角色描述 --
# 用于在 system prompt 中给 LLM 戴上对应市场的"角色面具"，
# 例："你是一名专业的 A 股投资分析师"。

_MARKET_ROLES = {
    "cn": {
        "zh": " A 股",
        "en": "China A-shares",
    },
    "hk": {
        "zh": "港股",
        "en": "Hong Kong stock",
    },
    "us": {
        "zh": "美股",
        "en": "US stock",
    },
}

# -- 市场专属分析准则 --
# 注入到 user prompt 的尾部，提醒 LLM 注意对应市场的交易制度与监管特征。
# 千万不要修改字符串字面量本身——这些是直接喂给 LLM 的提示词片段。

_MARKET_GUIDELINES = {
    "cn": {
        "zh": (
            "- 本次分析对象为 **A 股**（中国沪深交易所上市股票）。\n"
            "- 请关注 A 股特有的涨跌停机制（±10%/±20%/±30%）、T+1 交易制度及相关政策因素。"
        ),
        "en": (
            "- This analysis covers a **China A-share** (listed on Shanghai/Shenzhen exchanges).\n"
            "- Consider A-share-specific rules: daily price limits (±10%/±20%/±30%), T+1 settlement, and PRC policy factors."
        ),
    },
    "hk": {
        "zh": (
            "- 本次分析对象为 **港股**（香港交易所上市股票）。\n"
            "- 港股无涨跌停限制，支持 T+0 交易，需关注港币汇率、南北向资金流及联交所特有规则。"
        ),
        "en": (
            "- This analysis covers a **Hong Kong stock** (listed on HKEX).\n"
            "- HK stocks have no daily price limits, allow T+0 trading. Consider HKD FX, Southbound/Northbound flows, and HKEX-specific rules."
        ),
    },
    "us": {
        "zh": (
            "- 本次分析对象为 **美股**（美国交易所上市股票）。\n"
            "- 美股无涨跌停限制（但有熔断机制），支持 T+0 交易和盘前盘后交易，需关注美元汇率、美联储政策及 SEC 监管动态。"
        ),
        "en": (
            "- This analysis covers a **US stock** (listed on NYSE/NASDAQ).\n"
            "- US stocks have no daily price limits (but have circuit breakers), allow T+0 and pre/after-market trading. Consider USD FX, Fed policy, and SEC regulations."
        ),
    },
}


def get_market_role(stock_code: Optional[str], lang: str = "zh") -> str:
    """根据股票代码与语言，返回市场专属的"角色描述"片段。

    Args:
        stock_code: 股票代码；用于识别市场。
        lang: 输出语言，``zh`` 或 ``en``；非 ``en`` 全部按中文处理。

    Returns:
        str: 简短的角色文字（如 `` A 股`` / ``US stock``）；
        未识别时回退到 A 股对应文案。
    """
    market = detect_market(stock_code)
    lang_key = "en" if lang == "en" else "zh"
    # 用 .get(..., _MARKET_ROLES["cn"]) 保证即使新出现未知市场也不会 KeyError
    return _MARKET_ROLES.get(market, _MARKET_ROLES["cn"])[lang_key]


def get_market_guidelines(stock_code: Optional[str], lang: str = "zh") -> str:
    """根据股票代码与语言，返回市场专属的"分析准则"长片段。

    Args:
        stock_code: 股票代码；用于识别市场。
        lang: 输出语言，``zh`` 或 ``en``；非 ``en`` 全部按中文处理。

    Returns:
        str: 多行 Markdown 文本，包含对应市场的交易制度提醒。
    """
    market = detect_market(stock_code)
    lang_key = "en" if lang == "en" else "zh"
    return _MARKET_GUIDELINES.get(market, _MARKET_GUIDELINES["cn"])[lang_key]
