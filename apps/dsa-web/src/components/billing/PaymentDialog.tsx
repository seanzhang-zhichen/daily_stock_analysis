import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, Loader2, RefreshCw, X, AlertTriangle, FlaskConical } from 'lucide-react';
import { Button } from '../common';
import { SettingsAlert } from '../settings';
import { billingApi, type BillingOrder, type BillingPlan } from '../../api/billing';
import { getParsedApiError, isParsedApiError, type ParsedApiError } from '../../api/error';

type Provider = 'wechat' | 'alipay';
type Phase = 'select_provider' | 'creating_order' | 'pending_pay' | 'paid' | 'cancelled' | 'error';

interface PaymentDialogProps {
  open: boolean;
  plan: BillingPlan;
  onClose: () => void;
  onPaid: () => void;
}

const PROVIDER_LABEL: Record<Provider, string> = {
  wechat: '微信支付',
  alipay: '支付宝',
};

const POLL_INTERVAL_MS = 2000;
// 防止永远轮询: 订单超时上限 (与后端默认 expires_at = 15min 对齐)
const MAX_POLL_DURATION_MS = 15 * 60 * 1000;

const formatPrice = (cents: number, currency: string): string => {
  if (cents <= 0) return '联系客服';
  const amount = (cents / 100).toFixed(2).replace(/\.00$/, '');
  return currency.toUpperCase() === 'CNY' ? `¥${amount}` : `${currency} ${amount}`;
};

/**
 * 支付二维码弹窗 (Phase 5)。
 *
 * 流程: 选择支付通道 → 创建订单 → 拉起支付 → 显示二维码 + 轮询 → paid。
 * 在 ``PAYMENT_MOCK_ENABLED=true`` 时, ``/pay`` 返回 mock code_url; 用户可点
 * "模拟支付成功" 走通整条 UX, 无需真实通道接入。
 */
const PaymentDialog: React.FC<PaymentDialogProps> = ({ open, plan, onClose, onPaid }) => {
  const [phase, setPhase] = useState<Phase>('select_provider');
  const [provider, setProvider] = useState<Provider>('wechat');
  const [order, setOrder] = useState<BillingOrder | null>(null);
  const [codeUrl, setCodeUrl] = useState<string | null>(null);
  const [isMock, setIsMock] = useState(false);
  const [error, setError] = useState<ParsedApiError | string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [secondsElapsed, setSecondsElapsed] = useState(0);

  const pollTimer = useRef<number | null>(null);
  const tickTimer = useRef<number | null>(null);
  const pollStartedAt = useRef<number | null>(null);

  useEffect(() => {
    if (!open) {
      // Reset state on close so reopening starts fresh.
      setPhase('select_provider');
      setOrder(null);
      setCodeUrl(null);
      setIsMock(false);
      setError(null);
      setIsSubmitting(false);
      setSecondsElapsed(0);
      stopPolling();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => stopPolling();
  }, []);

  function stopPolling() {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
    if (tickTimer.current !== null) {
      window.clearInterval(tickTimer.current);
      tickTimer.current = null;
    }
    pollStartedAt.current = null;
  }

  const handleStartPay = async () => {
    setError(null);
    setIsSubmitting(true);
    setPhase('creating_order');
    try {
      const { order: createdOrder } = await billingApi.createOrder({
        planCode: plan.code,
        provider,
      });
      setOrder(createdOrder);

      try {
        const payRes = await billingApi.payOrder(createdOrder.orderNo);
        setCodeUrl(payRes.codeUrl);
        setIsMock(Boolean(payRes.mock));
        setPhase('pending_pay');
        startPolling(createdOrder.orderNo);
      } catch (err) {
        const parsed = getParsedApiError(err);
        if (parsed.status === 501 || parsed.status === 503) {
          setError(parsed);
          setPhase('error');
        } else {
          setError(parsed);
          setPhase('error');
        }
      }
    } catch (err) {
      setError(getParsedApiError(err));
      setPhase('error');
    } finally {
      setIsSubmitting(false);
    }
  };

  const startPolling = (orderNo: string) => {
    stopPolling();
    pollStartedAt.current = Date.now();
    setSecondsElapsed(0);
    tickTimer.current = window.setInterval(() => {
      const startedAt = pollStartedAt.current;
      if (startedAt === null) return;
      setSecondsElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);

    const tick = async () => {
      const startedAt = pollStartedAt.current;
      if (startedAt === null) return;
      if (Date.now() - startedAt > MAX_POLL_DURATION_MS) {
        stopPolling();
        setError('订单已超时, 请关闭窗口后重新下单。');
        setPhase('error');
        return;
      }
      try {
        const { order: latest } = await billingApi.getOrder(orderNo);
        setOrder(latest);
        if (latest.status === 'paid') {
          stopPolling();
          setPhase('paid');
          onPaid();
          return;
        }
        if (latest.status === 'failed' || latest.status === 'closed') {
          stopPolling();
          setPhase('cancelled');
          return;
        }
      } catch (err) {
        // 轮询期间的瞬时错误不阻塞, 仅记一次告警
        console.warn('payment polling failed', err);
      }
      pollTimer.current = window.setTimeout(tick, POLL_INTERVAL_MS);
    };

    pollTimer.current = window.setTimeout(tick, POLL_INTERVAL_MS);
  };

  const handleMockPay = async () => {
    if (!order) return;
    setIsSubmitting(true);
    try {
      const orderNo = order.orderNo;
      await billingApi.mockPayOrder(orderNo);
      // Polling 会自动捕获状态变化; 这里也立即拉一次。
      const { order: latest } = await billingApi.getOrder(orderNo);
      setOrder(latest);
      if (latest.status === 'paid') {
        stopPolling();
        setPhase('paid');
        onPaid();
      }
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancelOrder = async () => {
    if (!order) {
      onClose();
      return;
    }
    setIsSubmitting(true);
    try {
      const orderNo = order.orderNo;
      await billingApi.cancelOrder(orderNo);
    } catch {
      // 即使取消失败也允许关闭弹窗, 后端会通过 15min 超时关单
    } finally {
      stopPolling();
      setIsSubmitting(false);
      onClose();
    }
  };

  const qrImage = useMemo(() => {
    if (!codeUrl) return null;
    // 使用 api.qrserver.com 免费 QR 服务渲染二维码, 不引入新依赖。
    // 生产环境接入真实通道后, 应改为通道返回的 image URL 或本地 SVG 渲染。
    return `https://api.qrserver.com/v1/create-qr-code/?size=220x220&margin=8&data=${encodeURIComponent(codeUrl)}`;
  }, [codeUrl]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="w-full max-w-md rounded-2xl border border-border/60 bg-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-border/60 px-5 py-3">
          <h3 className="text-base font-semibold text-foreground">升级到 {plan.name}</h3>
          <button
            type="button"
            onClick={handleCancelOrder}
            className="rounded p-1 text-secondary-text hover:bg-white/5 hover:text-foreground"
            aria-label="关闭"
            disabled={isSubmitting && phase === 'creating_order'}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 px-5 py-5">
          <div className="flex items-baseline justify-between rounded-xl border border-border/60 bg-base/50 px-4 py-3">
            <div>
              <p className="text-xs uppercase tracking-wider text-secondary-text">套餐</p>
              <p className="mt-0.5 text-sm font-semibold text-foreground">{plan.name}</p>
            </div>
            <div className="text-right">
              <p className="text-xs uppercase tracking-wider text-secondary-text">应付金额</p>
              <p className="mt-0.5 text-lg font-semibold text-foreground">
                {formatPrice(plan.priceCents, plan.currency)}
              </p>
            </div>
          </div>

          {phase === 'select_provider' && (
            <>
              <p className="text-sm text-secondary-text">请选择支付方式</p>
              <div className="grid grid-cols-2 gap-3">
                {(['wechat', 'alipay'] as Provider[]).map((p) => {
                  const active = provider === p;
                  return (
                    <button
                      key={p}
                      type="button"
                      onClick={() => setProvider(p)}
                      className={
                        'rounded-xl border px-3 py-3 text-sm transition-colors ' +
                        (active
                          ? 'border-cyan/40 bg-cyan/10 text-cyan'
                          : 'border-border/60 bg-base/40 text-foreground/80 hover:border-cyan/30')
                      }
                    >
                      {PROVIDER_LABEL[p]}
                    </button>
                  );
                })}
              </div>
              <Button
                type="button"
                variant="primary"
                onClick={handleStartPay}
                disabled={isSubmitting}
                className="w-full"
                isLoading={isSubmitting}
              >
                创建订单并发起支付
              </Button>
              {error && (
                <SettingsAlert
                  title="下单失败"
                  message={isParsedApiError(error) ? error.message : error}
                  variant="error"
                />
              )}
            </>
          )}

          {phase === 'creating_order' && (
            <div className="flex flex-col items-center gap-3 py-8 text-sm text-secondary-text">
              <Loader2 className="h-6 w-6 animate-spin text-cyan" />
              <p>正在创建订单 / 拉起支付…</p>
            </div>
          )}

          {phase === 'pending_pay' && (
            <>
              {isMock && (
                <div className="flex items-start gap-2 rounded-xl border border-amber-400/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  <FlaskConical className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>
                    Mock 模式: 当前未接入真实支付通道, 二维码内容仅用于演示。
                    点击下方 "模拟支付成功" 可走通整条 UX。
                  </span>
                </div>
              )}
              <div className="flex flex-col items-center gap-3">
                {qrImage ? (
                  <img
                    src={qrImage}
                    alt="支付二维码"
                    className="h-56 w-56 rounded-xl border border-border/60 bg-white p-2 shadow-inner"
                  />
                ) : (
                  <div className="flex h-56 w-56 items-center justify-center rounded-xl border border-border/60 bg-base/30 text-xs text-secondary-text">
                    二维码加载失败
                  </div>
                )}
                <p className="text-sm text-foreground/80">
                  请使用 {PROVIDER_LABEL[provider]} 扫描二维码完成付款
                </p>
                <p className="text-xs text-secondary-text">
                  已等待 {secondsElapsed} 秒 · 系统每 {POLL_INTERVAL_MS / 1000} 秒自动检查支付状态
                </p>
                {codeUrl && (
                  <p className="break-all rounded-lg border border-border/60 bg-base/40 px-3 py-2 text-[10px] text-secondary-text">
                    {codeUrl}
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={handleCancelOrder}
                  disabled={isSubmitting}
                >
                  取消支付
                </Button>
                {isMock && (
                  <Button
                    type="button"
                    variant="primary"
                    onClick={handleMockPay}
                    isLoading={isSubmitting}
                    disabled={isSubmitting}
                  >
                    <CheckCircle2 className="h-4 w-4" /> 模拟支付成功
                  </Button>
                )}
              </div>
            </>
          )}

          {phase === 'paid' && (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <CheckCircle2 className="h-10 w-10 text-emerald-400" />
              <p className="text-base font-medium text-foreground">支付成功 🎉</p>
              <p className="text-sm text-secondary-text">已为你开通 {plan.name}, 即将刷新订阅状态。</p>
              <Button type="button" variant="primary" onClick={onClose} className="mt-2">
                完成
              </Button>
            </div>
          )}

          {phase === 'cancelled' && (
            <div className="flex flex-col items-center gap-3 py-6 text-center">
              <RefreshCw className="h-8 w-8 text-secondary-text" />
              <p className="text-base font-medium text-foreground">订单已关闭</p>
              <p className="text-sm text-secondary-text">请重新下单。</p>
              <Button type="button" variant="primary" onClick={onClose} className="mt-2">
                关闭
              </Button>
            </div>
          )}

          {phase === 'error' && (
            <div className="flex flex-col items-center gap-3 py-4">
              <AlertTriangle className="h-8 w-8 text-amber-400" />
              {error ? (
                <SettingsAlert
                  title="支付未能继续"
                  message={isParsedApiError(error) ? error.message : error}
                  variant="error"
                />
              ) : null}
              <div className="flex gap-2">
                <Button type="button" variant="ghost" onClick={onClose}>
                  关闭
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  onClick={() => {
                    setError(null);
                    setPhase('select_provider');
                  }}
                >
                  <RefreshCw className="h-4 w-4" /> 重试
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PaymentDialog;
