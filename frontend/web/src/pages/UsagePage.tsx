import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { BarChart3, BrainCircuit, CalendarDays, Loader2, RefreshCw } from 'lucide-react';
import { usageApi } from '../api/usage';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { Badge, Button, Card, EmptyState } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import type { UsageCallTypeBreakdown, UsageModelBreakdown, UsagePeriod, UsageSummaryResponse } from '../types/usage';

type PeriodOption = {
  key: UsagePeriod;
  label: string;
};

const PERIOD_OPTIONS: PeriodOption[] = [
  { key: 'today', label: '今天' },
  { key: 'month', label: '本月' },
  { key: 'all', label: '全部' },
];

function formatInteger(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  return value.toLocaleString('zh-CN');
}

function formatCallType(value: string): string {
  if (value === 'analysis') return '股票分析';
  if (value === 'agent') return '问股 Agent';
  if (value === 'market_review') return '大盘复盘';
  if (value === 'deep_research') return '深度研究';
  return value || '未知';
}

function getTokenShare(tokens: number, total: number): number {
  if (!total || total <= 0) return 0;
  return Math.max(0, Math.min(100, (tokens / total) * 100));
}

type BreakdownRowProps = {
  label: string;
  calls: number;
  tokens: number;
  totalTokens: number;
  badge?: React.ReactNode;
};

const BreakdownRow: React.FC<BreakdownRowProps> = ({ label, calls, tokens, totalTokens, badge }) => {
  const share = getTokenShare(tokens, totalTokens);
  return (
    <div className="rounded-xl border border-border/60 bg-card/50 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-foreground">{label}</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-text">
            <span>{formatInteger(calls)} 次调用</span>
            <span>{formatInteger(tokens)} tokens</span>
          </div>
        </div>
        {badge}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-muted">
          <div className="h-full rounded-full bg-primary transition-[width] duration-300" style={{ width: `${share}%` }} />
        </div>
        <span className="w-12 text-right text-xs tabular-nums text-muted-text">{share.toFixed(1)}%</span>
      </div>
    </div>
  );
};

function sortByTokens<T extends { totalTokens: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => b.totalTokens - a.totalTokens);
}

const UsagePage: React.FC = () => {
  const [period, setPeriod] = useState<UsagePeriod>('month');
  const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const callTypeRows = useMemo<UsageCallTypeBreakdown[]>(
    () => sortByTokens(summary?.byCallType || []),
    [summary?.byCallType],
  );
  const modelRows = useMemo<UsageModelBreakdown[]>(
    () => sortByTokens(summary?.byModel || []),
    [summary?.byModel],
  );

  const loadSummary = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await usageApi.getSummary(period));
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    document.title = '用量看板 - DSA';
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  return (
    <StandardPageLayout>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-text">USAGE</p>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">用量看板</h1>
          <p className="text-sm text-secondary-text/80">
            查看 LLM 调用次数、Token 消耗，以及按调用类型和模型拆分的用量。
          </p>
        </div>
        <Button variant="outline" onClick={() => void loadSummary()} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> 刷新
        </Button>
      </div>

      {error ? <SettingsAlert title="加载失败" message={error.message} variant="error" /> : null}

      <Card>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {PERIOD_OPTIONS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                  period === item.key
                    ? 'border-primary/40 bg-primary/10 text-primary'
                    : 'border-border/60 bg-card/50 text-secondary-text hover:text-foreground'
                }`}
                onClick={() => setPeriod(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-text">
            <CalendarDays className="h-4 w-4" />
            {summary ? `${summary.fromDate} 至 ${summary.toDate}` : '等待加载'}
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Card padding="sm">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs text-muted-text">总调用</p>
              <p className="mt-1 text-2xl font-semibold text-foreground">{formatInteger(summary?.totalCalls)}</p>
            </div>
            <BarChart3 className="h-5 w-5 text-primary" />
          </div>
        </Card>
        <Card padding="sm">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs text-muted-text">总 Tokens</p>
              <p className="mt-1 text-2xl font-semibold text-foreground">{formatInteger(summary?.totalTokens)}</p>
            </div>
            <BrainCircuit className="h-5 w-5 text-primary" />
          </div>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">平均 Tokens / 调用</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">
            {summary && summary.totalCalls > 0
              ? formatInteger(Math.round(summary.totalTokens / summary.totalCalls))
              : '--'}
          </p>
        </Card>
      </div>

      {loading && !summary ? (
        <Card>
          <div className="flex items-center gap-2 text-sm text-secondary-text">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载用量数据中...
          </div>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <Card title="调用类型" subtitle={`CALL TYPES (${callTypeRows.length})`}>
            {callTypeRows.length === 0 ? (
              <EmptyState title="暂无调用类型数据" description="当前周期还没有记录到 LLM 调用。" />
            ) : (
              <div className="space-y-2">
                {callTypeRows.map((item) => (
                  <BreakdownRow
                    key={item.callType}
                    label={formatCallType(item.callType)}
                    calls={item.calls}
                    tokens={item.totalTokens}
                    totalTokens={summary?.totalTokens || 0}
                    badge={<Badge variant="info">{item.callType}</Badge>}
                  />
                ))}
              </div>
            )}
          </Card>

          <Card title="模型分布" subtitle={`MODELS (${modelRows.length})`}>
            {modelRows.length === 0 ? (
              <EmptyState title="暂无模型数据" description="当前周期还没有记录到模型维度用量。" />
            ) : (
              <div className="space-y-2">
                {modelRows.map((item) => (
                  <BreakdownRow
                    key={item.model}
                    label={item.model}
                    calls={item.calls}
                    tokens={item.totalTokens}
                    totalTokens={summary?.totalTokens || 0}
                  />
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </StandardPageLayout>
  );
};

export default UsagePage;
