#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed sample research reports into the configured database.

Usage:

    python scripts/seed_research_reports.py --dry-run
    python scripts/seed_research_reports.py --count 6
    python scripts/seed_research_reports.py --author-email operator@example.com --replace
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import backend  # noqa: E402,F401

from src.services.research_report_service import _tags_to_json  # noqa: E402
from src.storage import AppResearchReport, AppUser, DatabaseManager  # noqa: E402
from src.users.passwords import hash_password  # noqa: E402


DEFAULT_AUTHOR_EMAIL = "research.seed@example.com"


@dataclass(frozen=True)
class ResearchReportSeed:
    title: str
    summary: str
    preview_content: str
    full_content: str
    category: str
    tags: list[str]
    price_credits: int
    cover_image_url: str | None = None
    is_published: bool = True


SAMPLE_REPORTS: tuple[ResearchReportSeed, ...] = (
    ResearchReportSeed(
        title="AI 算力链月度跟踪：供给约束与订单兑现",
        summary="围绕光模块、服务器、液冷和国产算力集群，拆解订单确定性、估值位置与主要风险。",
        preview_content=(
            "本期样本显示，算力链的短期分歧主要来自交付节奏，而不是终端需求塌缩。"
            "试读部分重点关注订单可见度、库存水位和毛利率变化。"
        ),
        full_content=(
            "# AI 算力链月度跟踪\n\n"
            "## 核心观点\n"
            "1. 海外云厂商资本开支仍处上修通道，国内算力集群建设从主题交易进入订单验证阶段。\n"
            "2. 光模块和液冷环节更容易体现供给约束，服务器整机环节更依赖客户结构和交付能力。\n"
            "3. 估值已经反映较高增长预期，后续重点跟踪订单取消、价格回落和汇率波动。\n\n"
            "## 观察指标\n"
            "- 800G/1.6T 光模块出货节奏\n"
            "- 头部云厂商季度资本开支指引\n"
            "- 液冷项目招标与交付验收节奏\n\n"
            "## 风险提示\n"
            "技术路线变化、客户集中度过高、上游关键器件供应扰动。"
        ),
        category="行业",
        tags=["AI", "算力", "光模块", "液冷"],
        price_credits=30,
        cover_image_url="https://images.unsplash.com/photo-1518770660439-4636190af475",
    ),
    ResearchReportSeed(
        title="创新药出海：BD 交易后的兑现路径",
        summary="梳理近期创新药授权交易后的临床、里程碑付款和商业化兑现节奏。",
        preview_content=(
            "创新药出海不再只看公告金额，核心差异在于首付款占比、适应症竞争格局和后续临床推进。"
        ),
        full_content=(
            "# 创新药出海专题\n\n"
            "## 核心观点\n"
            "BD 交易能改善现金流，但不能替代临床数据本身。首付款比例、合作方销售能力、"
            "适应症空间和专利期限共同决定交易质量。\n\n"
            "## 重点跟踪\n"
            "- Phase II/III 数据读出时间\n"
            "- 海外监管沟通进度\n"
            "- 同靶点竞品安全性和疗效差异\n\n"
            "## 投资含义\n"
            "优先关注拥有差异化数据、现金流压力缓解且后续里程碑节点清晰的公司。"
        ),
        category="医药",
        tags=["创新药", "出海", "BD"],
        price_credits=20,
        cover_image_url="https://images.unsplash.com/photo-1582719471384-894fbb16e074",
    ),
    ResearchReportSeed(
        title="红利资产周报：利率下行环境中的现金流质量",
        summary="从股息率、自由现金流、负债率和分红稳定性四个维度筛选红利资产。",
        preview_content=(
            "本周红利资产的定价逻辑仍围绕无风险利率下行，但高股息并不等同于高质量现金流。"
        ),
        full_content=(
            "# 红利资产周报\n\n"
            "## 核心观点\n"
            "利率下行提升红利资产相对吸引力，但需要区分周期性高分红和可持续高分红。"
            "自由现金流覆盖率、资本开支强度和分红政策稳定性是更关键的判断变量。\n\n"
            "## 筛选框架\n"
            "- 股息率处于历史分位较高区间\n"
            "- 自由现金流连续覆盖现金分红\n"
            "- 资产负债率未显著恶化\n"
            "- 管理层分红政策具备连续性\n\n"
            "## 风险提示\n"
            "盈利周期下行、监管价格调整、一次性高分红不可持续。"
        ),
        category="策略",
        tags=["红利", "现金流", "策略"],
        price_credits=0,
        cover_image_url="https://images.unsplash.com/photo-1554224155-6726b3ff858f",
    ),
    ResearchReportSeed(
        title="半导体设备：国产替代进入验证深水区",
        summary="跟踪刻蚀、薄膜沉积、量测和清洗设备在先进制程与成熟制程中的订单变化。",
        preview_content=(
            "设备板块的关键变量从能否进入产线，逐步切换到良率、稼动率和重复订单能力。"
        ),
        full_content=(
            "# 半导体设备跟踪\n\n"
            "## 核心观点\n"
            "国产替代进入深水区后，订单质量比订单数量更重要。能够在关键客户处获得重复订单的设备商，"
            "更可能穿越单一产线扩产周期。\n\n"
            "## 重点方向\n"
            "- 刻蚀和薄膜沉积设备的工艺覆盖范围\n"
            "- 量测设备在良率提升中的渗透率\n"
            "- 成熟制程扩产节奏与设备国产化率\n\n"
            "## 风险提示\n"
            "晶圆厂资本开支放缓、认证周期拉长、核心零部件供应波动。"
        ),
        category="行业",
        tags=["半导体", "设备", "国产替代"],
        price_credits=25,
        cover_image_url="https://images.unsplash.com/photo-1562408590-e32931084e23",
    ),
    ResearchReportSeed(
        title="港股互联网：回购、利润率与收入弹性的再平衡",
        summary="分析港股互联网龙头在降本增效后，收入恢复、股东回报和估值修复的边际变化。",
        preview_content=(
            "港股互联网公司的估值修复正在从成本收缩驱动，转向收入弹性和股东回报共同驱动。"
        ),
        full_content=(
            "# 港股互联网专题\n\n"
            "## 核心观点\n"
            "降本增效带来的利润率改善已经被市场部分定价，后续更重要的是广告、电商、本地生活和云业务"
            "能否形成收入端弹性。持续回购提升每股价值，但不能替代主营增长。\n\n"
            "## 跟踪指标\n"
            "- 广告加载率和商家投放意愿\n"
            "- 电商 GMV 与变现率\n"
            "- 回购金额占自由现金流比例\n\n"
            "## 风险提示\n"
            "消费恢复弱于预期、平台监管变化、汇率和流动性扰动。"
        ),
        category="港股",
        tags=["港股", "互联网", "回购"],
        price_credits=15,
        cover_image_url="https://images.unsplash.com/photo-1520607162513-77705c0f0d4a",
    ),
    ResearchReportSeed(
        title="新能源车供应链草稿：价格战后的结构分化",
        summary="草稿样例，用于测试运营工作台未发布研报列表。",
        preview_content="草稿试读：价格战对整车、零部件和电池材料的利润传导存在明显差异。",
        full_content=(
            "# 新能源车供应链草稿\n\n"
            "该报告默认未发布，用于测试运营端草稿列表、编辑和发布流程。"
        ),
        category="汽车",
        tags=["新能源车", "供应链", "草稿"],
        price_credits=10,
        cover_image_url="https://images.unsplash.com/photo-1593941707882-a5bba14938c7",
        is_published=False,
    ),
)


def ensure_seed_author(session, email: str) -> AppUser:
    normalized_email = email.strip().lower()
    user = session.query(AppUser).filter(AppUser.email == normalized_email).first()
    if user is not None:
        if not bool(getattr(user, "is_research_operator", False)):
            user.is_research_operator = True
            session.add(user)
            session.flush()
        return user

    user = AppUser(
        email=normalized_email,
        password_hash=hash_password(f"seed-only-{datetime.now().timestamp()}"),
        status="active",
        plan_code="free",
        is_research_operator=True,
    )
    session.add(user)
    session.flush()
    return user


def _selected_reports(count: int) -> Iterable[ResearchReportSeed]:
    if count < 1:
        return ()
    return SAMPLE_REPORTS[: min(count, len(SAMPLE_REPORTS))]


def seed_research_reports(
    session,
    *,
    author_email: str = DEFAULT_AUTHOR_EMAIL,
    count: int = len(SAMPLE_REPORTS),
    replace: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    author = ensure_seed_author(session, author_email)
    now = datetime.now()
    created = 0
    updated = 0
    skipped = 0

    for index, sample in enumerate(_selected_reports(count)):
        report = (
            session.query(AppResearchReport)
            .filter(AppResearchReport.title == sample.title)
            .first()
        )
        if report is not None and not replace:
            skipped += 1
            continue
        if report is None:
            report = AppResearchReport(title=sample.title)
            created += 1
        else:
            updated += 1

        report.summary = sample.summary
        report.preview_content = sample.preview_content
        report.full_content = sample.full_content
        report.category = sample.category
        report.tags = _tags_to_json(sample.tags)
        report.cover_image_url = sample.cover_image_url
        report.price_credits = max(0, int(sample.price_credits))
        report.is_published = bool(sample.is_published)
        report.author_id = int(author.id)
        if sample.is_published:
            report.published_at = now - timedelta(days=index)
        else:
            report.published_at = None
        session.add(report)

    if dry_run:
        session.rollback()
    else:
        session.commit()
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "requested": min(max(count, 0), len(SAMPLE_REPORTS)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed sample research reports into the configured database.")
    parser.add_argument("--author-email", default=DEFAULT_AUTHOR_EMAIL, help="Seed author email.")
    parser.add_argument("--count", type=int, default=len(SAMPLE_REPORTS), help=f"Number of reports to seed, max {len(SAMPLE_REPORTS)}.")
    parser.add_argument("--replace", action="store_true", help="Update existing reports with the same title.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes and roll them back.")
    args = parser.parse_args()

    if args.count < 1:
        print("error: --count must be greater than 0", file=sys.stderr)
        return 2

    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        result = seed_research_reports(
            session,
            author_email=args.author_email,
            count=args.count,
            replace=args.replace,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()

    suffix = " (dry-run, rolled back)" if args.dry_run else ""
    print(
        "ok: research report seed complete"
        f"{suffix}: requested={result['requested']}, created={result['created']}, "
        f"updated={result['updated']}, skipped={result['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
