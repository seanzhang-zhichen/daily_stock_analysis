import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowUpDown,
  Check,
  ExternalLink,
  Filter,
  Loader2,
  RotateCcw,
  Search,
  SlidersHorizontal,
  TrendingUp,
} from 'lucide-react';
import { stockSelectionApi } from '../api/stockSelection';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, Badge, Button, Card, EmptyState, Tooltip } from '../components/common';
import { WorkspacePageLayout } from '../components/common/PageLayouts';
import { cn } from '../utils/cn';
import type {
  StockSelectionCandidate,
  StockSelectionDiagnostics,
  StockSelectionResponse,
  StockSelectionSortBy,
  StockSelectionStrategyItem,
} from '../types/stockSelection';

const INPUT_CLASS = 'ui-input h-10 w-full px-3 text-sm disabled:cursor-not-allowed disabled:opacity-60';
const SELECT_CLASS = `${INPUT_CLASS} appearance-none pr-8`;
const TEXTAREA_CLASS = 'ui-input min-h-[6.5rem] w-full resize-y px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60';

type MarketOption = {
  key: string;
  label: string;
};

const MARKET_OPTIONS: MarketOption[] = [
  { key: 'cn', label: 'A 股' },
  { key: 'hk', label: '港股' },
  { key: 'us', label: '美股' },
];

const SORT_OPTIONS: Array<{ value: StockSelectionSortBy; label: string }> = [
  { value: 'volatility_then_return', label: '波动率优先' },
  { value: 'return_then_volatility', label: '涨幅优先' },
  { value: 'score', label: '综合分优先' },
];

type SelectionFormState = {
  strategy: string;
  markets: string[];
  stockCodesText: string;
  targetDate: string;
  lookbackDays: string;
  minHighPositionPct: string;
  recentHighDays: string;
  limit: string;
  sortBy: StockSelectionSortBy;
};

const DEFAULT_FORM: SelectionFormState = {
  strategy: 'near_new_high',
  markets: ['cn'],
  stockCodesText: '',
  targetDate: '',
  lookbackDays: '120',
  minHighPositionPct: '85',
  recentHighDays: '15',
  limit: '50',
  sortBy: 'volatility_then_return',
};

function splitStockCodes(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\s,，;；]+/)
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean),
    ),
  );
}

function parsePositiveInt(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseNonNegativeInt(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function parsePercent(value: string, fallback: number): number {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(parsed, 100) / 100;
}

function formatPct(value?: number | null, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

function formatNumber(value?: number | null, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '--';
  return value.toFixed(digits);
}

function formatGeneratedAt(value?: string | null): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function marketLabel(value: string): string {
  const normalized = value.toLowerCase();
  return MARKET_OPTIONS.find((item) => item.key === normalized)?.label ?? value.toUpperCase();
}

function sortLabel(value?: string): string {
  return SORT_OPTIONS.find((item) => item.value === value)?.label ?? value ?? '--';
}

function sourceLabel(value: string): string {
  if (value === 'db_cache') return '本地缓存';
  if (value === 'none') return '无数据';
  return value;
}

function resultTone(value: number): 'success' | 'danger' | 'default' {
  if (value > 0) return 'success';
  if (value < 0) return 'danger';
  return 'default';
}

const StatItem: React.FC<{ label: string; value: React.ReactNode; tone?: 'default' | 'success' | 'warning' | 'danger' | 'info' }> = ({
  label,
  value,
  tone = 'default',
}) => {
  const toneClass = {
    default: 'text-foreground',
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
    info: 'text-primary',
  }[tone];

  return (
    <div className="rounded-lg border border-border/30 bg-surface/60 px-3 py-2">
      <p className="text-xs text-muted-text">{label}</p>
      <p className={cn('mt-1 font-mono text-lg font-semibold tabular-nums', toneClass)}>{value}</p>
    </div>
  );
};

const DiagnosticsPanel: React.FC<{ diagnostics?: StockSelectionDiagnostics; result?: StockSelectionResponse | null }> = ({
  diagnostics,
  result,
}) => {
  if (!diagnostics) {
    return (
      <Card padding="md" title="运行概览" subtitle="SUMMARY">
        <div className="grid grid-cols-2 gap-2">
          <StatItem label="股票池" value="--" />
          <StatItem label="入选" value="--" />
          <StatItem label="已处理" value="--" />
          <StatItem label="异常" value="--" />
        </div>
      </Card>
    );
  }

  return (
    <Card padding="md" title="运行概览" subtitle="SUMMARY">
      <div className="grid grid-cols-2 gap-2">
        <StatItem label="股票池" value={diagnostics.total} />
        <StatItem label="入选" value={diagnostics.matched} tone="success" />
        <StatItem label="已处理" value={diagnostics.processed} tone="info" />
        <StatItem label="无数据" value={diagnostics.noData} tone={diagnostics.noData > 0 ? 'warning' : 'default'} />
        <StatItem
          label="数据不足"
          value={diagnostics.insufficientData}
          tone={diagnostics.insufficientData > 0 ? 'warning' : 'default'}
        />
        <StatItem label="异常" value={diagnostics.errors} tone={diagnostics.errors > 0 ? 'danger' : 'default'} />
      </div>
      <div className="mt-3 space-y-2 text-xs text-secondary-text">
        <div className="flex items-center justify-between gap-3">
          <span>生成时间</span>
          <span className="font-mono text-foreground">{formatGeneratedAt(result?.generatedAt)}</span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span>排序</span>
          <span className="font-medium text-foreground">{sortLabel(String(result?.params?.sortBy ?? ''))}</span>
        </div>
      </div>
    </Card>
  );
};

const StrategyPanel: React.FC<{
  strategies: StockSelectionStrategyItem[];
  loading: boolean;
  error: ParsedApiError | null;
  selectedStrategy: string;
}> = ({ strategies, loading, error, selectedStrategy }) => {
  const active = strategies.find((item) => item.name === selectedStrategy);

  return (
    <Card padding="md" title="策略信息" subtitle="STRATEGY">
      {loading ? (
        <div className="flex items-center gap-2 text-sm text-secondary-text">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载策略中
        </div>
      ) : error ? (
        <ApiErrorAlert error={error} />
      ) : active ? (
        <div className="space-y-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="info">{active.displayName}</Badge>
              <span className="font-mono text-xs text-muted-text">{active.name}</span>
            </div>
            <p className="mt-2 text-sm leading-6 text-secondary-text">{active.description}</p>
          </div>
          {active.aliases.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {active.aliases.map((alias) => (
                <span
                  key={alias}
                  className="rounded-md border border-border/35 bg-surface px-2 py-1 font-mono text-[11px] text-muted-text"
                >
                  {alias}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-secondary-text">当前策略可直接执行。</p>
      )}
    </Card>
  );
};

const ResultsTable: React.FC<{ items: StockSelectionCandidate[]; isLoading: boolean; hasRun: boolean }> = ({
  items,
  isLoading,
  hasRun,
}) => {
  if (isLoading && items.length === 0) {
    return (
      <div className="flex min-h-[18rem] flex-col items-center justify-center gap-3 text-sm text-secondary-text">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        正在执行选股
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={<Filter className="h-6 w-6" />}
        title={hasRun ? '暂无入选股票' : '尚未执行选股'}
        description={hasRun ? '当前条件下没有匹配标的。' : '设置策略参数后执行选股，结果会显示在这里。'}
        className="min-h-[18rem] border-dashed"
      />
    );
  }

  return (
    <div className="animate-fade-in">
      <div className="backtest-table-toolbar">
        <div className="backtest-table-toolbar-meta">
          <span className="label-uppercase">RESULTS ({items.length})</span>
          <span className="text-xs text-secondary-text">按后端策略排序返回</span>
        </div>
        <span className="backtest-table-scroll-hint">小屏幕可横向滚动查看完整表格</span>
      </div>
      <div className="backtest-table-wrapper">
        <table className="backtest-table min-w-[1080px] w-full text-sm">
          <thead className="backtest-table-head">
            <tr className="text-left">
              <th className="backtest-table-head-cell">#</th>
              <th className="backtest-table-head-cell">股票</th>
              <th className="backtest-table-head-cell">市场</th>
              <th className="backtest-table-head-cell">最新收盘</th>
              <th className="backtest-table-head-cell">窗口高点</th>
              <th className="backtest-table-head-cell">高点日期</th>
              <th className="backtest-table-head-cell">距高点</th>
              <th className="backtest-table-head-cell">窗口涨幅</th>
              <th className="backtest-table-head-cell">波动率</th>
              <th className="backtest-table-head-cell">综合分</th>
              <th className="backtest-table-head-cell">来源</th>
              <th className="backtest-table-head-cell">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, index) => (
              <tr key={`${item.code}-${item.latestDate}-${index}`} className="backtest-table-row">
                <td className="backtest-table-cell font-mono text-muted-text">{index + 1}</td>
                <td className="backtest-table-cell backtest-table-code">
                  <div className="flex flex-col">
                    <span>{item.code}</span>
                    <span className="text-xs text-muted-text">{item.name || '--'}</span>
                  </div>
                </td>
                <td className="backtest-table-cell">
                  <Badge variant="default">{marketLabel(item.market)}</Badge>
                </td>
                <td className="backtest-table-cell font-mono tabular-nums">
                  <div className="flex flex-col">
                    <span>{formatNumber(item.latestClose, 3)}</span>
                    <span className="text-xs text-muted-text">{item.latestDate}</span>
                  </div>
                </td>
                <td className="backtest-table-cell font-mono tabular-nums">{formatNumber(item.windowHigh, 3)}</td>
                <td className="backtest-table-cell">
                  <div className="flex flex-col">
                    <span>{item.windowHighDate}</span>
                    <span className="text-xs text-muted-text">{item.daysSinceHigh} 日前</span>
                  </div>
                </td>
                <td className="backtest-table-cell">
                  <Badge variant={resultTone(item.distanceToHighPct)}>
                    {formatPct(item.distanceToHighPct)}
                  </Badge>
                </td>
                <td className="backtest-table-cell">
                  <span className={cn(
                    'font-mono tabular-nums',
                    item.windowReturnPct > 0 ? 'text-success' : item.windowReturnPct < 0 ? 'text-danger' : 'text-secondary-text',
                  )}>
                    {formatPct(item.windowReturnPct)}
                  </span>
                </td>
                <td className="backtest-table-cell font-mono tabular-nums">{formatPct(item.volatilityPct)}</td>
                <td className="backtest-table-cell font-mono tabular-nums text-primary">{formatNumber(item.score, 2)}</td>
                <td className="backtest-table-cell text-secondary-text">{sourceLabel(item.source)}</td>
                <td className="backtest-table-cell">
                  <Link
                    to={`/stocks/${encodeURIComponent(item.code)}`}
                    className="inline-flex items-center gap-1 rounded-md border border-border/45 px-2 py-1 text-xs text-secondary-text transition hover:border-primary/35 hover:text-primary"
                  >
                    查看
                    <ExternalLink className="h-3.5 w-3.5" />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const StockSelectionPage: React.FC = () => {
  const [form, setForm] = useState<SelectionFormState>(DEFAULT_FORM);
  const [strategies, setStrategies] = useState<StockSelectionStrategyItem[]>([]);
  const [strategiesLoading, setStrategiesLoading] = useState(false);
  const [strategiesError, setStrategiesError] = useState<ParsedApiError | null>(null);
  const [result, setResult] = useState<StockSelectionResponse | null>(null);
  const [runError, setRunError] = useState<ParsedApiError | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [hasRun, setHasRun] = useState(false);

  useEffect(() => {
    document.title = '策略选股 - AlphaLens';
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadStrategies = async () => {
      setStrategiesLoading(true);
      setStrategiesError(null);
      try {
        const response = await stockSelectionApi.listStrategies();
        if (!cancelled) {
          setStrategies(response.items);
        }
      } catch (error) {
        if (!cancelled) {
          setStrategiesError(getParsedApiError(error));
        }
      } finally {
        if (!cancelled) {
          setStrategiesLoading(false);
        }
      }
    };

    void loadStrategies();
    return () => {
      cancelled = true;
    };
  }, []);

  const stockCodes = useMemo(() => splitStockCodes(form.stockCodesText), [form.stockCodesText]);
  const selectedStrategy = strategies.find((item) => item.name === form.strategy);

  const updateForm = useCallback(<K extends keyof SelectionFormState>(key: K, value: SelectionFormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  }, []);

  const toggleMarket = useCallback((market: string) => {
    setForm((prev) => {
      const exists = prev.markets.includes(market);
      const next = exists ? prev.markets.filter((item) => item !== market) : [...prev.markets, market];
      return { ...prev, markets: next.length > 0 ? next : [market] };
    });
  }, []);

  const handleReset = useCallback(() => {
    setForm(DEFAULT_FORM);
    setRunError(null);
  }, []);

  const handleRun = useCallback(async () => {
    setIsRunning(true);
    setRunError(null);
    setHasRun(true);
    try {
      const response = await stockSelectionApi.run({
        strategy: form.strategy,
        markets: form.markets,
        stockCodes: stockCodes.length > 0 ? stockCodes : undefined,
        targetDate: form.targetDate || undefined,
        lookbackDays: parsePositiveInt(form.lookbackDays, 120),
        minHighPosition: parsePercent(form.minHighPositionPct, 0.85),
        recentHighDays: parseNonNegativeInt(form.recentHighDays, 15),
        limit: Math.min(parsePositiveInt(form.limit, 50), 500),
        sortBy: form.sortBy,
      });
      setResult(response);
    } catch (error) {
      setRunError(getParsedApiError(error));
    } finally {
      setIsRunning(false);
    }
  }, [form, stockCodes]);

  return (
    <WorkspacePageLayout className="min-h-[calc(100vh-2rem)] !p-0">
      <div className="border-b border-white/5 bg-primary/5 px-3 py-2 sm:px-4">
        <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="label-uppercase">STOCK SELECTION</p>
            <h1 className="mt-1 text-xl font-semibold tracking-tight text-foreground">策略选股</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-secondary-text">
            <Badge variant="info">
              <TrendingUp className="h-3.5 w-3.5" />
              {selectedStrategy?.displayName ?? '近新高策略'}
            </Badge>
            <span className="font-mono">{stockCodes.length > 0 ? `${stockCodes.length} 只指定股票` : `${form.markets.map(marketLabel).join(' / ')} 股票池`}</span>
          </div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-hidden p-3 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-h-0 space-y-3 overflow-y-auto">
          <Card padding="md">
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(16rem,1.2fr)_repeat(5,minmax(7rem,0.55fr))_9rem]">
              <label className="flex min-w-0 flex-col gap-1.5">
                <span className="text-xs text-muted-text">策略</span>
                <select
                  value={form.strategy}
                  onChange={(event) => updateForm('strategy', event.target.value)}
                  className={SELECT_CLASS}
                  disabled={isRunning}
                >
                  {strategies.length > 0 ? (
                    strategies.map((strategy) => (
                      <option key={strategy.name} value={strategy.name}>
                        {strategy.displayName}
                      </option>
                    ))
                  ) : (
                    <option value="near_new_high">近新高策略</option>
                  )}
                </select>
              </label>

              <label className="flex min-w-0 flex-col gap-1.5">
                <span className="text-xs text-muted-text">窗口</span>
                <input
                  type="number"
                  min={2}
                  max={500}
                  value={form.lookbackDays}
                  onChange={(event) => updateForm('lookbackDays', event.target.value)}
                  className={`${INPUT_CLASS} text-center font-mono tabular-nums`}
                  disabled={isRunning}
                />
              </label>

              <label className="flex min-w-0 flex-col gap-1.5">
                <span className="text-xs text-muted-text">高点比例</span>
                <div className="relative">
                  <input
                    type="number"
                    min={1}
                    max={100}
                    step={0.1}
                    value={form.minHighPositionPct}
                    onChange={(event) => updateForm('minHighPositionPct', event.target.value)}
                    className={`${INPUT_CLASS} pr-8 text-center font-mono tabular-nums`}
                    disabled={isRunning}
                  />
                  <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-xs text-muted-text">%</span>
                </div>
              </label>

              <label className="flex min-w-0 flex-col gap-1.5">
                <span className="text-xs text-muted-text">高点近 N 日</span>
                <input
                  type="number"
                  min={0}
                  max={500}
                  value={form.recentHighDays}
                  onChange={(event) => updateForm('recentHighDays', event.target.value)}
                  className={`${INPUT_CLASS} text-center font-mono tabular-nums`}
                  disabled={isRunning}
                />
              </label>

              <label className="flex min-w-0 flex-col gap-1.5">
                <span className="text-xs text-muted-text">数量</span>
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={form.limit}
                  onChange={(event) => updateForm('limit', event.target.value)}
                  className={`${INPUT_CLASS} text-center font-mono tabular-nums`}
                  disabled={isRunning}
                />
              </label>

              <label className="flex min-w-0 flex-col gap-1.5">
                <span className="text-xs text-muted-text">目标日期</span>
                <input
                  type="date"
                  value={form.targetDate}
                  onChange={(event) => updateForm('targetDate', event.target.value)}
                  className={`${INPUT_CLASS} text-center font-mono tabular-nums`}
                  disabled={isRunning}
                />
              </label>

              <div className="flex items-end gap-2">
                <Button
                  variant="primary"
                  onClick={() => void handleRun()}
                  isLoading={isRunning}
                  loadingText="执行中"
                  className="h-10 flex-1 whitespace-nowrap"
                >
                  <Search className="h-4 w-4" />
                  执行
                </Button>
                <Tooltip content="恢复默认参数" focusable>
                  <button
                    type="button"
                    onClick={handleReset}
                    disabled={isRunning}
                    className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-surface text-secondary-text transition hover:border-primary/35 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                    aria-label="恢复默认参数"
                  >
                    <RotateCcw className="h-4 w-4" />
                  </button>
                </Tooltip>
              </div>
            </div>

            <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_18rem_15rem]">
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-muted-text">指定股票池</span>
                <textarea
                  value={form.stockCodesText}
                  onChange={(event) => updateForm('stockCodesText', event.target.value)}
                  placeholder="600519, 000001, AAPL"
                  className={TEXTAREA_CLASS}
                  disabled={isRunning}
                />
              </label>

              <div className="flex flex-col gap-1.5">
                <span className="text-xs text-muted-text">市场</span>
                <div className="grid grid-cols-3 gap-2">
                  {MARKET_OPTIONS.map((market) => {
                    const active = form.markets.includes(market.key);
                    return (
                      <button
                        key={market.key}
                        type="button"
                        onClick={() => toggleMarket(market.key)}
                        disabled={isRunning || stockCodes.length > 0}
                        className={cn(
                          'inline-flex h-10 items-center justify-center gap-1.5 rounded-lg border px-2 text-sm transition disabled:cursor-not-allowed disabled:opacity-55',
                          active
                            ? 'border-primary/40 bg-primary/10 text-primary'
                            : 'border-border/60 bg-surface text-secondary-text hover:border-border hover:text-foreground',
                        )}
                      >
                        {active ? <Check className="h-3.5 w-3.5" /> : null}
                        {market.label}
                      </button>
                    );
                  })}
                </div>
                <p className="text-xs text-muted-text">
                  {stockCodes.length > 0 ? '已使用指定股票池' : '未指定股票时按市场加载股票池'}
                </p>
              </div>

              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-muted-text">排序</span>
                <select
                  value={form.sortBy}
                  onChange={(event) => updateForm('sortBy', event.target.value as StockSelectionSortBy)}
                  className={SELECT_CLASS}
                  disabled={isRunning}
                >
                  {SORT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                <div className="rounded-lg border border-border/30 bg-surface/60 px-3 py-2 text-xs text-secondary-text">
                  <div className="flex items-center gap-1.5">
                    <ArrowUpDown className="h-3.5 w-3.5 text-primary" />
                    {sortLabel(form.sortBy)}
                  </div>
                </div>
              </label>
            </div>
          </Card>

          {runError ? <ApiErrorAlert error={runError} /> : null}

          <Card padding="md">
            <ResultsTable items={result?.items ?? []} isLoading={isRunning} hasRun={hasRun} />
          </Card>
        </div>

        <aside className="space-y-3 overflow-y-auto">
          <DiagnosticsPanel diagnostics={result?.diagnostics} result={result} />
          <StrategyPanel
            strategies={strategies}
            loading={strategiesLoading}
            error={strategiesError}
            selectedStrategy={form.strategy}
          />
          <Card padding="md" title="当前参数" subtitle="PARAMS">
            <div className="space-y-2 text-xs text-secondary-text">
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-1">
                  <SlidersHorizontal className="h-3.5 w-3.5 text-primary" />
                  窗口
                </span>
                <span className="font-mono text-foreground">{parsePositiveInt(form.lookbackDays, 120)} 日</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>高点比例</span>
                <span className="font-mono text-foreground">{formatNumber(parsePercent(form.minHighPositionPct, 0.85) * 100, 1)}%</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>高点最近</span>
                <span className="font-mono text-foreground">{parseNonNegativeInt(form.recentHighDays, 15)} 日</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>返回数量</span>
                <span className="font-mono text-foreground">{Math.min(parsePositiveInt(form.limit, 50), 500)}</span>
              </div>
            </div>
          </Card>
        </aside>
      </div>
    </WorkspacePageLayout>
  );
};

export default StockSelectionPage;
