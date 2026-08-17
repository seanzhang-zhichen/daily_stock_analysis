import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, Clock3, Cpu, Database, Gauge, RefreshCw } from 'lucide-react';
import { usageApi } from '../api/usage';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, Button, Card, EmptyState, PageHeader, StandardPageLayout, StatCard } from '../components/common';
import type { UsageCallTypeBreakdown, UsageDashboardResponse, UsageModelBreakdown, UsagePeriod } from '../types/usage';

const PERIOD_OPTIONS: Array<{ key: UsagePeriod; label: string }> = [
  { key: 'today', label: '今天' },
  { key: 'month', label: '本月' },
  { key: 'all', label: '全部' },
];

function formatNumber(value: number | null | undefined): string {
  return (value ?? 0).toLocaleString('zh-CN');
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value || '-' : date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function callTypeLabel(value: string): string {
  return ({ analysis: '个股分析', agent: '问股 Agent', market_review: '大盘复盘', deep_research: '深度研究' } as Record<string, string>)[value] || value || '未知';
}

const ModelCard: React.FC<{ item: UsageModelBreakdown }> = ({ item }) => (
  <Card padding="sm">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h3 className="truncate text-base font-semibold text-foreground">{item.model}</h3>
        <p className="mt-1 text-xs text-secondary-text">{formatNumber(item.calls)} 次调用</p>
      </div>
      <span className="rounded-full border border-primary/20 bg-primary/10 px-2 py-1 text-xs text-primary">
        {formatNumber(item.totalTokens)} tokens
      </span>
    </div>
    <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
      <div><p className="text-xs text-secondary-text">Prompt</p><p className="mt-1 font-medium">{formatNumber(item.promptTokens)}</p></div>
      <div><p className="text-xs text-secondary-text">Completion</p><p className="mt-1 font-medium">{formatNumber(item.completionTokens)}</p></div>
      <div><p className="text-xs text-secondary-text">单次峰值</p><p className="mt-1 font-medium">{formatNumber(item.maxTotalTokens)}</p></div>
    </div>
  </Card>
);

const CallTypeBreakdown: React.FC<{ items: UsageCallTypeBreakdown[] }> = ({ items }) => {
  const largest = Math.max(...items.map((item) => item.totalTokens), 1);
  return (
    <Card title="调用类型" subtitle="BREAKDOWN">
      {items.length === 0 ? <EmptyState title="暂无调用类型数据" /> : (
        <div className="space-y-4">
          {items.map((item) => (
            <div key={item.callType}>
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="font-medium">{callTypeLabel(item.callType)}</span>
                <span className="text-secondary-text">{formatNumber(item.totalTokens)} tokens</span>
              </div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-muted">
                <div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(4, (item.totalTokens / largest) * 100)}%` }} />
              </div>
              <p className="mt-1 text-xs text-secondary-text">
                {formatNumber(item.calls)} 次 · Prompt {formatNumber(item.promptTokens)} · Completion {formatNumber(item.completionTokens)}
              </p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
};

const UsagePage: React.FC = () => {
  const [period, setPeriod] = useState<UsagePeriod>('month');
  const [dashboard, setDashboard] = useState<UsageDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDashboard(await usageApi.getDashboard({ period, limit: 50 }));
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { void loadDashboard(); }, [loadDashboard]);

  const models = useMemo(() => [...(dashboard?.byModel || [])].sort((a, b) => b.totalTokens - a.totalTokens), [dashboard]);
  const callTypes = useMemo(() => [...(dashboard?.byCallType || [])].sort((a, b) => b.totalTokens - a.totalTokens), [dashboard]);

  return (
    <StandardPageLayout>
      <div className="space-y-5">
        <PageHeader
          eyebrow="USAGE"
          title="用量看板"
          description="查看 LLM 调用次数、Prompt/Completion 消耗、模型用量和最近调用明细。"
          actions={(
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex rounded-xl border border-border/70 bg-card/70 p-1">
                {PERIOD_OPTIONS.map((option) => (
                  <button key={option.key} type="button" onClick={() => setPeriod(option.key)} className={`rounded-lg px-3 py-1.5 text-sm ${period === option.key ? 'bg-primary text-white' : 'text-secondary-text hover:bg-hover'}`}>
                    {option.label}
                  </button>
                ))}
              </div>
              <Button variant="outline" onClick={() => void loadDashboard()} disabled={loading}>
                <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> 刷新
              </Button>
            </div>
          )}
        />

        {error ? <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void loadDashboard()} /> : null}
        {loading && !dashboard ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-28 animate-pulse rounded-2xl border border-border/70 bg-card/60" />)}</div> : null}

        {dashboard ? <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard label="总 Tokens" value={formatNumber(dashboard.totalTokens)} hint={`${dashboard.fromDate} 至 ${dashboard.toDate}`} icon={<Database className="h-5 w-5" />} tone="primary" />
            <StatCard label="调用次数" value={formatNumber(dashboard.totalCalls)} hint="已记录的 LLM 调用" icon={<Activity className="h-5 w-5" />} />
            <StatCard label="Prompt tokens" value={formatNumber(dashboard.totalPromptTokens)} hint="输入上下文消耗" icon={<Cpu className="h-5 w-5" />} />
            <StatCard label="Completion tokens" value={formatNumber(dashboard.totalCompletionTokens)} hint="模型输出消耗" icon={<Gauge className="h-5 w-5" />} />
          </div>

          {dashboard.totalCalls === 0 ? <EmptyState title="暂无 Token 用量记录" description="完成一次分析、复盘或问股调用后，这里会显示用量。" /> : (
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)]">
              <section className="space-y-4"><div><h2 className="text-lg font-semibold">模型用量</h2><p className="mt-1 text-sm text-secondary-text">按模型聚合 Token 消耗、调用次数和单次峰值。</p></div><div className="grid gap-4">{models.map((item) => <ModelCard key={item.model} item={item} />)}</div></section>
              <CallTypeBreakdown items={callTypes} />
            </div>
          )}

          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">最近调用</h2><p className="mt-1 text-sm text-secondary-text">最近 50 条 LLM Token 审计记录。</p></div><Clock3 className="h-5 w-5 text-secondary-text" /></div>
            <div className="overflow-hidden rounded-2xl border border-border/70 bg-card/75 shadow-card"><div className="overflow-x-auto"><table className="min-w-full divide-y divide-border/70 text-sm"><thead className="bg-surface-2/70 text-left text-xs uppercase tracking-[0.16em] text-secondary-text"><tr><th className="px-4 py-3 font-medium">时间</th><th className="px-4 py-3 font-medium">类型</th><th className="px-4 py-3 font-medium">模型</th><th className="px-4 py-3 text-right font-medium">Prompt</th><th className="px-4 py-3 text-right font-medium">Completion</th><th className="px-4 py-3 text-right font-medium">Total</th></tr></thead><tbody className="divide-y divide-border/60">{dashboard.recentCalls.length ? dashboard.recentCalls.map((item) => <tr key={item.id} className="hover:bg-hover/60"><td className="whitespace-nowrap px-4 py-3 text-secondary-text">{formatDateTime(item.calledAt)}</td><td className="whitespace-nowrap px-4 py-3">{callTypeLabel(item.callType)}</td><td className="min-w-56 px-4 py-3"><div className="max-w-[18rem] truncate font-medium">{item.model}</div>{item.stockCode ? <div className="text-xs text-secondary-text">{item.stockCode}</div> : null}</td><td className="whitespace-nowrap px-4 py-3 text-right text-secondary-text">{formatNumber(item.promptTokens)}</td><td className="whitespace-nowrap px-4 py-3 text-right text-secondary-text">{formatNumber(item.completionTokens)}</td><td className="whitespace-nowrap px-4 py-3 text-right font-medium">{formatNumber(item.totalTokens)}</td></tr>) : <tr><td colSpan={6} className="px-4 py-8 text-center text-secondary-text">暂无最近调用记录</td></tr>}</tbody></table></div></div>
          </section>
        </> : null}
      </div>
    </StandardPageLayout>
  );
};

export default UsagePage;
