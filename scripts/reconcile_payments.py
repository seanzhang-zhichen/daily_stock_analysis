#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/reconcile_payments.py — 每日支付对账 (Phase 5)。

职责:

1. 拉取**昨日通道账单** (微信支付商户平台 / 支付宝商户中心 API)。
2. 与本地 ``app_orders`` 按 ``provider_trade_no`` 关联。
3. 输出三类差异并落 ``app_reconciliation_diffs`` 表 + (可选) 邮件 / Webhook 告警:
   - ``channel_only``: 通道有支付记录但本地无订单 → 可触发自动补单 (待 SDK 接入后启用)。
   - ``local_only``: 本地标记 paid 但通道无对应记录 → 告警, 可能是测试数据 / 伪造回调。
   - ``amount_mismatch``: 通道金额与本地金额不一致 → 告警 + 暂停通道。
4. 当日核对完成后写一条 ``app_reconciliation_reports`` (clean / has_diff / failed)。

骨架阶段:

- 通道账单拉取尚未对接真实 API, ``fetch_channel_settlements`` 默认返回空列表;
  接入微信 / 支付宝 SDK 后在此函数内补真实实现。
- 邮件 / Webhook 告警仅打印日志, 实际告警接入 ``src/users/email.py`` 后改写
  ``_notify_diffs``。
- 默认 dry-run: ``--commit`` 才真正写库。

定时任务示例 (W6 上线后):

    # crontab UTC+8 每日 02:00 跑昨日对账
    0 2 * * * cd /opt/dsa && /opt/dsa/venv/bin/python scripts/reconcile_payments.py --commit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.orm import Session

from src.storage import (
    AppOrder,
    AppReconciliationDiff,
    AppReconciliationReport,
    DatabaseManager,
)

logger = logging.getLogger("reconcile_payments")


# ── 数据结构 ─────────────────────────────────────────────────────────────────

@dataclass
class ChannelSettlement:
    """通道账单行 (规范化后的中间结构)。"""

    provider: str  # wechat / alipay
    provider_trade_no: str
    out_trade_no: str  # 商户订单号 (即 ``app_orders.order_no``)
    amount_cents: int
    status: str  # 通道侧状态 (paid / refunded / closed)
    settled_at: datetime
    raw: dict = field(default_factory=dict)


@dataclass
class ReconcileReport:
    reconcile_date: date
    provider: str
    total_channel: int = 0
    total_local: int = 0
    diff_count: int = 0
    diffs: List[dict] = field(default_factory=list)
    status: str = "clean"  # clean / has_diff / failed
    note: str = ""


# ── 通道账单拉取 (待接入) ────────────────────────────────────────────────────

def fetch_channel_settlements(provider: str, target_date: date) -> List[ChannelSettlement]:
    """从通道侧拉取目标日期的对账单。

    实现策略:

    1. 优先委托给 :mod:`src.services.billing.gateways` 的 gateway 实例
       (:meth:`PaymentGateway.fetch_settlements`); 真实 SDK 接入后只改 gateway
       即可让对账脚本立刻拿到真实账单。
    2. gateway 未配置 (``PAYMENT_ENABLED=false`` 或密钥缺失) 时返回空列表,
       脚本仅做本地 vs 空通道的差异分析 (此时 ``local_only`` 是预期产物)。
    3. gateway ``fetch_settlements`` 抛 :class:`NotImplementedError` 时同样
       返回空列表 + warning 日志, 保持脚本骨架可跑。

    Args:
        provider: ``wechat`` 或 ``alipay``。
        target_date: 对账目标日 (默认昨日)。

    Returns:
        规范化的 :class:`ChannelSettlement` 列表 (可能为空)。
    """
    try:
        from src.services.billing.gateways import get_gateway
    except Exception as exc:  # noqa: BLE001
        logger.warning("import billing.gateways failed: %s", exc)
        return []

    gateway = get_gateway(provider)
    if gateway is None:
        logger.info(
            "fetch_channel_settlements: gateway not configured for %s, returning empty list",
            provider,
        )
        return []

    try:
        rows = gateway.fetch_settlements(target_date)
    except NotImplementedError:
        logger.warning(
            "gateway.fetch_settlements not implemented for %s (will be wired in SDK slice)",
            provider,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        logger.exception("gateway.fetch_settlements failed for %s: %s", provider, exc)
        return []

    # 规范化: gateway 返回的 ChannelSettlement 与脚本内同名 dataclass 字段一致,
    # 这里做一次 to-dict + 重建, 避免 isinstance 检查依赖具体导入路径。
    out: List[ChannelSettlement] = []
    for r in rows:
        out.append(ChannelSettlement(
            provider=r.provider,
            provider_trade_no=r.provider_trade_no,
            out_trade_no=r.out_trade_no,
            amount_cents=r.amount_cents,
            status=r.status,
            settled_at=r.settled_at,
            raw=getattr(r, "raw", {}) or {},
        ))
    return out


# ── 本地订单查询 ────────────────────────────────────────────────────────────

def fetch_local_paid_orders(
    db: Session, provider: str, target_date: date
) -> List[AppOrder]:
    """查询本地在 ``target_date`` 这一天 paid 的订单。

    与 ``ChannelSettlement.settled_at`` 对齐时, 通道账单一般包含 0-24h 内的支付记录。
    """
    start = datetime.combine(target_date, time.min)
    end = datetime.combine(target_date, time.max)
    return (
        db.query(AppOrder)
        .filter(
            AppOrder.provider == provider,
            AppOrder.status.in_(["paid", "refunded", "partial_refunded"]),
            AppOrder.paid_at.isnot(None),
            AppOrder.paid_at >= start,
            AppOrder.paid_at <= end,
        )
        .all()
    )


# ── 差异比对核心逻辑 ────────────────────────────────────────────────────────

def reconcile(
    db: Session,
    provider: str,
    target_date: date,
    commit: bool = False,
) -> ReconcileReport:
    """对账主流程: channel 与 local 双边比对, 输出 :class:`ReconcileReport`。

    Args:
        db: SQLAlchemy Session, 写库时由调用方负责事务提交。
        provider: ``wechat`` / ``alipay``。
        target_date: 对账目标日。
        commit: True 时把差异落 ``app_reconciliation_diffs`` + 汇总落
            ``app_reconciliation_reports``; False (默认) 仅在控制台输出 dry-run。
    """
    report = ReconcileReport(reconcile_date=target_date, provider=provider)

    try:
        channel_rows = fetch_channel_settlements(provider, target_date)
        local_rows = fetch_local_paid_orders(db, provider, target_date)
    except Exception as exc:  # noqa: BLE001
        logger.exception("拉取账单失败: provider=%s date=%s", provider, target_date)
        report.status = "failed"
        report.note = f"fetch error: {exc}"
        if commit:
            _persist_report(db, report)
        return report

    report.total_channel = len(channel_rows)
    report.total_local = len(local_rows)

    # 建立索引: out_trade_no -> row
    channel_by_order: dict[str, ChannelSettlement] = {r.out_trade_no: r for r in channel_rows}
    local_by_order: dict[str, AppOrder] = {r.order_no: r for r in local_rows}

    # 1) channel_only: 通道有 / 本地无
    for order_no, ch in channel_by_order.items():
        if order_no in local_by_order:
            continue
        report.diffs.append(_diff_dict(
            diff_type="channel_only",
            provider=provider,
            order_no=order_no,
            provider_trade_no=ch.provider_trade_no,
            channel_amount_cents=ch.amount_cents,
            channel_status=ch.status,
            local_amount_cents=None,
            local_status=None,
            detail=ch.raw,
        ))

    # 2) local_only: 本地有 / 通道无
    for order_no, lo in local_by_order.items():
        if order_no in channel_by_order:
            continue
        report.diffs.append(_diff_dict(
            diff_type="local_only",
            provider=provider,
            order_no=order_no,
            provider_trade_no=lo.provider_trade_no,
            channel_amount_cents=None,
            channel_status=None,
            local_amount_cents=lo.amount_cents,
            local_status=lo.status,
            detail={"local_paid_at": lo.paid_at.isoformat() if lo.paid_at else None},
        ))

    # 3) amount_mismatch / status_mismatch: 双边都有但不一致
    for order_no, ch in channel_by_order.items():
        lo = local_by_order.get(order_no)
        if lo is None:
            continue
        if ch.amount_cents != lo.amount_cents:
            report.diffs.append(_diff_dict(
                diff_type="amount_mismatch",
                provider=provider,
                order_no=order_no,
                provider_trade_no=ch.provider_trade_no,
                channel_amount_cents=ch.amount_cents,
                channel_status=ch.status,
                local_amount_cents=lo.amount_cents,
                local_status=lo.status,
                detail=ch.raw,
            ))
        # 状态比对 (paid 对 paid, refunded 对 refunded)
        elif _normalize_channel_status(ch.status) != _normalize_local_status(lo.status):
            report.diffs.append(_diff_dict(
                diff_type="status_mismatch",
                provider=provider,
                order_no=order_no,
                provider_trade_no=ch.provider_trade_no,
                channel_amount_cents=ch.amount_cents,
                channel_status=ch.status,
                local_amount_cents=lo.amount_cents,
                local_status=lo.status,
                detail=ch.raw,
            ))

    report.diff_count = len(report.diffs)
    report.status = "has_diff" if report.diff_count > 0 else "clean"

    if commit:
        _persist_diffs(db, report)
        _persist_report(db, report)

    if report.diff_count > 0:
        _notify_diffs(report)

    return report


def _normalize_channel_status(s: str) -> str:
    s = (s or "").lower()
    if s in ("success", "paid", "trade_success"):
        return "paid"
    if s in ("refund", "refunded"):
        return "refunded"
    if s in ("closed", "trade_closed", "cancel"):
        return "closed"
    return s


def _normalize_local_status(s: str) -> str:
    s = (s or "").lower()
    if s == "partial_refunded":
        return "refunded"
    return s


def _diff_dict(**kwargs) -> dict:
    return kwargs


# ── 落库 ────────────────────────────────────────────────────────────────────

def _persist_diffs(db: Session, report: ReconcileReport) -> None:
    """把差异行落 ``app_reconciliation_diffs`` 表 (幂等: 同日同 order_no 同类型只插一次)。"""
    for d in report.diffs:
        existing = (
            db.query(AppReconciliationDiff)
            .filter(
                AppReconciliationDiff.reconcile_date == report.reconcile_date,
                AppReconciliationDiff.provider == report.provider,
                AppReconciliationDiff.diff_type == d["diff_type"],
                AppReconciliationDiff.order_no == d.get("order_no"),
            )
            .first()
        )
        if existing:
            continue
        row = AppReconciliationDiff(
            reconcile_date=report.reconcile_date,
            provider=report.provider,
            diff_type=d["diff_type"],
            order_no=d.get("order_no"),
            provider_trade_no=d.get("provider_trade_no"),
            local_amount_cents=d.get("local_amount_cents"),
            channel_amount_cents=d.get("channel_amount_cents"),
            local_status=d.get("local_status"),
            channel_status=d.get("channel_status"),
            detail=json.dumps(d.get("detail") or {}, ensure_ascii=False),
        )
        db.add(row)
    db.commit()


def _persist_report(db: Session, report: ReconcileReport) -> None:
    """落 ``app_reconciliation_reports`` 汇总行。同日同 provider 已存在时 upsert。"""
    existing = (
        db.query(AppReconciliationReport)
        .filter(
            AppReconciliationReport.reconcile_date == report.reconcile_date,
            AppReconciliationReport.provider == report.provider,
        )
        .first()
    )
    if existing is not None:
        existing.status = report.status
        existing.total_channel = report.total_channel
        existing.total_local = report.total_local
        existing.diff_count = report.diff_count
        existing.note = (report.note or "")[:1000]
        db.add(existing)
    else:
        db.add(AppReconciliationReport(
            reconcile_date=report.reconcile_date,
            provider=report.provider,
            status=report.status,
            total_channel=report.total_channel,
            total_local=report.total_local,
            diff_count=report.diff_count,
            note=(report.note or "")[:1000],
        ))
    db.commit()


# ── 告警（邮件 + Webhook） ────────────────────────────────────────────────────

def _build_alert_text(report: ReconcileReport) -> str:
    lines = [
        f"[对账告警] {report.reconcile_date.isoformat()} provider={report.provider}",
        f"状态: {report.status}  差异: {report.diff_count} 条  "
        f"通道: {report.total_channel}  本地: {report.total_local}",
        "",
    ]
    for d in report.diffs[:20]:
        lines.append(f"  [{d['diff_type']}] order={d.get('order_no')} "
                     f"channel={d.get('channel_amount_cents')} local={d.get('local_amount_cents')}")
    if report.diff_count > 20:
        lines.append(f"  ... 还有 {report.diff_count - 20} 条差异，请查数据库 app_reconciliation_diffs 表")
    if report.note:
        lines.append(f"备注: {report.note}")
    return "\n".join(lines)


def _notify_via_email(report: ReconcileReport, body_text: str) -> None:
    """通过项目邮件后端发送告警到 ADMIN_ALERT_EMAIL。"""
    alert_email = (os.getenv("ADMIN_ALERT_EMAIL") or "").strip()
    if not alert_email:
        logger.info("ADMIN_ALERT_EMAIL 未配置, 跳过邮件告警")
        return
    try:
        from src.users.email import EmailMessageDTO, get_email_backend
        backend = get_email_backend()
        backend.send(EmailMessageDTO(
            to=alert_email,
            subject=f"[对账告警] {report.reconcile_date.isoformat()} {report.provider} diff={report.diff_count}",
            body_text=body_text,
        ))
        logger.info("对账告警邮件已发送到 %s", alert_email)
    except Exception:  # noqa: BLE001
        logger.warning("对账告警邮件发送失败", exc_info=True)


def _notify_via_webhook(report: ReconcileReport, body_text: str) -> None:
    """向 RECONCILE_WEBHOOK_URL 发送 JSON 告警（兼容飞书/企业微信/通用 Webhook）。"""
    webhook_url = (os.getenv("RECONCILE_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        return
    payload = json.dumps({
        "msg_type": "text",
        "content": {"text": body_text},
    }, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("对账 Webhook 告警发送成功 status=%s", resp.status)
    except Exception:  # noqa: BLE001
        logger.warning("对账 Webhook 告警发送失败 url=%s", webhook_url, exc_info=True)


def _notify_diffs(report: ReconcileReport) -> None:
    """差异告警出口：控制台日志 + 邮件（ADMIN_ALERT_EMAIL）+ Webhook（RECONCILE_WEBHOOK_URL）。"""
    logger.warning(
        "[reconcile] %s %s: diff=%d (channel=%d, local=%d) status=%s",
        report.reconcile_date.isoformat(),
        report.provider,
        report.diff_count,
        report.total_channel,
        report.total_local,
        report.status,
    )
    for d in report.diffs[:20]:  # 控制台限制 20 条, 余下查表
        logger.warning("  - %s", json.dumps(d, ensure_ascii=False))

    body_text = _build_alert_text(report)
    _notify_via_email(report, body_text)
    _notify_via_webhook(report, body_text)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Daily payment reconciliation.")
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=date.today() - timedelta(days=1),
        help="对账目标日, 格式 YYYY-MM-DD, 默认为昨日。",
    )
    parser.add_argument(
        "--provider",
        choices=("wechat", "alipay", "all"),
        default="all",
        help="对账通道, 默认 all 跑两条。",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="实际写库 + 告警; 不加该 flag 时仅 dry-run。",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG 日志。",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    providers = ("wechat", "alipay") if args.provider == "all" else (args.provider,)

    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    exit_code = 0
    try:
        for provider in providers:
            report = reconcile(session, provider, args.date, commit=args.commit)
            mode = "commit" if args.commit else "dry-run"
            print(
                f"[{mode}] reconcile {args.date.isoformat()} provider={provider}: "
                f"status={report.status} channel={report.total_channel} "
                f"local={report.total_local} diffs={report.diff_count}"
            )
            if report.status == "failed":
                exit_code = 1
            elif report.status == "has_diff":
                exit_code = exit_code or 2
    finally:
        session.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
