import type React from 'react';
import { Archive, CalendarClock, ChevronRight, CircleDollarSign, ShieldAlert, Target } from 'lucide-react';
import { Badge, Button } from '../common';
import type { DecisionAction, DecisionSignalItem, DecisionSignalStatus } from '../../types/decisionSignals';
import { cn } from '../../utils/cn';
import { formatDateTime } from '../../utils/format';

const ACTION_LABELS: Record<DecisionAction, string> = {
  buy: '买入',
  add: '加仓',
  hold: '持有',
  reduce: '减仓',
  sell: '卖出',
  watch: '观望',
  avoid: '回避',
  alert: '风险提醒',
};

const STATUS_LABELS: Record<DecisionSignalStatus, string> = {
  active: '有效',
  expired: '已过期',
  invalidated: '已失效',
  closed: '已关闭',
  archived: '已归档',
};

const HORIZON_LABELS: Record<string, string> = {
  intraday: '日内',
  '1d': '1 日',
  '3d': '3 日',
  '5d': '5 日',
  '10d': '10 日',
  swing: '波段',
  long: '长期',
};

const MARKET_LABELS: Record<string, string> = {
  cn: 'A 股',
  hk: '港股',
  us: '美股',
};

function actionTone(action: DecisionAction): 'success' | 'warning' | 'danger' | 'info' | 'default' {
  if (action === 'buy' || action === 'add') return 'success';
  if (action === 'sell' || action === 'avoid') return 'danger';
  if (action === 'reduce' || action === 'alert') return 'warning';
  if (action === 'watch') return 'info';
  return 'default';
}

function statusTone(status: DecisionSignalStatus): 'success' | 'warning' | 'danger' | 'default' {
  if (status === 'active') return 'success';
  if (status === 'invalidated') return 'danger';
  if (status === 'expired') return 'warning';
  return 'default';
}

function price(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--';
  return Number(value.toFixed(3)).toString();
}

function confidence(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--';
  return `${Math.round(value * 100)}%`;
}

export const DecisionSignalCard: React.FC<{
  item: DecisionSignalItem;
  onOpen: (item: DecisionSignalItem) => void;
}> = ({ item, onOpen }) => {
  const displayName = item.stockName?.trim() || item.stockCode;
  return (
    <button
      type="button"
      className="ui-card ui-card-hoverable w-full overflow-hidden p-0 text-left"
      onClick={() => onOpen(item)}
      aria-label={`查看 ${displayName} AI 建议详情`}
    >
      <div className="grid min-h-[148px] grid-cols-1 md:grid-cols-[minmax(0,1.45fr)_minmax(260px,0.8fr)_42px]">
        <div className="min-w-0 px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={actionTone(item.action)} size="md">{item.actionLabel || ACTION_LABELS[item.action]}</Badge>
            <Badge variant={statusTone(item.status)}>{STATUS_LABELS[item.status]}</Badge>
            <span className="text-xs text-muted-text">{MARKET_LABELS[item.market] || item.market.toUpperCase()}</span>
          </div>
          <div className="mt-3 flex min-w-0 items-baseline gap-2">
            <h2 className="truncate text-lg font-semibold text-foreground">{displayName}</h2>
            <span className="shrink-0 font-mono text-sm text-secondary-text">{item.stockCode}</span>
          </div>
          <p className="mt-2 line-clamp-2 text-sm leading-6 text-secondary-text">
            {item.reason || '该建议来自历史分析报告，打开查看价格计划与风险条件。'}
          </p>
          <p className="mt-3 text-xs text-muted-text">生成于 {formatDateTime(item.createdAt || undefined)}</p>
        </div>

        <div className="grid grid-cols-2 gap-x-5 gap-y-3 border-t border-border/40 px-5 py-4 md:border-l md:border-t-0">
          <SignalMetric label="评分" value={item.score == null ? '--' : `${item.score}`} />
          <SignalMetric label="置信度" value={confidence(item.confidence)} />
          <SignalMetric label="观察周期" value={HORIZON_LABELS[item.horizon || ''] || item.horizon || '--'} />
          <SignalMetric label="目标价" value={price(item.targetPrice)} />
          <SignalMetric label="止损位" value={price(item.stopLoss)} />
          <SignalMetric label="报告编号" value={`#${item.sourceReportId}`} />
        </div>

        <div className="hidden items-center justify-center border-l border-border/40 text-muted-text md:flex">
          <ChevronRight className="h-5 w-5" />
        </div>
      </div>
    </button>
  );
};

const SignalMetric: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="min-w-0">
    <p className="text-[11px] text-muted-text">{label}</p>
    <p className="mt-1 truncate font-mono text-sm font-semibold tabular-nums text-foreground">{value}</p>
  </div>
);

export const PortfolioSignalSummary: React.FC<{
  item?: DecisionSignalItem;
  loading?: boolean;
}> = ({ item, loading = false }) => {
  if (loading && !item) {
    return <span className="text-xs text-secondary-text">读取中...</span>;
  }
  if (!item) {
    return <span className="text-xs text-muted-text">暂无有效信号</span>;
  }
  return (
    <div className="min-w-[10rem] max-w-[16rem] text-left">
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        <Badge variant={actionTone(item.action)}>{item.actionLabel || ACTION_LABELS[item.action]}</Badge>
        {item.horizon ? (
          <span className="text-[11px] text-secondary-text">{HORIZON_LABELS[item.horizon] || item.horizon}</span>
        ) : null}
      </div>
      {item.riskSummary ? <p className="mt-1 line-clamp-2 text-[11px] text-warning">{item.riskSummary}</p> : null}
      {item.watchConditions ? <p className="mt-1 line-clamp-2 text-[11px] text-secondary-text">{item.watchConditions}</p> : null}
    </div>
  );
};

const DetailBlock: React.FC<{
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
  tone?: 'default' | 'danger' | 'success';
}> = ({ icon, label, children, tone = 'default' }) => (
  <section className="border-b border-border/40 py-5 last:border-b-0">
    <div className={cn(
      'flex items-center gap-2 text-xs font-semibold uppercase',
      tone === 'danger' ? 'text-danger' : tone === 'success' ? 'text-success' : 'text-muted-text',
    )}>
      {icon}
      <span>{label}</span>
    </div>
    <div className="mt-3 text-sm leading-6 text-secondary-text">{children}</div>
  </section>
);

export const DecisionSignalDetails: React.FC<{
  item: DecisionSignalItem;
  isUpdating?: boolean;
  onStatusChange?: (status: 'closed' | 'archived') => void;
}> = ({ item, isUpdating = false, onStatusChange }) => {
  const entry = item.entryLow != null
    ? item.entryHigh != null && item.entryHigh !== item.entryLow
      ? `${price(item.entryLow)} - ${price(item.entryHigh)}`
      : price(item.entryLow)
    : '--';
  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/50 pb-5">
        <div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={actionTone(item.action)} size="md">{item.actionLabel || ACTION_LABELS[item.action]}</Badge>
            <Badge variant={statusTone(item.status)}>{STATUS_LABELS[item.status]}</Badge>
          </div>
          <p className="mt-3 text-sm text-secondary-text">
            {MARKET_LABELS[item.market] || item.market.toUpperCase()} · {item.stockCode} · 报告 #{item.sourceReportId}
          </p>
        </div>
        {item.status === 'active' && onStatusChange ? (
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={isUpdating} onClick={() => onStatusChange('closed')}>
              关闭
            </Button>
            <Button variant="ghost" size="sm" disabled={isUpdating} onClick={() => onStatusChange('archived')}>
              <Archive className="h-4 w-4" />
              归档
            </Button>
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border/50 bg-border/50 sm:grid-cols-4">
        {[
          ['建议评分', item.score == null ? '--' : `${item.score}`],
          ['置信度', confidence(item.confidence)],
          ['观察周期', HORIZON_LABELS[item.horizon || ''] || item.horizon || '--'],
          ['有效至', formatDateTime(item.expiresAt || undefined)],
        ].map(([label, value]) => (
          <div key={label} className="bg-elevated px-3 py-3">
            <p className="text-[11px] text-muted-text">{label}</p>
            <p className="mt-1 font-mono text-sm font-semibold text-foreground">{value}</p>
          </div>
        ))}
      </div>

      <DetailBlock icon={<CircleDollarSign className="h-4 w-4" />} label="执行计划">
        <div className="grid grid-cols-3 gap-3">
          <SignalMetric label="参考区间" value={entry} />
          <SignalMetric label="止损位" value={price(item.stopLoss)} />
          <SignalMetric label="目标价" value={price(item.targetPrice)} />
        </div>
      </DetailBlock>

      <DetailBlock icon={<Target className="h-4 w-4" />} label="建议依据">
        {item.reason || '暂无详细建议依据。'}
      </DetailBlock>

      {item.watchConditions ? (
        <DetailBlock icon={<CalendarClock className="h-4 w-4" />} label="观察条件">
          {item.watchConditions}
        </DetailBlock>
      ) : null}

      {item.riskSummary || item.invalidation ? (
        <DetailBlock icon={<ShieldAlert className="h-4 w-4" />} label="风险与失效条件" tone="danger">
          <div className="space-y-2">
            {item.riskSummary ? <p>{item.riskSummary}</p> : null}
            {item.invalidation && item.invalidation !== item.riskSummary ? <p>{item.invalidation}</p> : null}
          </div>
        </DetailBlock>
      ) : null}

      {item.catalystSummary ? (
        <DetailBlock icon={<Target className="h-4 w-4" />} label="潜在催化" tone="success">
          {item.catalystSummary}
        </DetailBlock>
      ) : null}
    </div>
  );
};

export { ACTION_LABELS, MARKET_LABELS, STATUS_LABELS };
