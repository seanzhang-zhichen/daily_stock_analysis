import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { AlertCircle, ArrowRight, Clock, X } from 'lucide-react';
import { useAuth } from '../../hooks';
import { cn } from '../../utils/cn';

const DISMISS_STORAGE_KEY = 'dsa.renewalBanner.dismiss';

type DismissEntry = {
  /** plan_expires_at + reminder bucket，避免新周期复用旧关闭状态。 */
  signature: string;
  /** 关闭时间戳，6 小时后自动重新弹出。 */
  ts: number;
};

const DISMISS_TTL_MS = 6 * 60 * 60 * 1000; // 6h

function loadDismiss(): DismissEntry | null {
  try {
    const raw = localStorage.getItem(DISMISS_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw) as DismissEntry;
  } catch {
    return null;
  }
}

function saveDismiss(entry: DismissEntry): void {
  try {
    localStorage.setItem(DISMISS_STORAGE_KEY, JSON.stringify(entry));
  } catch {
    /* ignore quota errors */
  }
}

/**
 * 顶栏续费提示 (Phase 2 + Phase 4 收尾)。
 *
 * 当 `/api/v1/account/status` 返回的 `renewal.willExpireSoon` 为 true,
 * 或 `renewal.expired` 为 true 时, 在路由顶部渲染一条粘性 banner,
 * 引导用户跳转 `/billing?renew=1`。
 *
 * 关闭后会按 `(expiresAt+bucket)` 在 localStorage 缓存 6 小时, 期间不再弹出;
 * 跨周期 (新订单 → 新 expiresAt) 自动重置, 避免旧关闭状态影响下一轮提醒。
 */
export const RenewalBanner: React.FC = () => {
  const { userMode } = useAuth();
  const location = useLocation();
  const [dismissed, setDismissed] = useState<DismissEntry | null>(() => loadDismiss());

  const renewal = userMode?.renewal ?? null;
  const planName = userMode?.plan?.name ?? userMode?.user?.plan ?? 'Pro';

  const signature = useMemo(() => {
    if (!renewal) {
      return '';
    }
    const bucket = renewal.expired
      ? 'expired'
      : renewal.daysRemaining <= 1
        ? '1d'
        : renewal.daysRemaining <= 3
          ? '3d'
          : '7d';
    return `${renewal.planCode}|${renewal.expiresAt ?? ''}|${bucket}`;
  }, [renewal]);

  // 跨周期自动失效旧 dismiss
  useEffect(() => {
    if (!dismissed) {
      return;
    }
    if (dismissed.signature !== signature) {
      return;
    }
    if (Date.now() - dismissed.ts > DISMISS_TTL_MS) {
      setDismissed(null);
    }
  }, [dismissed, signature]);

  if (!renewal) {
    return null;
  }
  if (!renewal.willExpireSoon && !renewal.expired) {
    return null;
  }
  if (dismissed && dismissed.signature === signature && Date.now() - dismissed.ts <= DISMISS_TTL_MS) {
    return null;
  }
  // 在登录 / 注册 / 法律页 / 引导页等公开页面不展示
  if (
    location.pathname === '/login' ||
    location.pathname === '/register' ||
    location.pathname === '/forgot-password' ||
    location.pathname === '/verify-email' ||
    location.pathname === '/onboarding' ||
    location.pathname.startsWith('/legal/')
  ) {
    return null;
  }

  const isExpired = renewal.expired;
  const tone = isExpired ? 'expired' : renewal.daysRemaining <= 1 ? 'urgent' : 'warning';

  const toneStyles = {
    expired:
      'border-red-500/40 bg-red-500/10 text-red-200',
    urgent:
      'border-amber-500/50 bg-amber-500/15 text-amber-100',
    warning:
      'border-amber-500/30 bg-amber-500/10 text-amber-200',
  }[tone];

  const Icon = isExpired ? AlertCircle : Clock;

  const message = isExpired
    ? `您的「${planName}」套餐已到期，账户已降级为 Free 档`
    : renewal.daysRemaining <= 0
      ? `您的「${planName}」套餐今日到期`
      : `您的「${planName}」套餐还有 ${renewal.daysRemaining} 天到期`;

  const handleDismiss = () => {
    const entry: DismissEntry = { signature, ts: Date.now() };
    saveDismiss(entry);
    setDismissed(entry);
  };

  return (
    <div
      role="status"
      data-testid="renewal-banner"
      className={cn(
        'sticky top-0 z-30 flex w-full items-center gap-3 border-b px-3 py-2 text-xs sm:px-4 sm:text-sm',
        toneStyles
      )}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 truncate">
        {message}
        {!isExpired && (
          <span className="ml-2 hidden text-secondary-text/80 sm:inline">
            到期后将自动降级为 Free 档，记得及时续费
          </span>
        )}
      </span>
      <Link
        to="/billing?renew=1"
        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-current/40 px-2 py-1 text-xs font-medium transition-colors hover:bg-current/10"
      >
        立即续费
        <ArrowRight className="h-3.5 w-3.5" aria-hidden />
      </Link>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="暂时关闭续费提示"
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-current/70 transition-colors hover:bg-current/10 hover:text-current"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
};
