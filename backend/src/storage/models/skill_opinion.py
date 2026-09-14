# -*- coding: utf-8 -*-
"""专家意见（Skill Opinion）及其前向评估结果的数据模型。

记录各分析技能（Skill）对特定股票给出的意见，以及后续对意见准确性的评估结果。
用于技能质量监控、模型效果评估和专家系统优化。

模型关系：
- :class:`SkillOpinionSampleRecord`：专家意见样本（不可变记录）。
- :class:`SkillOpinionOutcomeRecord`：意见的前向评估结果（可更新）。

评估流程：
1. 分析时记录各技能的意见样本
2. 设定评估时间窗口（horizon）和引擎版本
3. 在窗口结束后计算实际走势与意见方向的一致性
4. 更新评估结果（direction_correct、outcome 等）
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from src.storage.base import Base


class SkillOpinionSampleRecord(Base):
    """专家意见样本记录表。

    记录特定分析任务中各技能对某只股票给出的意见。
    该表为不可变记录，一旦写入不再修改。

    属性:
        id: 自增主键。
        analysis_history_id: 关联的分析历史记录 ID。
        stock_code: 股票代码。
        skill_id: 技能标识符（如 ``trend_analysis``、``fundamental_check`` 等）。
        signal: 意见信号（如 ``buy``、``sell``、``hold`` 等）。
        confidence: 置信度（0-1 之间的浮点数）。
        created_at: 记录创建时间。
    """

    __tablename__ = "skill_opinion_samples"

    id = Column(Integer, primary_key=True)
    # 关联的分析历史记录 ID，用于追溯意见产生的上下文
    analysis_history_id = Column(Integer, nullable=False, index=True)
    # 股票代码，支持多市场格式（如 ``600519``、``00700.HK``）
    stock_code = Column(String(16), nullable=False, index=True)
    # 技能标识符，如 ``trend_analysis``、``fundamental_check``、``sentiment_analysis`` 等
    skill_id = Column(String(128), nullable=False, index=True)
    # 意见信号方向：buy / sell / hold / neutral 等
    signal = Column(String(16), nullable=False)
    # 置信度，范围 0.0-1.0，值越高表示模型越确定
    confidence = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    # 唯一约束：同一分析任务中同一技能只能有一条意见
    __table_args__ = (
        UniqueConstraint(
            "analysis_history_id",
            "skill_id",
            name="uix_skill_opinion_sample",
        ),
    )


class SkillOpinionOutcomeRecord(Base):
    """专家意见前向评估结果记录表。

    记录每条专家意见在特定评估窗口和引擎版本下的实际命中情况。
    支持按评估状态、引擎版本等维度进行统计查询。

    属性:
        id: 自增主键。
        sample_id: 关联的专家意见样本 ID。
        horizon: 评估时间窗口（如 ``5d``、``20d``、``60d``）。
        engine_version: 评估引擎版本号。
        eval_status: 评估状态（``pending``、``evaluated``、``expired`` 等）。
        outcome: 评估结果（``hit``、``miss``、``partial`` 等）。
        direction_correct: 方向是否正确（True/False）。
        analysis_date: 分析日期。
        stock_return_pct: 股票在评估窗口内的实际收益率（百分比）。
        created_at: 记录创建时间。
        updated_at: 记录最后更新时间。
    """

    __tablename__ = "skill_opinion_outcomes"

    id = Column(Integer, primary_key=True)
    # 关联的专家意见样本 ID
    sample_id = Column(Integer, nullable=False, index=True)
    # 评估时间窗口，如 "5d"（5天）、"20d"（20天）、"60d"（60天）
    horizon = Column(String(16), nullable=False)
    # 评估引擎版本号，用于追踪不同版本模型的效果差异
    engine_version = Column(String(32), nullable=False)
    # 评估状态：pending（待评估）、evaluated（已评估）、expired（已过期）
    eval_status = Column(
        String(24), nullable=False, default="pending", index=True
    )
    # 评估结果：hit（命中）、miss（未命中）、partial（部分命中）
    outcome = Column(String(16), index=True)
    # 方向是否正确：意见方向与实际走势是否一致
    direction_correct = Column(Boolean)
    # 分析日期，用于按时间维度统计
    analysis_date = Column(Date, index=True)
    # 股票在评估窗口内的实际收益率（百分比），如 5.23 表示上涨 5.23%
    stock_return_pct = Column(Float)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    # 唯一约束：同一样本在同一窗口和版本下只能有一条评估结果
    # 复合索引：用于按引擎版本、评估状态、时间窗口查询性能
    __table_args__ = (
        UniqueConstraint(
            "sample_id",
            "horizon",
            "engine_version",
            name="uix_skill_opinion_outcome",
        ),
        Index(
            "ix_skill_opinion_outcome_performance",
            "engine_version",
            "eval_status",
            "horizon",
        ),
    )
