import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowUpRight,
  CheckCircle2,
  CreditCard,
  FileText,
  Gift,
  Receipt,
  Sparkles,
  TicketPercent,
} from 'lucide-react';
import { Button, Card, Input, Loading } from '../components/common';
import { SettingsAlert } from '../components/settings';
import PaymentDialog from '../components/billing/PaymentDialog';
import { accountApi } from '../api/account';
import {
  billingApi,
  type BillingPlan,
  type BillingPlansResponse,
  type BillingSubscriptionResponse,
} from '../api/billing';
import { getParsedApiError, isParsedApiError, type ParsedApiError } from '../api/error';
import { useAuth } from '../hooks';
import { cn } from '../utils/cn';

type FormError = ParsedApiError | string | null;

const PLAN_HIGHLIGHTS: Record<string, string> = {
  free: '快速体验 AI 自选股分析的核心能力。',
  pro: '面向重度用户, 解锁更多自选股 / 高级模型 / Webhook 推送。',
  pro_yearly: 'Pro 年付, 折扣力度最大, 适合长期使用。',
};

const formatPrice = (priceCents: number, currency: string): string => {
  if (priceCents <= 0) {
    return '联系客服';
  }
  const amount = (priceCents / 100).toFixed(0);
  return currency.toUpperCase() === 'CNY' ? `¥${amount}` : `${currency} ${amount}`;
};

const formatDate = (value?: string | null): string => {
  if (!value) {
    return '—';
  }
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

const PlanCard: React.FC<{
  plan: BillingPlan;
  isCurrent: boolean;
  isRecommended?: boolean;
  onUpgrade?: (plan: BillingPlan) => void;
  upgradeLoading?: boolean;
}> = ({ plan, isCurrent, isRecommended, onUpgrade, upgradeLoading }) => {
  const tone = isCurrent ? 'current' : isRecommended ? 'recommended' : 'default';
  const canUpgrade = !isCurrent && plan.code !== 'free' && plan.priceCents > 0;
  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-2xl border p-5 transition-shadow',
        tone === 'current'
          ? 'border-cyan/40 bg-cyan/5 shadow-[0_0_20px_var(--nav-indicator-shadow)]'
          : tone === 'recommended'
            ? 'border-purple-400/40 bg-purple-500/5'
            : 'border-border/60 bg-card/60'
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-secondary-text">
            {plan.code.toUpperCase()}
          </p>
          <h3 className="mt-1 text-lg font-semibold text-foreground">{plan.name}</h3>
        </div>
        {tone === 'current' ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-cyan/40 bg-cyan/10 px-2 py-0.5 text-xs text-cyan">
            <CheckCircle2 className="h-3 w-3" /> 当前档位
          </span>
        ) : tone === 'recommended' ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-purple-400/40 bg-purple-500/10 px-2 py-0.5 text-xs text-purple-300">
            <Sparkles className="h-3 w-3" /> 推荐
          </span>
        ) : null}
      </div>

      <div className="text-2xl font-semibold text-foreground">
        {formatPrice(plan.priceCents, plan.currency)}
        {plan.priceCents > 0 ? (
          <span className="ml-1 text-sm font-normal text-secondary-text">
            / {plan.code === 'pro_yearly' ? '年' : '月'}
          </span>
        ) : null}
      </div>

      <p className="text-sm text-secondary-text">
        {PLAN_HIGHLIGHTS[plan.code] ?? '—'}
      </p>

      <ul className="space-y-1.5 text-sm">
        <li className="flex gap-2 text-foreground/90">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-cyan" />
          每日 {plan.dailyAnalysisLimit > 0 ? `${plan.dailyAnalysisLimit} 次` : '不限'} 分析
        </li>
        <li className="flex gap-2 text-foreground/90">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-cyan" />
          每日 {plan.dailyAgentLimit > 0 ? `${plan.dailyAgentLimit} 次` : '不限'} Agent 问股
        </li>
        <li className="flex gap-2 text-foreground/90">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-cyan" />
          自选股上限 {plan.maxStocks > 0 ? `${plan.maxStocks} 只` : '不限'}
        </li>
        <li
          className={cn(
            'flex gap-2',
            plan.canWebhook ? 'text-foreground/90' : 'text-secondary-text/70'
          )}
        >
          <CheckCircle2
            className={cn(
              'h-4 w-4 shrink-0',
              plan.canWebhook ? 'text-cyan' : 'text-border'
            )}
          />
          自定义 Webhook 推送 {plan.canWebhook ? '' : '(不支持)'}
        </li>
        <li
          className={cn(
            'flex gap-2',
            plan.canByok ? 'text-foreground/90' : 'text-secondary-text/70'
          )}
        >
          <CheckCircle2
            className={cn(
              'h-4 w-4 shrink-0',
              plan.canByok ? 'text-cyan' : 'text-border'
            )}
          />
          支持 BYOK (自带 API Key) {plan.canByok ? '' : '(不支持)'}
        </li>
      </ul>
      {canUpgrade && onUpgrade && (
        <Button
          type="button"
          variant="primary"
          size="sm"
          className="mt-2 w-full"
          onClick={() => onUpgrade(plan)}
          isLoading={upgradeLoading}
        >
          升级到 {plan.name}
        </Button>
      )}
      {isCurrent && (
        <p className="mt-2 text-center text-xs text-cyan/80">当前套餐</p>
      )}
    </div>
  );
};

const BillingPage: React.FC = () => {
  const { userMode, refreshStatus } = useAuth();

  const [plansResponse, setPlansResponse] = useState<BillingPlansResponse | null>(null);
  const [subscription, setSubscription] = useState<BillingSubscriptionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const [redeemCode, setRedeemCode] = useState('');
  const [isRedeeming, setIsRedeeming] = useState(false);
  const [redeemError, setRedeemError] = useState<FormError>(null);
  const [redeemInfo, setRedeemInfo] = useState<string | null>(null);

  const [activePlanForPayment, setActivePlanForPayment] = useState<BillingPlan | null>(null);

  useEffect(() => {
    document.title = '会员中心 - DSA';
  }, []);

  const loggedIn = Boolean(userMode?.loggedIn);
  const userModeEnabled = Boolean(userMode?.userModeEnabled);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const plans = await billingApi.listPlans();
        if (cancelled) return;
        setPlansResponse(plans);

        if (loggedIn) {
          const sub = await billingApi.getSubscription();
          if (cancelled) return;
          setSubscription(sub);
        } else {
          setSubscription(null);
        }
      } catch (err) {
        if (!cancelled) setError(getParsedApiError(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [loggedIn]);

  const currentPlanCode = useMemo(
    () => subscription?.plan.code ?? plansResponse?.currentPlan?.code ?? null,
    [subscription, plansResponse]
  );

  const handleRedeem = async (e: React.FormEvent) => {
    e.preventDefault();
    setRedeemError(null);
    setRedeemInfo(null);
    if (!redeemCode.trim()) {
      setRedeemError('请输入兑换码');
      return;
    }
    setIsRedeeming(true);
    try {
      const res = await accountApi.redeem(redeemCode.trim());
      setRedeemInfo(`兑换成功! 已为你开通 ${res.plan.name}。`);
      setRedeemCode('');
      // 重新拉取套餐 / 订阅 / userMode
      const [plans, sub] = await Promise.all([
        billingApi.listPlans(),
        billingApi.getSubscription(),
      ]);
      setPlansResponse(plans);
      setSubscription(sub);
      await refreshStatus();
    } catch (err) {
      setRedeemError(getParsedApiError(err));
    } finally {
      setIsRedeeming(false);
    }
  };

  if (!userModeEnabled) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10">
        <Card title="会员中心" subtitle="BILLING">
          <p className="text-sm text-secondary-text">
            当前实例未启用 To C 多用户模式, 会员中心暂不可用。
          </p>
        </Card>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <Loading />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <SettingsAlert title="加载失败" message={error.message} variant="error" />
      </div>
    );
  }

  const plans = plansResponse?.plans ?? [];
  const recommendedCode =
    plans.find((p) => p.code === 'pro')?.code ?? plans.find((p) => p.canByok)?.code;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-8 lg:py-10">
      <div className="space-y-1">
        <p className="text-xs font-medium uppercase tracking-wider text-secondary-text">
          BILLING
        </p>
        <h1 className="text-2xl font-semibold text-foreground">会员中心</h1>
        <p className="text-sm text-secondary-text">
          升级到 Pro 解锁更多自选股、高级模型、Webhook 推送, 以及自带 API Key (BYOK)。
        </p>
      </div>

      {subscription ? (
        <Card title="当前订阅" subtitle="STATUS">
          <div className="flex flex-wrap items-center gap-4">
            <div>
              <p className="text-xs uppercase tracking-wider text-secondary-text">套餐</p>
              <p className="mt-1 text-lg font-semibold text-foreground">
                {subscription.plan.name}
                {subscription.plan.isPro ? (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-purple-400/40 bg-purple-500/10 px-2 py-0.5 text-xs text-purple-300">
                    Pro
                  </span>
                ) : null}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wider text-secondary-text">到期时间</p>
              <p className="mt-1 text-foreground">
                {subscription.plan.expiresAt
                  ? formatDate(subscription.plan.expiresAt)
                  : '永久'}
              </p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wider text-secondary-text">能力</p>
              <p className="mt-1 text-sm text-foreground">
                {subscription.plan.canByok ? 'BYOK · ' : ''}
                {subscription.plan.canWebhook ? 'Webhook · ' : ''}
                每日 {subscription.plan.dailyAnalysisLimit > 0 ? subscription.plan.dailyAnalysisLimit : '∞'} /{' '}
                {subscription.plan.dailyAgentLimit > 0 ? subscription.plan.dailyAgentLimit : '∞'}
              </p>
            </div>
          </div>
        </Card>
      ) : null}

      <Card title="套餐对比" subtitle="PLANS">
        {plans.length === 0 ? (
          <p className="text-sm text-secondary-text">暂未配置套餐, 请联系站点管理员。</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {plans.map((plan) => (
              <PlanCard
                key={plan.code}
                plan={plan}
                isCurrent={currentPlanCode === plan.code}
                isRecommended={
                  recommendedCode === plan.code && currentPlanCode !== plan.code
                }
                onUpgrade={loggedIn ? setActivePlanForPayment : undefined}
              />
            ))}
          </div>
        )}
        {!loggedIn && (
          <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-500/5 px-4 py-3 text-xs text-amber-300/80">
            升级需要登录, 请先 <Link to="/login" className="text-cyan underline">登录</Link>。
          </div>
        )}
      </Card>

      <Card title="使用兑换码升级" subtitle="REDEEM">
        <form onSubmit={handleRedeem} className="flex flex-col gap-4 md:flex-row md:items-end">
          <div className="flex-1">
            <Input
              id="redeem-code"
              type="text"
              label="兑换码"
              hint="一次性使用, 兑换成功后会立即生效。"
              placeholder="例如: VIP-AAAA"
              iconType="key"
              value={redeemCode}
              onChange={(e) => setRedeemCode(e.target.value)}
              disabled={isRedeeming || !loggedIn}
            />
          </div>
          <Button type="submit" variant="primary" isLoading={isRedeeming} disabled={!loggedIn}>
            <TicketPercent className="h-4 w-4" /> 立即兑换
          </Button>
        </form>
        {!loggedIn ? (
          <p className="mt-3 text-xs text-secondary-text">
            兑换需要登录, 请先 <Link to="/login" className="text-cyan">登录</Link>。
          </p>
        ) : null}
        {redeemError ? (
          isParsedApiError(redeemError) ? (
            <SettingsAlert
              title="兑换失败"
              message={redeemError.message}
              variant="error"
              className="mt-4"
            />
          ) : (
            <SettingsAlert title="兑换失败" message={redeemError} variant="error" className="mt-4" />
          )
        ) : null}
        {redeemInfo ? (
          <SettingsAlert title="兑换成功" message={redeemInfo} variant="success" className="mt-4" />
        ) : null}
      </Card>

      {subscription && subscription.subscriptions.length > 0 ? (
        <Card title="订阅记录" subtitle="HISTORY">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-secondary-text">
                <tr>
                  <th className="pb-2 pr-3">开始时间</th>
                  <th className="pb-2 pr-3">套餐</th>
                  <th className="pb-2 pr-3">来源</th>
                  <th className="pb-2 pr-3">到期</th>
                  <th className="pb-2">备注</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {subscription.subscriptions.map((row) => (
                  <tr key={row.id} className="text-foreground">
                    <td className="py-2 pr-3 tabular-nums">{formatDate(row.startedAt)}</td>
                    <td className="py-2 pr-3">{row.planCode}</td>
                    <td className="py-2 pr-3">
                      <span className="inline-flex items-center gap-1 text-xs text-secondary-text">
                        <Gift className="h-3 w-3" /> {row.source}
                      </span>
                    </td>
                    <td className="py-2 pr-3 tabular-nums">{formatDate(row.expiresAt)}</td>
                    <td className="py-2 text-xs text-secondary-text">{row.note ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <div className="flex flex-wrap items-center gap-4 text-xs text-secondary-text">
        <span><CreditCard className="mr-1 inline h-3 w-3" />内容由 AI 生成, 仅供参考, 不构成投资建议。</span>
        <Link to="/account/orders" className="inline-flex items-center gap-1 text-cyan hover:underline">
          <Receipt className="h-3 w-3" /> 我的订单
        </Link>
        <Link to="/account/invoices" className="inline-flex items-center gap-1 text-cyan hover:underline">
          <FileText className="h-3 w-3" /> 申请发票
        </Link>
        <Link to="/legal/terms" className="inline-flex items-center gap-1 text-secondary-text hover:text-foreground">
          用户协议
        </Link>
        <Link to="/legal/privacy" className="inline-flex items-center gap-1 text-secondary-text hover:text-foreground">
          隐私政策
        </Link>
        <Link to="/legal/risk-disclosure" className="inline-flex items-center gap-1 text-secondary-text hover:text-foreground">
          风险揭示
        </Link>
        <Link to="/account" className="inline-flex items-center gap-1 text-cyan hover:underline">
          返回账户设置 <ArrowUpRight className="h-3 w-3" />
        </Link>
      </div>

      {activePlanForPayment && (
        <PaymentDialog
          open={Boolean(activePlanForPayment)}
          plan={activePlanForPayment}
          onClose={() => setActivePlanForPayment(null)}
          onPaid={() => {
            // 关闭后台轮询拉到的状态后刷新订阅 + 套餐 + userMode quota
            void (async () => {
              try {
                const [plansFresh, subFresh] = await Promise.all([
                  billingApi.listPlans(),
                  billingApi.getSubscription(),
                ]);
                setPlansResponse(plansFresh);
                setSubscription(subFresh);
                await refreshStatus();
              } catch (err) {
                console.warn('refresh after paid failed', err);
              }
            })();
          }}
        />
      )}
    </div>
  );
};

export default BillingPage;
