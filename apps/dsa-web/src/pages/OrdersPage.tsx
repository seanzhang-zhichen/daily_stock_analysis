import type React from 'react';
import { createPortal } from 'react-dom';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowUpRight,
  Clock,
  CreditCard,
  FileText,
  Loader2,
  Receipt,
  RefreshCw,
  RotateCcw,
  X,
} from 'lucide-react';
import { Button, Card, Loading } from '../components/common';
import { SettingsAlert } from '../components/settings';
import { billingApi, type BillingOrder } from '../api/billing';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { useAuth } from '../hooks';
import { cn } from '../utils/cn';

const formatDate = (value?: string | null): string => {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

const formatAmount = (cents: number, currency = 'CNY'): string => {
  const amount = (cents / 100).toFixed(2);
  return currency === 'CNY' ? `¥${amount}` : `${currency} ${amount}`;
};

const ORDER_STATUS_LABEL: Record<string, string> = {
  created: '待支付',
  pending: '支付中',
  paid: '已支付',
  failed: '支付失败',
  closed: '已关闭',
  refunded: '已退款',
  partial_refunded: '部分退款',
};

const ORDER_STATUS_COLOR: Record<string, string> = {
  created: 'text-amber-300 border-amber-400/40 bg-amber-500/10',
  pending: 'text-blue-300 border-blue-400/40 bg-blue-500/10',
  paid: 'text-cyan border-cyan/40 bg-cyan/10',
  failed: 'text-red-400 border-red-400/40 bg-red-500/10',
  closed: 'text-secondary-text border-border/40 bg-card/40',
  refunded: 'text-purple-300 border-purple-400/40 bg-purple-500/10',
  partial_refunded: 'text-purple-300 border-purple-400/40 bg-purple-500/10',
};

const OrderStatusBadge: React.FC<{ status: string }> = ({ status }) => (
  <span
    className={cn(
      'inline-flex items-center rounded-full border px-2 py-0.5 text-xs',
      ORDER_STATUS_COLOR[status] ?? 'text-secondary-text border-border/40 bg-card/40',
    )}
  >
    {ORDER_STATUS_LABEL[status] ?? status}
  </span>
);

interface RefundDialogState {
  orderNo: string;
  reason: string;
  submitting: boolean;
  error: string | null;
}

const RefundDialog: React.FC<{
  state: RefundDialogState;
  onReasonChange: (v: string) => void;
  onSubmit: () => void;
  onClose: () => void;
}> = ({ state, onReasonChange, onSubmit, onClose }) => {
  const dialog = (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="mx-4 w-full max-w-md rounded-xl border border-border/70 bg-elevated p-6 shadow-2xl animate-in fade-in zoom-in duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-1 text-lg font-medium text-foreground">申请退款</h3>
        <p className="mb-4 text-xs text-secondary-text">订单号：{state.orderNo}</p>
        {state.error && (
          <p className="mb-3 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">{state.error}</p>
        )}
        <label className="block text-sm text-secondary-text mb-1">退款原因 <span className="text-red-400">*</span></label>
        <textarea
          className="w-full rounded-lg border border-border/60 bg-card/80 px-3 py-2 text-sm text-foreground placeholder-secondary-text/50 focus:border-cyan/50 focus:outline-none resize-none"
          rows={3}
          maxLength={255}
          placeholder="请简要说明退款原因…"
          value={state.reason}
          onChange={(e) => onReasonChange(e.target.value)}
          disabled={state.submitting}
        />
        <p className="mt-1 text-xs text-secondary-text">退款申请提交后将由运营审核，审核结果会通过邮件通知。</p>
        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={state.submitting}
            className="rounded-lg border border-border/70 px-4 py-2 text-sm font-medium text-secondary-text transition-colors hover:bg-hover hover:text-foreground disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={onSubmit}
            disabled={state.submitting || state.reason.trim().length === 0}
            className="inline-flex items-center gap-2 rounded-lg bg-red-500/80 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50 shadow-lg shadow-red-500/20"
          >
            {state.submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            提交申请
          </button>
        </div>
      </div>
    </div>
  );
  return createPortal(dialog, document.body);
};

const OrdersPage: React.FC = () => {
  const { userMode } = useAuth();
  const [orders, setOrders] = useState<BillingOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [cancellingNo, setCancellingNo] = useState<string | null>(null);
  const [refundDialog, setRefundDialog] = useState<RefundDialogState | null>(null);
  const [refundSuccessNo, setRefundSuccessNo] = useState<string | null>(null);

  useEffect(() => {
    document.title = '我的订单 - DSA';
  }, []);

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await billingApi.listOrders();
      setOrders(res.orders);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (userMode?.loggedIn) void loadOrders();
  }, [userMode?.loggedIn, loadOrders]);

  const openRefundDialog = (orderNo: string) => {
    setRefundDialog({ orderNo, reason: '', submitting: false, error: null });
    setRefundSuccessNo(null);
  };

  const handleRefundSubmit = async () => {
    if (!refundDialog || refundDialog.reason.trim().length === 0) return;
    setRefundDialog((prev) => prev ? { ...prev, submitting: true, error: null } : null);
    try {
      await billingApi.requestRefund({ orderNo: refundDialog.orderNo, reason: refundDialog.reason.trim() });
      setRefundSuccessNo(refundDialog.orderNo);
      setRefundDialog(null);
    } catch (err) {
      const parsed = getParsedApiError(err);
      setRefundDialog((prev) => prev ? { ...prev, submitting: false, error: parsed.message } : null);
    }
  };

  const handleCancel = async (orderNo: string) => {
    setCancellingNo(orderNo);
    try {
      const res = await billingApi.cancelOrder(orderNo);
      setOrders((prev) => prev.map((o) => (o.orderNo === orderNo ? res.order : o)));
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setCancellingNo(null);
    }
  };

  if (!userMode?.userModeEnabled) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <Card title="我的订单" subtitle="ORDERS">
          <p className="text-sm text-secondary-text">当前实例未启用 To C 多用户模式。</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5 px-4 py-8 lg:py-10">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wider text-secondary-text">ORDERS</p>
          <h1 className="text-2xl font-semibold text-foreground">我的订单</h1>
          <p className="text-sm text-secondary-text">查看历史订单或申请发票。</p>
        </div>
        <Button variant="outline" onClick={loadOrders} disabled={loading}>
          <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} /> 刷新
        </Button>
      </div>

      {error && <SettingsAlert title="加载失败" message={error.message} variant="error" />}

      {loading ? (
        <div className="flex min-h-[20vh] items-center justify-center">
          <Loading />
        </div>
      ) : orders.length === 0 ? (
        <Card title="暂无订单" subtitle="EMPTY">
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <Receipt className="h-10 w-10 text-secondary-text/40" />
            <p className="text-sm text-secondary-text">你还没有任何订单记录。</p>
            <Link to="/billing">
              <Button variant="primary">
                <CreditCard className="h-4 w-4" /> 前往升级
              </Button>
            </Link>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {orders.map((order) => (
            <div
              key={order.orderNo}
              className="rounded-2xl border border-border/60 bg-card/60 p-4 sm:p-5"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <p className="font-mono text-sm text-foreground">{order.orderNo}</p>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-secondary-text">
                    <span>{order.planCode.toUpperCase()}</span>
                    <span>·</span>
                    <span>{formatAmount(order.amountCents, order.currency)}</span>
                    <span>·</span>
                    <span>{order.provider === 'wechat' ? '微信支付' : order.provider === 'alipay' ? '支付宝' : '人工'}</span>
                  </div>
                  <p className="text-xs text-secondary-text">
                    下单: {formatDate(order.createdAt)}
                    {order.paidAt && <span className="ml-2">支付: {formatDate(order.paidAt)}</span>}
                  </p>
                </div>
                <OrderStatusBadge status={order.status} />
              </div>

              {/* 操作按钮 */}
              <div className="mt-3 flex flex-wrap gap-2">
                {order.status === 'paid' && (
                  <>
                    <Link
                      to={`/account/invoices?orderNo=${order.orderNo}`}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-border/50 px-3 py-1.5 text-xs text-secondary-text hover:border-cyan/50 hover:text-cyan transition-colors"
                    >
                      <FileText className="h-3.5 w-3.5" /> 申请发票
                    </Link>
                    {refundSuccessNo === order.orderNo ? (
                      <span className="inline-flex items-center gap-1.5 rounded-lg border border-green-400/30 bg-green-500/10 px-3 py-1.5 text-xs text-green-400">
                        <RotateCcw className="h-3.5 w-3.5" /> 退款已提交
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => openRefundDialog(order.orderNo)}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-border/50 px-3 py-1.5 text-xs text-secondary-text hover:border-red-400/50 hover:text-red-400 transition-colors"
                      >
                        <RotateCcw className="h-3.5 w-3.5" /> 申请退款
                      </button>
                    )}
                  </>
                )}
                {order.status === 'created' && (
                  <>
                    <Link
                      to="/billing"
                      className="inline-flex items-center gap-1.5 rounded-lg border border-cyan/40 bg-cyan/10 px-3 py-1.5 text-xs text-cyan hover:bg-cyan/20 transition-colors"
                    >
                      <CreditCard className="h-3.5 w-3.5" /> 去支付
                    </Link>
                    <button
                      type="button"
                      disabled={cancellingNo === order.orderNo}
                      onClick={() => void handleCancel(order.orderNo)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-border/50 px-3 py-1.5 text-xs text-secondary-text hover:border-red-400/50 hover:text-red-400 transition-colors disabled:opacity-50"
                    >
                      {cancellingNo === order.orderNo ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <X className="h-3.5 w-3.5" />
                      )}
                      取消订单
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {refundDialog && (
        <RefundDialog
          state={refundDialog}
          onReasonChange={(v) => setRefundDialog((prev) => prev ? { ...prev, reason: v } : null)}
          onSubmit={() => void handleRefundSubmit()}
          onClose={() => setRefundDialog(null)}
        />
      )}

      <p className="text-xs text-secondary-text">
        <Clock className="mr-1 inline h-3 w-3" />
        订单在未支付状态下 15 分钟后自动关闭。
        <Link to="/billing" className="ml-2 inline-flex items-center gap-1 text-cyan hover:underline">
          返回会员中心 <ArrowUpRight className="h-3 w-3" />
        </Link>
      </p>
    </div>
  );
};

export default OrdersPage;
