import type React from 'react';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Gauge, Sparkles } from 'lucide-react';
import { useAuth } from '../../hooks';
import { cn } from '../../utils/cn';

type QuotaIndicatorProps = {
  /** 折叠态 (`true`): 仅显示图标 + 提示, 用于折叠后的侧边栏。 */
  collapsed?: boolean;
  className?: string;
  onNavigate?: () => void;
};

type Tone = 'neutral' | 'warning' | 'brand';

type QuotaSummary =
  | {
      kind: 'unlimited';
      tone: Tone;
      label: string;
      hint: string;
    }
  | {
      kind: 'metered';
      tone: Tone;
      label: string;
      hint: string;
      analysisRemaining: number;
      analysisLimit: number;
      agentRemaining: number;
      agentLimit: number;
    };

const TONE_STYLES: Record<Tone, string> = {
  neutral:
    'border-border/60 bg-card/70 text-secondary-text hover:bg-hover hover:text-foreground',
  warning:
    'border-amber-500/40 bg-amber-500/10 text-amber-300 hover:border-amber-400/60 hover:bg-amber-500/15',
  brand:
    'border-cyan/30 bg-cyan/10 text-cyan hover:bg-cyan/15',
};

function formatRemaining(remaining: number | null | undefined, limit: number) {
  if (remaining == null) {
    return '∞';
  }
  if (limit <= 0) {
    return '∞';
  }
  return `${remaining}/${limit}`;
}

/**
 * Quota header bar (wireframe §6) rendered inside the sidebar so it shows on
 * both desktop and mobile drawer. Handles three tones:
 *
 *  - neutral:   还有余量, 中性色
 *  - warning:   今日额度耗尽, 引导到 /billing 或 /account/api-keys
 *  - brand:     不限额 / BYOK / 自部署模式, 品牌色
 *
 * The component renders nothing when To C 用户体系未启用 — 单管理员模式不需要
 * 额度提示。
 */
export const QuotaIndicator: React.FC<QuotaIndicatorProps> = ({
  collapsed = false,
  className,
  onNavigate,
}) => {
  const { userMode } = useAuth();

  const summary = useMemo<QuotaSummary | null>(() => {
    if (!userMode?.userModeEnabled) {
      return null;
    }
    if (!userMode.loggedIn) {
      return null;
    }

    const quota = userMode.quota;
    const plan = userMode.plan;

    // 不限额 (管理员 / 套餐填了 0): 直接显示 brand 色提示
    if (!quota || (quota.analysisLimit <= 0 && quota.agentLimit <= 0)) {
      return {
        kind: 'unlimited' as const,
        tone: 'brand' as const,
        label: '不限额度',
        hint: plan?.isPro ? `${plan.name} · 不限额度` : '不限额度',
      };
    }

    const analysisRemaining = quota.analysisRemaining ?? 0;
    const agentRemaining = quota.agentRemaining ?? 0;
    const analysisLimit = quota.analysisLimit;
    const agentLimit = quota.agentLimit;

    const isAnalysisExhausted = analysisLimit > 0 && analysisRemaining <= 0;
    const isAgentExhausted = agentLimit > 0 && agentRemaining <= 0;
    const isExhausted = isAnalysisExhausted && isAgentExhausted;

    if (isExhausted) {
      const hint = plan?.canByok
        ? '今日已用完 · 切换 BYOK'
        : '今日已用完 · 升级 Pro';
      return {
        kind: 'metered' as const,
        tone: 'warning' as const,
        label: `分析 ${formatRemaining(analysisRemaining, analysisLimit)} · 问股 ${formatRemaining(
          agentRemaining,
          agentLimit
        )}`,
        hint,
        analysisRemaining,
        analysisLimit,
        agentRemaining,
        agentLimit,
      };
    }

    return {
      kind: 'metered' as const,
      tone: 'neutral' as const,
      label: `分析 ${formatRemaining(analysisRemaining, analysisLimit)} · 问股 ${formatRemaining(
        agentRemaining,
        agentLimit
      )}`,
      hint: `今日剩余 · ${plan?.name ?? '免费会员'}`,
      analysisRemaining,
      analysisLimit,
      agentRemaining,
      agentLimit,
    };
  }, [userMode]);

  if (summary === null) {
    return null;
  }

  const tone: Tone = summary.tone;
  const exhausted = summary.kind === 'metered' && summary.tone === 'warning';
  const target = exhausted
    ? userMode?.plan?.canByok
      ? '/account/api-keys'
      : '/billing'
    : '/account';

  const Icon = tone === 'brand' ? Sparkles : Gauge;

  if (collapsed) {
    return (
      <Link
        to={target}
        onClick={onNavigate}
        aria-label={summary.hint}
        title={`${summary.hint}: ${summary.label}`}
        className={cn(
          'mt-3 flex h-10 w-10 items-center justify-center self-center rounded-xl border transition-colors',
          TONE_STYLES[tone],
          className
        )}
      >
        <Icon className="h-4 w-4" aria-hidden />
      </Link>
    );
  }

  return (
    <Link
      to={target}
      onClick={onNavigate}
      aria-label={summary.hint}
      title={summary.hint}
      data-testid="quota-indicator"
      className={cn(
        'mt-3 flex flex-col gap-1 rounded-xl border px-3 py-2 text-xs transition-colors',
        TONE_STYLES[tone],
        className
      )}
    >
      <span className="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wide opacity-80">
        <Icon className="h-3.5 w-3.5" aria-hidden />
        {summary.hint}
      </span>
      <span className="truncate text-sm font-semibold tabular-nums">
        {summary.label}
      </span>
    </Link>
  );
};
