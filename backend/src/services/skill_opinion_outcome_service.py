"""评估本地持久化日K线中待处理的专业意见。

本模块提供对 SkillOpinion（专家意见/信号）的回测评估能力，
通过读取本地数据库中的日K线数据，计算不同时间 horizon 下的
收益表现，从而验证专家意见的有效性。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from src.storage import (
    DatabaseManager,
    SkillOpinionOutcomeRecord,
    SkillOpinionSampleRecord,
    StockDaily,
)
from src.services.skill_opinion_weight_service import ENGINE_VERSION

# 评估时间窗口映射：键为可读标识，值为对应交易日天数
# 例如 "5d" 表示评估信号发出后 5 个交易日的表现
HORIZONS = {"1d": 1, "3d": 3, "5d": 5, "10d": 10}


class SkillOpinionOutcomeService:
    """尽力而为的本地评估器；不抓取实时行情，也不会阻塞分析流程。

    设计哲学：
    - 纯本地计算，依赖已持久化的日K线数据，避免网络 I/O 阻塞
    - 幂等执行：重复运行不会重复创建记录，只会更新未完成的评估
    - 容错处理：数据不足时标记为 pending，而非抛错中断
    """

    def __init__(self, db_manager=None):
        # 允许注入数据库管理器实例，便于测试和依赖注入；
        # 未提供时从单例获取默认实例
        self.db = db_manager or DatabaseManager.get_instance()

    def evaluate_pending(self, *, limit: int = 200) -> dict[str, int]:
        """评估缺失的 sample/horizon 组合，使用本地存储的日K线数据。

        处理流程：
        1. 从 SkillOpinionSampleRecord 按 ID 顺序取前 limit 条样本
        2. 对每个样本的每个 horizon，检查是否已有评估记录
        3. 若无或状态为 pending，则查询对应股票在信号日后的日K线
        4. 调用 _evaluate 计算收益与判断正误
        5. 写入或更新 SkillOpinionOutcomeRecord

        Args:
            limit: 每次处理的最大样本数，范围 [1, 500]，默认 200。

        Returns:
            统计字典，包含 created（新建记录数）、evaluated（完成评估数）、
            pending（数据不足暂挂数）。
        """
        # 对 limit 做边界保护，防止非法值导致查询异常
        limit = max(1, min(int(limit), 500))
        completed = pending = created = 0
        with self.db.get_session() as session:
            # 按 ID 升序获取样本，保证评估顺序稳定且可预期
            samples = session.execute(
                select(SkillOpinionSampleRecord).order_by(SkillOpinionSampleRecord.id).limit(limit)
            ).scalars().all()
            for sample in samples:
                # 对每个样本遍历所有预定义的时间窗口
                for horizon, days in HORIZONS.items():
                    # 查询该样本在该 horizon 下是否已有评估记录
                    existing = session.execute(select(SkillOpinionOutcomeRecord).where(
                        SkillOpinionOutcomeRecord.sample_id == sample.id,
                        SkillOpinionOutcomeRecord.horizon == horizon,
                        SkillOpinionOutcomeRecord.engine_version == ENGINE_VERSION,
                    )).scalar_one_or_none()
                    # 若已有非 pending 状态的记录，则跳过，避免重复评估
                    if existing is not None and existing.eval_status != "pending":
                        continue
                    # 查询该股票从信号创建日开始的日K线，取前 days+1 条
                    # days+1 是因为需要包含信号日当天（起点）和第 days 天（终点）
                    bars = session.execute(select(StockDaily).where(
                        StockDaily.code == sample.stock_code,
                        StockDaily.date >= sample.created_at.date(),
                    ).order_by(StockDaily.date).limit(days + 1)).scalars().all()
                    # 执行核心评估逻辑：计算收益并判断方向正确性
                    status, outcome, correct, stock_return = self._evaluate(sample.signal, bars, days)
                    # 统计状态分布
                    if status == "pending":
                        pending += 1
                    else:
                        completed += 1
                    # 若记录不存在则新建，确保每个 sample/horizon 组合都有对应记录
                    if existing is None:
                        existing = SkillOpinionOutcomeRecord(
                            sample_id=sample.id, horizon=horizon, engine_version=ENGINE_VERSION,
                        )
                        session.add(existing)
                        created += 1
                    # 更新评估结果到记录中
                    existing.eval_status = status
                    existing.outcome = outcome
                    existing.direction_correct = correct
                    existing.analysis_date = sample.created_at.date()
                    existing.stock_return_pct = stock_return
                    existing.updated_at = datetime.now()
            # 一次性提交事务，保证数据一致性
            session.commit()
        return {"created": created, "evaluated": completed, "pending": pending}

    @staticmethod
    def _evaluate(signal, bars, days):
        """核心评估逻辑：计算给定时间窗口内的股票收益并判断信号方向正确性。

        算法说明：
        - 需要至少 days+1 根K线（包含起点和终点）
        - 收益率 = (终点收盘价 - 起点收盘价) / 起点收盘价 * 100%
        - 对于 buy/strong_buy 信号，收益率为正则方向正确
        - 对于 sell/strong_sell 信号，收益率为负则方向正确（取反后判断）
        - hold 信号不参与方向判断，仅记录观察性收益

        Args:
            signal: 信号类型，如 "buy", "strong_buy", "sell", "strong_sell", "hold"
            bars: 日K线数据列表，按日期升序排列
            days: 评估时间窗口的交易日天数

        Returns:
            四元组：(status, outcome, correct, stock_return)
            - status: "pending"（数据不足）/ "observational"（hold信号）/ "evaluated"（正常评估）
            - outcome: "hit"（方向正确）/ "miss"（方向错误）/ "observational"（hold）/ None（pending）
            - correct: True/False/None，表示方向是否正确
            - stock_return: 收益率百分比，None 表示无法计算
        """
        # 数据完整性校验：需要足够的K线数据且首尾收盘价有效
        if len(bars) < days + 1 or not bars or not bars[0].close or not bars[-1].close:
            return "pending", None, None, None
        # 提取起点（信号日）和终点（days 个交易日后）的收盘价
        start, end = float(bars[0].close), float(bars[days].close)
        # 起点价格必须为正，否则无法计算收益率
        if start <= 0:
            return "pending", None, None, None
        # 计算百分比收益率
        stock_return = (end - start) / start * 100.0
        # hold 信号不参与方向判断，仅作为观察性数据记录
        if signal == "hold":
            return "observational", "observational", None, stock_return
        # 根据信号方向计算方向性收益：
        # - 买入信号：希望价格上涨，directional = stock_return
        # - 卖出信号：希望价格下跌，directional = -stock_return
        directional = stock_return if signal in {"strong_buy", "buy"} else -stock_return
        # 方向性收益大于 0 表示方向判断正确
        correct = directional > 0
        return "evaluated", "hit" if correct else "miss", correct, stock_return
