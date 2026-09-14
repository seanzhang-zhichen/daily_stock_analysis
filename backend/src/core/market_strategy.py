# -*- coding: utf-8 -*-
"""为 CN/HK/US 每日市场复盘提供策略蓝图（blueprint）。

这些蓝图是市场复盘分析器共用的静态 prompt/报告片段。把各区域的交易逻辑集中
放在这里，让分析器专注于数据采集与 LLM 编排，而不用关心 prompt 措辞。
"""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class StrategyDimension:
    """市场复盘 prompt 使用的单一策略维度。

    ``checkpoints`` 刻意保持简短。它们会被作为具体的复盘锚点注入 prompt，
    因此此处写得太冗长反而会稀释最终的市场复盘指令。
    """

    # 策略维度的名称，如"趋势结构"、"资金情绪"等
    name: str
    # 该维度的分析目标描述，用于指导 LLM 关注重点
    objective: str
    # 该维度下的检查点列表，作为 LLM 复盘时的具体锚点
    checkpoints: List[str]


@dataclass(frozen=True)
class MarketStrategyBlueprint:
    """按区域划分的市场策略蓝图。

    同一个对象既能渲染 prompt 指令，也能渲染回退用的 markdown，这样在策略措辞
    变化时，LLM 路径与非 LLM/模板路径能保持一致。
    """

    # 市场区域代码：cn（A股）、us（美股）、hk（港股）等
    region: str
    # 策略蓝图的标题，用于 prompt 和报告中标识
    title: str
    # 策略定位描述，说明该策略的核心关注点和目标
    positioning: str
    # 策略原则列表，指导 LLM 分析时的基本逻辑和优先级
    principles: List[str]
    # 分析维度列表，每个维度包含具体的检查点
    dimensions: List[StrategyDimension]
    # 行动框架列表，定义不同市场状态下的操作建议
    action_framework: List[str]

    def to_prompt_block(self) -> str:
        """把蓝图渲染为 prompt 指令文本。

        将策略原则、分析维度和行动框架格式化为结构化的 prompt 文本，
        供 LLM 在生成复盘报告时遵循。
        """
        principles_text = "\n".join([f"- {item}" for item in self.principles])
        action_text = "\n".join([f"- {item}" for item in self.action_framework])

        dims = []
        for dim in self.dimensions:
            checkpoints = "\n".join([f"  - {cp}" for cp in dim.checkpoints])
            dims.append(f"- {dim.name}: {dim.objective}\n{checkpoints}")
        dimensions_text = "\n".join(dims)

        return (
            f"## Strategy Blueprint: {self.title}\n"
            f"{self.positioning}\n\n"
            f"### Strategy Principles\n{principles_text}\n\n"
            f"### Analysis Dimensions\n{dimensions_text}\n\n"
            f"### Action Framework\n{action_text}"
        )

    def to_markdown_block(self) -> str:
        """把蓝图渲染为模板回退报告用的 markdown 小节。

        当 LLM 不可用时，将策略维度简化为 markdown 列表，
        作为模板报告的"策略框架"部分。
        """
        dims = "\n".join([f"- **{dim.name}**: {dim.objective}" for dim in self.dimensions])
        section_title = "### VI. Strategy Framework" if self.region == "us" else "### 六、策略框架"
        return f"{section_title}\n{dims}\n"


# A股（中国）市场复盘策略蓝图
# 聚焦指数趋势、资金博弈与板块轮动，形成次日交易计划
CN_BLUEPRINT = MarketStrategyBlueprint(
    region="cn",
    title="A股市场三段式复盘策略",
    positioning="聚焦指数趋势、资金博弈与板块轮动，形成次日交易计划。",
    principles=[
        "先看指数方向，再看量能结构，最后看板块持续性。",
        "结论必须映射到仓位、节奏与风险控制动作。",
        "判断使用当日数据与近3日新闻，不臆测未验证信息。",
    ],
    dimensions=[
        StrategyDimension(
            name="趋势结构",
            objective="判断市场处于上升、震荡还是防守阶段。",
            checkpoints=["上证/深证/创业板是否同向", "放量上涨或缩量下跌是否成立", "关键支撑阻力是否被突破"],
        ),
        StrategyDimension(
            name="资金情绪",
            objective="识别短线风险偏好与情绪温度。",
            checkpoints=["涨跌家数与涨跌停结构", "成交额是否扩张", "高位股是否出现分歧"],
        ),
        StrategyDimension(
            name="主线板块",
            objective="提炼可交易主线与规避方向。",
            checkpoints=["领涨板块是否具备事件催化", "板块内部是否有龙头带动", "领跌板块是否扩散"],
        ),
    ],
    action_framework=[
        "进攻：指数共振上行 + 成交额放大 + 主线强化。",
        "均衡：指数分化或缩量震荡，控制仓位并等待确认。",
        "防守：指数转弱 + 领跌扩散，优先风控与减仓。",
    ],
)

# 美股市场复盘策略蓝图
# 聚焦指数趋势、宏观叙事与板块轮动，定义下一交易时段的风险姿态
US_BLUEPRINT = MarketStrategyBlueprint(
    region="us",
    title="US Market Regime Strategy",
    positioning="Focus on index trend, macro narrative, and sector rotation to define next-session risk posture.",
    principles=[
        "Read market regime from S&P 500, Nasdaq, and Dow alignment first.",
        "Separate beta move from theme-driven alpha rotation.",
        "Translate recap into actionable risk-on/risk-off stance with clear invalidation points.",
    ],
    dimensions=[
        StrategyDimension(
            name="Trend Regime",
            objective="Classify the market as momentum, range, or risk-off.",
            checkpoints=[
                "Are SPX/NDX/DJI directionally aligned",
                "Did volume confirm the move",
                "Are key index levels reclaimed or lost",
            ],
        ),
        StrategyDimension(
            name="Macro & Flows",
            objective="Map policy/rates narrative into equity risk appetite.",
            checkpoints=[
                "Treasury yield and USD implications",
                "Breadth and leadership concentration",
                "Defensive vs growth factor rotation",
            ],
        ),
        StrategyDimension(
            name="Sector Themes",
            objective="Identify persistent leaders and vulnerable laggards.",
            checkpoints=[
                "AI/semiconductor/software trend persistence",
                "Energy/financials sensitivity to macro data",
                "Volatility signals from VIX and large-cap earnings",
            ],
        ),
    ],
    action_framework=[
        "Risk-on: broad index breakout with expanding participation.",
        "Neutral: mixed index signals; focus on selective relative strength.",
        "Risk-off: failed breakouts and rising volatility; prioritize capital preservation.",
    ],
)

# 港股市场复盘策略蓝图
# 聚焦恒生指数趋势、南向资金博弈与板块轮动，形成次日交易计划
HK_BLUEPRINT = MarketStrategyBlueprint(
    region="hk",
    title="港股市场三段式复盘策略",
    positioning="聚焦恒生指数趋势、南向资金博弈与板块轮动，形成次日交易计划。",
    principles=[
        "先看恒指/恒科/国企指数方向，再看南向资金情绪，最后看板块持续性。",
        "结论必须映射到仓位、节奏与风险控制动作。",
        "判断使用当日数据与近3日新闻，不臆测未验证信息。",
    ],
    dimensions=[
        StrategyDimension(
            name="趋势结构",
            objective="判断市场处于上升、震荡还是防守阶段。",
            checkpoints=["恒指/恒科/国企指数是否同向", "放量上涨或缩量下跌是否成立", "关键支撑阻力是否被突破"],
        ),
        StrategyDimension(
            name="资金情绪",
            objective="识别南向资金风险偏好与情绪温度。",
            checkpoints=["南向资金净流入方向与规模", "港元汇率与内地政策含义", "市场广度与龙头集中度"],
        ),
        StrategyDimension(
            name="主线板块",
            objective="提炼可交易主线与规避方向。",
            checkpoints=["科技/互联网平台趋势持续性", "金融/地产对政策转向的敏感度", "防御与成长因子轮动"],
        ),
    ],
    action_framework=[
        "进攻：恒指共振上行 + 南向资金持续流入 + 主线强化。",
        "均衡：指数分化或缩量震荡，控制仓位并等待确认。",
        "防守：指数转弱 + 波动率上升，优先风控与减仓。",
    ],
)


def get_market_strategy_blueprint(region: str) -> MarketStrategyBlueprint:
    """按市场区域返回对应的策略蓝图。

    根据传入的区域代码返回对应的策略蓝图实例。
    未知区域回退到 A 股语义，因为市场复盘的历史默认区域就是 ``cn``。

    Args:
        region: 市场区域代码，支持 "cn"（A股）、"us"（美股）、"hk"（港股）

    Returns:
        对应区域的 MarketStrategyBlueprint 策略蓝图实例
    """
    if region == "us":
        return US_BLUEPRINT
    if region == "hk":
        return HK_BLUEPRINT
    return CN_BLUEPRINT
