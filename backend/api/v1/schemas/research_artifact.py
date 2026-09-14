# -*- coding: utf-8 -*-
"""稳定结构化研究产物（Research Artifact）Schema 定义。

本模块定义研究产物的核心数据契约，用于在历史记录、分析报告和分享图片之间
传递结构化的研究结论。包含证据、论点、风险等关键字段，确保不同模块间数据格式一致。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ResearchEvidence(BaseModel):
    """研究证据条目模型。

    该类用于描述支持研究论点的单个证据，包含标题、详情、来源和观察时间。
    每个证据条目都是研究结论的重要组成部分，用于支撑或反驳核心论点。

    Attributes:
        title: 证据标题，简明扼要地描述该证据的核心内容
        detail: 证据详情描述，提供该证据的详细说明和背景信息，可选
        source: 证据来源，如新闻网站、财报、公告、研报等数据来源标识，可选
        observed_at: 观察到该证据的时间（ISO 格式），用于追溯证据时效性，可选
    """

    title: str
    """证据标题，简明扼要地描述该证据的核心内容。"""

    detail: Optional[str] = None
    """证据详情描述，提供该证据的详细说明和背景信息。"""

    source: Optional[str] = None
    """证据来源，如新闻网站、财报等。"""

    observed_at: Optional[str] = None
    """观察到该证据的时间（ISO 格式），用于追溯证据时效性。"""


class ResearchArtifact(BaseModel):
    """结构化研究产物模型。

    该类用于封装对一只股票完整的研究结论，包括核心论点、支持证据、风险因素、
    失效条件和数据来源。作为研究产物的核心数据载体，用于在历史详情、分享图片
    和结构化报告中统一展示研究结论，确保不同模块间数据格式一致。

    Attributes:
        version: 产物版本号，用于兼容性管理和数据迁移，默认 "1.0"
        stock_code: 股票代码，研究标的的唯一标识
        stock_name: 股票名称，便于识别和展示，可选
        generated_at: 生成时间（ISO 格式），记录研究产物的创建时间，可选
        thesis: 核心论点列表，每条论点都是对标的的核心判断或结论
        evidence: 支持论点的证据列表，每个证据都是一个 ResearchEvidence 对象
        risks: 风险因素列表，描述可能影响研究结论的风险点
        invalidation_conditions: 失效条件列表，描述在什么情况下该研究结论不再成立
        data_quality: 数据质量评估字典，如覆盖率、时效性、完整性等指标
        sources: 数据来源列表，记录研究过程中引用的所有数据来源
    """

    version: str = "1.0"
    """产物版本号，用于兼容性管理，默认 "1.0"。"""

    stock_code: str
    """股票代码，研究标的的唯一标识。"""

    stock_name: Optional[str] = None
    """股票名称，便于识别和展示。"""

    generated_at: Optional[str] = None
    """生成时间（ISO 格式），记录研究产物的创建时间。"""

    thesis: List[str] = Field(default_factory=list)
    """核心论点列表，每条论点都是对标的的核心判断或结论。"""

    evidence: List[ResearchEvidence] = Field(default_factory=list)
    """支持论点的证据列表，每个证据都是一个 ResearchEvidence 对象。"""

    risks: List[str] = Field(default_factory=list)
    """风险因素列表，描述可能影响研究结论的风险点。"""

    invalidation_conditions: List[str] = Field(default_factory=list)
    """失效条件列表，描述在什么情况下该研究结论不再成立。"""

    data_quality: Dict[str, Any] = Field(default_factory=dict)
    """数据质量评估字典，如覆盖率、时效性、完整性等指标。"""

    sources: List[str] = Field(default_factory=list)
    """数据来源列表，记录研究过程中引用的所有数据来源。"""
