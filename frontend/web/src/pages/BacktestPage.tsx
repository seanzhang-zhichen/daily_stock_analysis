import type React from 'react';
import { useState, useEffect, useCallback } from 'react';
import { Check, HelpCircle, Info, Minus, X } from 'lucide-react';
import { backtestApi } from '../api/backtest';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { ApiErrorAlert, Button, Card, Badge, EmptyState, Pagination, StatusDot, Tooltip } from '../components/common';
import { cn } from '../utils/cn';
import type {
  BacktestResultItem,
  BacktestRunResponse,
  PerformanceMetrics,
} from '../types/backtest';

const BACKTEST_INPUT_CLASS =
  'ui-input h-11 w-full px-4 text-sm disabled:cursor-not-allowed disabled:opacity-60';
const BACKTEST_COMPACT_INPUT_CLASS =
  'ui-input h-10 px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-60';

// ============ Helpers ============

function pct(value?: number | null): string {
  if (value == null) return '--';
  return `${value.toFixed(1)}%`;
}

function outcomeBadge(outcome?: string) {
  if (!outcome) return <Badge variant="default">--</Badge>;
  switch (outcome) {
    case 'win':
      return <Badge variant="success" glow>命中</Badge>;
    case 'loss':
      return <Badge variant="danger" glow>未命中</Badge>;
    case 'neutral':
      return <Badge variant="warning">中性</Badge>;
    default:
      return <Badge variant="default">{outcome}</Badge>;
  }
}

function statusBadge(status: string) {
  switch (status) {
    case 'completed':
      return <Badge variant="success">已完成</Badge>;
    case 'insufficient':
    case 'insufficient_data':
      return <Badge variant="warning">数据不足</Badge>;
    case 'error':
      return <Badge variant="danger">异常</Badge>;
    default:
      return <Badge variant="default">{status}</Badge>;
  }
}

function actualMovementBadge(movement?: string | null) {
  switch (movement) {
    case 'up':
      return <Badge variant="success">上涨</Badge>;
    case 'down':
      return <Badge variant="danger">下跌</Badge>;
    case 'flat':
      return <Badge variant="warning">横盘</Badge>;
    default:
      return <Badge variant="default">--</Badge>;
  }
}

function directionExpectedLabel(direction?: string | null): string {
  switch (direction) {
    case 'up':
      return '预期上涨';
    case 'down':
      return '预期下跌';
    case 'flat':
      return '预期横盘';
    case 'not_down':
      return '预期不下跌';
    case 'not_up':
      return '预期不上涨';
    case 'long':
      return '看多';
    case 'cash':
      return '观望';
    default:
      return direction || '';
  }
}

function boolIcon(value?: boolean | null) {
  if (value === true) {
    return (
      <span
        className="inline-flex items-center justify-center gap-1 rounded-md border border-success/20 bg-success/10 px-1.5 py-0.5 text-xs font-medium text-success"
        aria-label="是"
      >
        <StatusDot tone="success" className="h-1.5 w-1.5 shadow-none" />
        <Check className="h-3.5 w-3.5" />
      </span>
    );
  }

  if (value === false) {
    return (
      <span
        className="inline-flex items-center justify-center gap-1 rounded-md border border-destructive/20 bg-destructive/10 px-1.5 py-0.5 text-xs font-medium text-danger"
        aria-label="否"
      >
        <StatusDot tone="danger" className="h-1.5 w-1.5 shadow-none" />
        <X className="h-3.5 w-3.5" />
      </span>
    );
  }

  return (
    <span
      className="inline-flex items-center justify-center gap-1 rounded-md border border-border/50 bg-surface px-1.5 py-0.5 text-xs font-medium text-muted-text"
      aria-label="未知"
    >
      <StatusDot tone="neutral" className="h-1.5 w-1.5 shadow-none" />
      <Minus className="h-3.5 w-3.5" />
    </span>
  );
}

// ============ Metric Row ============

const MetricRow: React.FC<{ label: string; value: string; hint?: string; accent?: boolean }> = ({ label, value, hint, accent }) => (
  <div className="flex items-center justify-between border-b border-border/10 py-2 last:border-b-0">
    <span className="flex items-center gap-1 text-xs text-secondary-text">
      {label}
      {hint ? (
        <Tooltip content={hint} focusable>
          <HelpCircle className="h-3 w-3 cursor-help text-muted-text" />
        </Tooltip>
      ) : null}
    </span>
    <span className={cn('font-mono text-sm font-semibold', accent ? 'text-primary' : 'text-foreground')}>{value}</span>
  </div>
);

// ============ Performance Card ============

const PerformanceCard: React.FC<{ metrics: PerformanceMetrics; title: string }> = ({ metrics, title }) => (
  <Card padding="md" className="animate-fade-in">
    <div className="mb-3">
      <span className="label-uppercase">{title}</span>
    </div>
    <MetricRow label="方向命中率" hint="AI 判断的涨跌方向与实际方向一致的比例" value={pct(metrics.directionAccuracyPct)} accent />
    <MetricRow label="胜率" hint="按 AI 建议进行模拟交易，最终盈利的比例" value={pct(metrics.winRatePct)} accent />
    <MetricRow label="平均模拟收益" hint="按 AI 建议模拟交易的平均收益率（含止盈止损）" value={pct(metrics.avgSimulatedReturnPct)} />
    <MetricRow label="平均实际涨跌幅" hint="股票在验证周期内的真实平均涨跌幅" value={pct(metrics.avgStockReturnPct)} />
    <MetricRow label="止损触发率" hint="模拟交易中触发止损的比例" value={pct(metrics.stopLossTriggerRate)} />
    <MetricRow label="止盈触发率" hint="模拟交易中触发止盈的比例" value={pct(metrics.takeProfitTriggerRate)} />
    <MetricRow label="平均触达天数" hint="从分析日到首次触发止盈或止损的平均天数" value={metrics.avgDaysToFirstHit != null ? metrics.avgDaysToFirstHit.toFixed(1) : '--'} />
    <div className="flex items-center justify-between border-t border-border/20 pt-2 mt-2">
      <span className="text-xs text-muted-text">验证数</span>
      <span className="text-xs text-secondary-text font-mono">
        {Number(metrics.completedCount)} / {Number(metrics.totalEvaluations)}
      </span>
    </div>
    <div className="flex items-center justify-between">
      <span className="text-xs text-muted-text">命中 / 未命中 / 中性</span>
      <span className="text-xs font-mono">
        <span className="text-success">{metrics.winCount}</span>
        {' / '}
        <span className="text-danger">{metrics.lossCount}</span>
        {' / '}
        <span className="text-warning">{metrics.neutralCount}</span>
      </span>
    </div>
  </Card>
);

// ============ Run Summary ============

const RunSummary: React.FC<{ data: BacktestRunResponse }> = ({ data }) => (
  <div className="animate-fade-in flex flex-wrap items-center gap-4 rounded-lg border border-border/20 bg-surface px-3 py-2 font-mono text-xs">
    <span className="text-secondary-text">已处理：<span className="text-foreground">{data.processed}</span></span>
    <span className="text-secondary-text">已保存：<span className="text-primary">{data.saved}</span></span>
    <span className="text-secondary-text">已完成：<span className="text-success">{data.completed}</span></span>
    <span className="text-secondary-text">数据不足：<span className="text-warning">{data.insufficient}</span></span>
    {data.errors > 0 && (
      <span className="text-secondary-text">错误：<span className="text-danger">{data.errors}</span></span>
    )}
  </div>
);

// ============ BacktestConfigBar ============

type BacktestConfigBarProps = {
  codeFilter: string;
  analysisDateFrom: string;
  analysisDateTo: string;
  evalDays: string;
  forceRerun: boolean;
  isRunning: boolean;
  isLoadingResults: boolean;
  isNextDayValidation: boolean;
  runResult: BacktestRunResponse | null;
  runError: ParsedApiError | null;
  onCodeFilterChange: (v: string) => void;
  onAnalysisDateFromChange: (v: string) => void;
  onAnalysisDateToChange: (v: string) => void;
  onEvalDaysChange: (v: string) => void;
  onForceRerunToggle: (e?: React.ChangeEvent<HTMLInputElement>) => void;
  onFilter: () => void;
  onRun: () => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
};

const BacktestConfigBar: React.FC<BacktestConfigBarProps> = ({
  codeFilter,
  analysisDateFrom,
  analysisDateTo,
  evalDays,
  forceRerun,
  isRunning,
  isLoadingResults,
  isNextDayValidation,
  runResult,
  runError,
  onCodeFilterChange,
  onAnalysisDateFromChange,
  onAnalysisDateToChange,
  onEvalDaysChange,
  onForceRerunToggle,
  onFilter,
  onRun,
  onKeyDown,
}) => (
  <header className="flex-shrink-0 border-b border-white/5 px-3 py-3 sm:px-4">
    <div className="flex max-w-7xl flex-wrap items-start justify-between gap-3">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-[1_1_220px]">
          <input
            type="text"
            value={codeFilter}
            onChange={(e) => onCodeFilterChange(e.target.value.toUpperCase())}
            onKeyDown={onKeyDown}
            placeholder="输入股票代码，可留空查看全部"
            disabled={isRunning}
            className={BACKTEST_INPUT_CLASS}
          />
        </div>
        <Button
          variant="outline"
          onClick={onFilter}
          disabled={isLoadingResults}
          className="whitespace-nowrap"
        >
          筛选
        </Button>
        <div className="flex items-center gap-2 whitespace-nowrap lg:w-40 lg:justify-between">
          <Tooltip content="AI 给出结论后，往后看几个交易日来评估对错。填 1 = 只看下一个交易日" focusable>
            <span className="flex cursor-help items-center gap-1 text-xs text-muted-text">
              验证周期
              <HelpCircle className="h-3 w-3" />
            </span>
          </Tooltip>
          <input
            type="number"
            min={1}
            max={120}
            value={evalDays}
            onChange={(e) => onEvalDaysChange(e.target.value)}
            placeholder="10"
            disabled={isRunning}
            className={`${BACKTEST_COMPACT_INPUT_CLASS} !w-24 text-center tabular-nums`}
          />
        </div>
        <div className="flex items-center gap-2 whitespace-nowrap">
          <Tooltip content="只统计这段时间内 AI 做出的历史分析" focusable>
            <span className="flex cursor-help items-center gap-1 text-xs text-muted-text">
              开始日期
              <HelpCircle className="h-3 w-3" />
            </span>
          </Tooltip>
          <input
            type="date"
            aria-label="分析开始日期"
            value={analysisDateFrom}
            onChange={(e) => onAnalysisDateFromChange(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={isRunning}
            className={`${BACKTEST_COMPACT_INPUT_CLASS} !w-40 text-center tabular-nums`}
          />
        </div>
        <div className="flex items-center gap-2 whitespace-nowrap">
          <span className="text-xs text-muted-text">结束日期</span>
          <input
            type="date"
            aria-label="分析结束日期"
            value={analysisDateTo}
            onChange={(e) => onAnalysisDateToChange(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={isRunning}
            className={`${BACKTEST_COMPACT_INPUT_CLASS} !w-40 text-center tabular-nums`}
          />
        </div>
      </div>
      <div className="flex flex-shrink-0 items-center gap-2 sm:ml-auto">
        <Tooltip content="开启后会重新计算已有的回测记录，否则会跳过已完成的条目" focusable>
          <label className={cn(
            'flex cursor-pointer select-none items-center gap-2 whitespace-nowrap rounded-md border px-3 py-2 text-sm transition-colors',
            isRunning && 'cursor-not-allowed opacity-60',
            forceRerun
              ? 'border-primary/40 bg-primary/10 text-primary'
              : 'border-border/60 bg-surface text-secondary-text hover:border-border hover:text-foreground',
          )}>
            <input
              type="checkbox"
              checked={forceRerun}
              onChange={onForceRerunToggle}
              disabled={isRunning}
              className="h-3.5 w-3.5 cursor-pointer accent-primary disabled:cursor-not-allowed"
            />
            强制重算
          </label>
        </Tooltip>
        <Button
          variant="primary"
          onClick={onRun}
          disabled={isRunning}
          isLoading={isRunning}
          className="whitespace-nowrap"
        >
          开始验证
        </Button>
      </div>
    </div>
    {runResult && (
      <div className="mt-2 max-w-4xl">
        <RunSummary data={runResult} />
      </div>
    )}
    {runError && (
      <ApiErrorAlert error={runError} className="mt-2 max-w-4xl" />
    )}
    <p className="mt-2 text-xs text-muted-text">
      {isNextDayValidation
        ? '次日验证会将 AI 分析结论与下一个交易日收盘表现进行对比。'
        : '验证周期填 1 时，可查看 AI 分析结论在下一个交易日的验证表现。'}
    </p>
  </header>
);

// ============ BacktestMetricSidebar ============

type BacktestMetricSidebarProps = {
  isLoadingPerf: boolean;
  overallPerf: PerformanceMetrics | null;
  stockPerf: PerformanceMetrics | null;
  codeFilter: string;
};

const BacktestMetricSidebar: React.FC<BacktestMetricSidebarProps> = ({
  isLoadingPerf,
  overallPerf,
  stockPerf,
  codeFilter,
}) => (
  <div className="flex max-h-[38vh] flex-col gap-3 overflow-y-auto lg:max-h-none lg:w-60 lg:flex-shrink-0">
    {isLoadingPerf ? (
      <div className="flex items-center justify-center py-8">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
      </div>
    ) : overallPerf ? (
      <PerformanceCard metrics={overallPerf} title="总体表现" />
    ) : (
      <EmptyState
        title="暂无指标"
        description="点击「开始验证」后，将生成整体历史验证表现指标。"
        className="h-full min-h-[12rem] border-dashed bg-card/45 shadow-none"
      />
    )}
    {stockPerf && (
      <PerformanceCard metrics={stockPerf} title={`${stockPerf.code || codeFilter}`} />
    )}
  </div>
);

// ============ BacktestResultsTable ============

type BacktestResultsTableProps = {
  pageError: ParsedApiError | null;
  isLoadingResults: boolean;
  results: BacktestResultItem[];
  totalResults: number;
  currentPage: number;
  totalPages: number;
  isNextDayValidation: boolean;
  showNextDayActualColumns: boolean;
  codeFilter: string;
  evalDays: string;
  analysisDateFrom: string;
  analysisDateTo: string;
  onPageChange: (page: number) => void;
};

const BacktestResultsTable: React.FC<BacktestResultsTableProps> = ({
  pageError,
  isLoadingResults,
  results,
  totalResults,
  currentPage,
  totalPages,
  isNextDayValidation,
  showNextDayActualColumns,
  codeFilter,
  evalDays,
  analysisDateFrom,
  analysisDateTo,
  onPageChange,
}) => (
  <section className="min-h-0 flex-1 overflow-y-auto">
    {pageError ? (
      <ApiErrorAlert error={pageError} className="mb-3" />
    ) : null}
    {isLoadingResults ? (
      <div className="flex flex-col items-center justify-center h-64">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
        <p className="mt-3 text-secondary-text text-sm">正在加载验证结果...</p>
      </div>
    ) : results.length === 0 ? (
      <EmptyState
        title="暂无验证结果"
        description="点击「开始验证」后，可查看历史分析与后续真实行情的对比结果。"
        className="border-dashed"
        icon={(
          <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
        )}
      />
    ) : (
      <div className="animate-fade-in">
        <div className="backtest-table-toolbar">
          <div className="backtest-table-toolbar-meta">
            <span className="label-uppercase">{isNextDayValidation ? '次日验证' : '验证结果'}</span>
            <span className="text-xs text-secondary-text">
              {codeFilter.trim() ? `筛选股票：${codeFilter.trim()}` : '全部股票'}
              {evalDays ? ` · ${evalDays} 日验证周期` : ''}
              {analysisDateFrom ? ` · 开始 ${analysisDateFrom}` : ''}
              {analysisDateTo ? ` · 结束 ${analysisDateTo}` : ''}
            </span>
          </div>
          <span className="backtest-table-scroll-hint">小屏幕可横向滚动查看完整表格</span>
        </div>
        <div className="backtest-table-wrapper">
          <table className="backtest-table min-w-[840px] w-full text-sm">
            <thead className="backtest-table-head">
              <tr className="text-left">
                <th className="backtest-table-head-cell">股票</th>
                <th className="backtest-table-head-cell">分析日期</th>
                <th className="backtest-table-head-cell">AI 分析结论</th>
                <th className="backtest-table-head-cell">
                  {showNextDayActualColumns ? '实际表现' : '周期涨跌幅'}
                </th>
                <th className="backtest-table-head-cell">
                  {showNextDayActualColumns ? '是否命中' : '方向匹配'}
                </th>
                <th className="backtest-table-head-cell">结果</th>
                <th className="backtest-table-head-cell">状态</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row) => (
                <tr key={row.analysisHistoryId} className="backtest-table-row">
                  <td className="backtest-table-cell backtest-table-code">
                    <div className="flex flex-col">
                      <span>{row.code}</span>
                      <span className="text-xs text-muted-text">{row.stockName || '--'}</span>
                    </div>
                  </td>
                  <td className="backtest-table-cell text-secondary-text">{row.analysisDate || '--'}</td>
                  <td className="backtest-table-cell max-w-[220px] text-foreground">
                    {(row.trendPrediction || row.operationAdvice) ? (
                      <Tooltip
                        content={[row.trendPrediction, row.operationAdvice].filter(Boolean).join(' / ')}
                        focusable
                      >
                        <div className="flex flex-col gap-1">
                          <span className="block truncate">{row.trendPrediction || '--'}</span>
                          <span className="block truncate text-xs text-secondary-text">{row.operationAdvice || '--'}</span>
                        </div>
                      </Tooltip>
                    ) : '--'}
                  </td>
                  <td className="backtest-table-cell">
                    <div className="flex items-center gap-2">
                      {actualMovementBadge(row.actualMovement)}
                      <span className={
                        row.actualReturnPct != null
                          ? row.actualReturnPct > 0 ? 'text-success' : row.actualReturnPct < 0 ? 'text-danger' : 'text-secondary-text'
                          : 'text-muted-text'
                      }>
                        {pct(row.actualReturnPct)}
                      </span>
                    </div>
                  </td>
                  <td className="backtest-table-cell">
                    <span className="flex items-center gap-2">
                      {boolIcon(row.directionCorrect)}
                      <span className="text-muted-text">{directionExpectedLabel(row.directionExpected)}</span>
                    </span>
                  </td>
                  <td className="backtest-table-cell">{outcomeBadge(row.outcome)}</td>
                  <td className="backtest-table-cell">{statusBadge(row.evalStatus)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-4">
          <Pagination
            currentPage={currentPage}
            totalPages={totalPages}
            onPageChange={onPageChange}
          />
        </div>
        <p className="text-xs text-muted-text text-center mt-2">
          共 {totalResults} 条结果 · 第 {currentPage} / {Math.max(totalPages, 1)} 页
        </p>
      </div>
    )}
  </section>
);

// ============ Main Page ============

const BacktestPage: React.FC = () => {
  // Set page title
  useEffect(() => {
    document.title = '策略回测 - DSA';
  }, []);

  // Defaults: 10-day eval window, last 30 days analysis range
  const defaultDates = (() => {
    const toIso = (d: Date) => {
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${y}-${m}-${day}`;
    };
    const today = new Date();
    const from = new Date();
    from.setDate(today.getDate() - 30);
    return { from: toIso(from), to: toIso(today) };
  })();
  const DEFAULT_EVAL_DAYS = '10';

  // Input state
  const [codeFilter, setCodeFilter] = useState('');
  const [analysisDateFrom, setAnalysisDateFrom] = useState(defaultDates.from);
  const [analysisDateTo, setAnalysisDateTo] = useState(defaultDates.to);
  const [evalDays, setEvalDays] = useState(DEFAULT_EVAL_DAYS);
  const [forceRerun, setForceRerun] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [runResult, setRunResult] = useState<BacktestRunResponse | null>(null);
  const [runError, setRunError] = useState<ParsedApiError | null>(null);
  const [pageError, setPageError] = useState<ParsedApiError | null>(null);

  // Results state
  const [results, setResults] = useState<BacktestResultItem[]>([]);
  const [totalResults, setTotalResults] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [isLoadingResults, setIsLoadingResults] = useState(false);
  const pageSize = 20;

  // Performance state
  const [overallPerf, setOverallPerf] = useState<PerformanceMetrics | null>(null);
  const [stockPerf, setStockPerf] = useState<PerformanceMetrics | null>(null);
  const [isLoadingPerf, setIsLoadingPerf] = useState(false);
  const effectiveWindowDays = evalDays ? parseInt(evalDays, 10) : overallPerf?.evalWindowDays;
  const isNextDayValidation = effectiveWindowDays === 1;
  const showNextDayActualColumns = isNextDayValidation;

  // Fetch results
  const fetchResults = useCallback(async (
    page = 1,
    code?: string,
    windowDays?: number,
    startDate?: string,
    endDate?: string,
  ) => {
    setIsLoadingResults(true);
    try {
      const response = await backtestApi.getResults({
        code: code || undefined,
        evalWindowDays: windowDays,
        analysisDateFrom: startDate || undefined,
        analysisDateTo: endDate || undefined,
        page,
        limit: pageSize,
      });
      setResults(response.items);
      setTotalResults(response.total);
      setCurrentPage(response.page);
      setPageError(null);
    } catch (err) {
      console.error('Failed to fetch backtest results:', err);
      setPageError(getParsedApiError(err));
    } finally {
      setIsLoadingResults(false);
    }
  }, []);

  // Fetch performance
  const fetchPerformance = useCallback(async (
    code?: string,
    windowDays?: number,
    startDate?: string,
    endDate?: string,
  ) => {
    setIsLoadingPerf(true);
    try {
      const overall = await backtestApi.getOverallPerformance({
        evalWindowDays: windowDays,
        analysisDateFrom: startDate || undefined,
        analysisDateTo: endDate || undefined,
      });
      setOverallPerf(overall);

      if (code) {
        const stock = await backtestApi.getStockPerformance(code, {
          evalWindowDays: windowDays,
          analysisDateFrom: startDate || undefined,
          analysisDateTo: endDate || undefined,
        });
        setStockPerf(stock);
      } else {
        setStockPerf(null);
      }
      setPageError(null);
    } catch (err) {
      console.error('Failed to fetch performance:', err);
      setPageError(getParsedApiError(err));
    } finally {
      setIsLoadingPerf(false);
    }
  }, []);

  // Initial load — use defaults for eval window and date range
  useEffect(() => {
    const init = async () => {
      const windowDays = parseInt(DEFAULT_EVAL_DAYS, 10);
      fetchPerformance(undefined, windowDays, defaultDates.from, defaultDates.to);
      fetchResults(1, undefined, windowDays, defaultDates.from, defaultDates.to);
    };
    init();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Run backtest
  const handleRun = async () => {
    setIsRunning(true);
    setRunResult(null);
    setRunError(null);
    try {
      const code = codeFilter.trim() || undefined;
      const evalWindowDays = evalDays ? parseInt(evalDays, 10) : undefined;
      const response = await backtestApi.run({
        code,
        force: forceRerun || undefined,
        minAgeDays: forceRerun ? 0 : undefined,
        evalWindowDays,
      });
      setRunResult(response);
      // Refresh data with same eval_window_days
      fetchResults(1, codeFilter.trim() || undefined, evalWindowDays, analysisDateFrom, analysisDateTo);
      fetchPerformance(codeFilter.trim() || undefined, evalWindowDays, analysisDateFrom, analysisDateTo);
    } catch (err) {
      setRunError(getParsedApiError(err));
    } finally {
      setIsRunning(false);
    }
  };

  // Filter by code
  const handleFilter = () => {
    const code = codeFilter.trim() || undefined;
    const windowDays = evalDays ? parseInt(evalDays, 10) : undefined;
    setCurrentPage(1);
    fetchResults(1, code, windowDays, analysisDateFrom, analysisDateTo);
    fetchPerformance(code, windowDays, analysisDateFrom, analysisDateTo);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleFilter();
    }
  };

  // Pagination
  const totalPages = Math.ceil(totalResults / pageSize);
  const handlePageChange = (page: number) => {
    const windowDays = evalDays ? parseInt(evalDays, 10) : undefined;
    fetchResults(page, codeFilter.trim() || undefined, windowDays, analysisDateFrom, analysisDateTo);
  };

  return (
    <main className="workspace-page-layout flex flex-col min-h-[calc(100vh-2rem)] !p-0">
      <div className="flex-shrink-0 border-b border-white/5 bg-primary/5 px-3 py-2 sm:px-4">
        <div className="flex max-w-5xl items-start gap-2 text-xs text-secondary-text">
          <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-primary" />
          <p className="leading-relaxed">
            <span className="font-medium text-foreground">策略回测</span>：用历史真实行情来验证 AI 过往分析结论是否准确。设置条件后点「开始验证」，左侧会汇总命中率等整体指标，右侧逐条展示 AI 当时的结论与实际表现对比。
          </p>
        </div>
      </div>
      <BacktestConfigBar
        codeFilter={codeFilter}
        analysisDateFrom={analysisDateFrom}
        analysisDateTo={analysisDateTo}
        evalDays={evalDays}
        forceRerun={forceRerun}
        isRunning={isRunning}
        isLoadingResults={isLoadingResults}
        isNextDayValidation={isNextDayValidation}
        runResult={runResult}
        runError={runError}
        onCodeFilterChange={setCodeFilter}
        onAnalysisDateFromChange={setAnalysisDateFrom}
        onAnalysisDateToChange={setAnalysisDateTo}
        onEvalDaysChange={setEvalDays}
        onForceRerunToggle={() => setForceRerun((prev) => !prev)}
        onFilter={handleFilter}
        onRun={handleRun}
        onKeyDown={handleKeyDown}
      />

      {/* Main content */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 lg:flex-row">
        <BacktestMetricSidebar
          isLoadingPerf={isLoadingPerf}
          overallPerf={overallPerf}
          stockPerf={stockPerf}
          codeFilter={codeFilter}
        />
        <BacktestResultsTable
          pageError={pageError}
          isLoadingResults={isLoadingResults}
          results={results}
          totalResults={totalResults}
          currentPage={currentPage}
          totalPages={totalPages}
          isNextDayValidation={isNextDayValidation}
          showNextDayActualColumns={showNextDayActualColumns}
          codeFilter={codeFilter}
          evalDays={evalDays}
          analysisDateFrom={analysisDateFrom}
          analysisDateTo={analysisDateTo}
          onPageChange={handlePageChange}
        />
      </div>
    </main>
  );
};

export default BacktestPage;
