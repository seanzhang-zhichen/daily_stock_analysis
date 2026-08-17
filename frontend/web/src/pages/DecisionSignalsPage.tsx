import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, RefreshCw, Search, SlidersHorizontal } from 'lucide-react';
import { decisionSignalsApi } from '../api/decisionSignals';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import {
  ApiErrorAlert,
  Button,
  ConfirmDialog,
  Drawer,
  EmptyState,
  Pagination,
} from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import {
  ACTION_LABELS,
  DecisionSignalCard,
  DecisionSignalDetails,
  MARKET_LABELS,
  STATUS_LABELS,
} from '../components/decision-signals/DecisionSignalDisplay';
import { StockAutocomplete } from '../components/StockAutocomplete';
import type {
  DecisionSignalItem,
} from '../types/decisionSignals';

const PAGE_SIZE = 20;
const INPUT_CLASS = 'ui-input h-11 w-full px-4 text-sm';
const SELECT_CLASS = `${INPUT_CLASS} appearance-none pr-9`;

type Filters = {
  stockCode: string;
  market: string;
  action: string;
  status: string;
};

const DEFAULT_FILTERS: Filters = {
  stockCode: '',
  market: '',
  action: '',
  status: 'active',
};

type PendingStatus = {
  item: DecisionSignalItem;
  status: 'closed' | 'archived';
} | null;

const DecisionSignalsPage: React.FC = () => {
  const [draft, setDraft] = useState<Filters>(DEFAULT_FILTERS);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [items, setItems] = useState<DecisionSignalItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<DecisionSignalItem | null>(null);
  const [pendingStatus, setPendingStatus] = useState<PendingStatus>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSyncing, setIsSyncing] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  useEffect(() => {
    document.title = 'AI 建议 - AlphaLens';
  }, []);

  const loadSignals = useCallback(async (targetPage = page) => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await decisionSignalsApi.list({
        stockCode: filters.stockCode.trim() || undefined,
        market: filters.market || undefined,
        action: filters.action || undefined,
        status: filters.status,
        page: targetPage,
        pageSize: PAGE_SIZE,
      });
      setItems(result.items);
      setTotal(result.total);
      setPage(result.page);
      setSelected((current) => {
        if (!current) return null;
        return result.items.find((item) => item.id === current.id) || current;
      });
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    void loadSignals(page);
  }, [filters, page, loadSignals]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const activeCount = useMemo(() => items.filter((item) => item.status === 'active').length, [items]);
  const defensiveCount = useMemo(
    () => items.filter((item) => ['reduce', 'sell', 'avoid', 'alert'].includes(item.action)).length,
    [items],
  );

  const applyFilters = (event?: React.FormEvent) => {
    event?.preventDefault();
    setPage(1);
    setFilters({ ...draft, stockCode: draft.stockCode.trim().toUpperCase() });
  };

  const resetFilters = () => {
    setDraft(DEFAULT_FILTERS);
    setPage(1);
    setFilters(DEFAULT_FILTERS);
  };

  const syncHistory = async () => {
    setIsSyncing(true);
    setError(null);
    try {
      await decisionSignalsApi.sync();
      await loadSignals(1);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSyncing(false);
    }
  };

  const confirmStatusChange = async () => {
    if (!pendingStatus) return;
    setIsUpdating(true);
    setError(null);
    try {
      const updated = await decisionSignalsApi.updateStatus(pendingStatus.item.id, pendingStatus.status);
      setItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setSelected((current) => (current?.id === updated.id ? updated : current));
      setPendingStatus(null);
      if (filters.status === 'active') {
        await loadSignals(page);
      }
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <StandardPageLayout>
      <header className="ui-page-header">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-text">DECISION SIGNALS</p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-foreground">AI 建议</h1>
            <p className="mt-1 max-w-2xl text-sm text-secondary-text/80">
              将历史分析中的操作建议、价格计划和风险条件沉淀为可追踪信号。
            </p>
          </div>
          <Button variant="outline" onClick={() => void syncHistory()} isLoading={isSyncing} loadingText="同步中">
            <RefreshCw className="h-4 w-4" />
            同步历史分析
          </Button>
        </div>
      </header>

      {error ? <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void loadSignals(page)} /> : null}

      <section className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border/50 bg-border/50 md:grid-cols-4" aria-label="建议概览">
        {[
          ['当前结果', `${total}`],
          ['本页有效', `${activeCount}`],
          ['本页防守建议', `${defensiveCount}`],
          ['每页显示', `${PAGE_SIZE}`],
        ].map(([label, value]) => (
          <div key={label} className="bg-elevated px-4 py-3">
            <p className="text-xs text-muted-text">{label}</p>
            <p className="mt-1 font-mono text-xl font-semibold tabular-nums text-foreground">{value}</p>
          </div>
        ))}
      </section>

      <form className="border-y border-border/50 py-4" onSubmit={applyFilters}>
        <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-muted-text">
          <SlidersHorizontal className="h-4 w-4" />
          筛选条件
        </div>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(240px,1.35fr)_150px_150px_150px_auto]">
          <StockAutocomplete
            value={draft.stockCode}
            onChange={(value) => setDraft((current) => ({ ...current, stockCode: value }))}
            onSubmit={(code) => {
              const next = { ...draft, stockCode: code };
              setDraft(next);
              setPage(1);
              setFilters(next);
            }}
            placeholder="股票代码或名称"
          />
          <select
            aria-label="市场"
            className={SELECT_CLASS}
            value={draft.market}
            onChange={(event) => setDraft((current) => ({ ...current, market: event.target.value }))}
          >
            <option value="">全部市场</option>
            {Object.entries(MARKET_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select
            aria-label="建议动作"
            className={SELECT_CLASS}
            value={draft.action}
            onChange={(event) => setDraft((current) => ({ ...current, action: event.target.value }))}
          >
            <option value="">全部动作</option>
            {Object.entries(ACTION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select
            aria-label="建议状态"
            className={SELECT_CLASS}
            value={draft.status}
            onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}
          >
            <option value="">全部状态</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <div className="flex gap-2">
            <Button type="submit" className="flex-1 lg:flex-none">
              <Search className="h-4 w-4" />
              查询
            </Button>
            <Button variant="ghost" onClick={resetFilters}>重置</Button>
          </div>
        </div>
      </form>

      <section aria-label="AI 建议列表">
        {isLoading ? (
          <div className="flex min-h-64 items-center justify-center text-sm text-secondary-text">
            <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
            正在读取 AI 建议
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Activity className="h-6 w-6" />}
            title="暂无 AI 建议"
            description="完成一次个股分析后，建议会自动从分析历史同步到这里。"
            action={(
              <Button variant="outline" onClick={() => void syncHistory()}>
                <RefreshCw className="h-4 w-4" />
                重新同步
              </Button>
            )}
          />
        ) : (
          <div className="space-y-3">
            {items.map((item) => <DecisionSignalCard key={item.id} item={item} onOpen={setSelected} />)}
          </div>
        )}
        <Pagination
          currentPage={page}
          totalPages={totalPages}
          onPageChange={(nextPage) => setPage(nextPage)}
          className="mt-5"
        />
      </section>

      <Drawer
        isOpen={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected ? `${selected.stockName || selected.stockCode} AI 建议` : 'AI 建议'}
      >
        {selected ? (
          <DecisionSignalDetails
            item={selected}
            isUpdating={isUpdating}
            onStatusChange={(status) => setPendingStatus({ item: selected, status })}
          />
        ) : null}
      </Drawer>

      <ConfirmDialog
        isOpen={Boolean(pendingStatus)}
        title={pendingStatus?.status === 'archived' ? '归档 AI 建议' : '关闭 AI 建议'}
        message={pendingStatus?.status === 'archived'
          ? '归档后，该建议不再出现在默认有效列表中。'
          : '关闭后，该建议不再作为当前有效建议展示。'}
        confirmText={pendingStatus?.status === 'archived' ? '确认归档' : '确认关闭'}
        cancelText="取消"
        onConfirm={() => void confirmStatusChange()}
        onCancel={() => setPendingStatus(null)}
      />
    </StandardPageLayout>
  );
};

export default DecisionSignalsPage;
