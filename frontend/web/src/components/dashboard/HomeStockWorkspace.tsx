import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Loader2, Play, Plus, RefreshCw, Star, Trash2 } from 'lucide-react';
import { accountApi, type WatchlistItem } from '../../api/account';
import { analysisApi } from '../../api/analysis';
import { getParsedApiError } from '../../api/error';
import type { AnalyzeAsyncResponse, HistoryItem, TaskInfo } from '../../types/analysis';
import { getTodayInShanghai } from '../../utils/format';
import { Button, InlineAlert } from '../common';
import { HistoryList } from '../history';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

type WorkspaceTab = 'history' | 'watchlist' | 'today';

interface Props {
  historyItems: HistoryItem[];
  isLoadingHistory: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  selectedId?: number;
  selectedIds: Set<number>;
  isDeletingHistory: boolean;
  activeTasks: TaskInfo[];
  onHistoryItemClick: (recordId: number) => void;
  onLoadMore: () => void;
  onToggleItemSelection: (recordId: number) => void;
  onToggleSelectAll: (recordIds: number[]) => void;
  onDeleteSelected: () => void;
  onTaskCreated: (task: TaskInfo) => void;
}

const isAShareCode = (value: string): boolean => /^(?:(?:sh|sz|bj)\d{6}|\d{6}(?:\.(?:sh|sz|bj))?)$/i.test(value.trim());

const isCreatedToday = (value?: string): boolean => {
  if (!value) return false;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return false;
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(parsed) === getTodayInShanghai();
};

function buildTasks(response: AnalyzeAsyncResponse, stocks: Map<string, WatchlistItem>, fallbackCodes: string[]): TaskInfo[] {
  const createdAt = new Date().toISOString();
  if ('taskId' in response) {
    const stockCode = fallbackCodes[0] || '';
    return [{
      taskId: response.taskId,
      stockCode,
      stockName: stocks.get(stockCode)?.stockName ?? undefined,
      status: response.status,
      progress: 0,
      message: response.message,
      reportType: 'detailed',
      createdAt,
      originalQuery: stockCode,
      selectionSource: 'manual',
    }];
  }
  return response.accepted.map((accepted) => ({
    taskId: accepted.taskId,
    stockCode: accepted.stockCode,
    stockName: stocks.get(accepted.stockCode)?.stockName ?? undefined,
    status: accepted.status,
    progress: 0,
    message: accepted.message,
    reportType: 'detailed',
    createdAt,
    originalQuery: accepted.stockCode,
    selectionSource: 'manual',
  }));
}

export function HomeStockWorkspace({
  historyItems, isLoadingHistory, isLoadingMore, hasMore, selectedId, selectedIds,
  isDeletingHistory, activeTasks, onHistoryItemClick, onLoadMore, onToggleItemSelection,
  onToggleSelectAll, onDeleteSelected, onTaskCreated,
}: Props) {
  const { t } = useUiLanguage();
  const [tab, setTab] = useState<WorkspaceTab>('history');
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [actioning, setActioning] = useState(false);
  const [draftCode, setDraftCode] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadWatchlist = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await accountApi.getWatchlist();
      setWatchlist(result.stocks.filter((item) => isAShareCode(item.stockCode)));
    } catch (cause) {
      setError(getParsedApiError(cause).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab === 'watchlist' || tab === 'today') void loadWatchlist();
  }, [loadWatchlist, tab]);

  const latestByCode = useMemo(() => {
    const result = new Map<string, HistoryItem>();
    historyItems.filter((item) => item.reportType !== 'market_review' && isCreatedToday(item.createdAt)).forEach((item) => {
      if (!result.has(item.stockCode)) result.set(item.stockCode, item);
    });
    return result;
  }, [historyItems]);
  const analyzedCodes = useMemo(() => new Set(Array.from(latestByCode.keys()).map((code) => code.toUpperCase())), [latestByCode]);
  const pendingStocks = useMemo(
    () => watchlist.filter((item) => !analyzedCodes.has(item.stockCode.toUpperCase())),
    [analyzedCodes, watchlist],
  );

  const addStock = useCallback(async () => {
    const code = draftCode.trim();
    if (!code || !isAShareCode(code)) {
      setError(t('workspace.invalidCode'));
      return;
    }
    setActioning(true);
    setError(null);
    try {
      await accountApi.addWatchlistStock({ stockCode: code });
      setDraftCode('');
      await loadWatchlist();
    } catch (cause) {
      setError(getParsedApiError(cause).message);
    } finally {
      setActioning(false);
    }
  }, [draftCode, loadWatchlist, t]);

  const removeStock = useCallback(async (stockCode: string) => {
    setActioning(true);
    setError(null);
    try {
      await accountApi.removeWatchlistStock(stockCode);
      setWatchlist((items) => items.filter((item) => item.stockCode !== stockCode));
    } catch (cause) {
      setError(getParsedApiError(cause).message);
    } finally {
      setActioning(false);
    }
  }, []);

  const analyzeStocks = useCallback(async (stocks: WatchlistItem[]) => {
    const codes = stocks.map((item) => item.stockCode);
    if (!codes.length) return;
    setActioning(true);
    setError(null);
    setNotice(null);
    try {
      const response = await analysisApi.analyzeAsync({
        stockCodes: codes,
        reportType: 'detailed',
        originalQuery: codes.join(','),
        selectionSource: 'manual',
      });
      const stockMap = new Map(watchlist.map((item) => [item.stockCode, item]));
      buildTasks(response, stockMap, codes).forEach(onTaskCreated);
      const accepted = 'taskId' in response ? 1 : response.accepted.length;
      const duplicates = 'taskId' in response ? 0 : response.duplicates.length;
      setNotice(duplicates
        ? t('workspace.sentWithDuplicates', { accepted, duplicates })
        : t('workspace.sent', { accepted }));
    } catch (cause) {
      setError(getParsedApiError(cause).message);
    } finally {
      setActioning(false);
    }
  }, [onTaskCreated, t, watchlist]);

  return (
    <div className="flex min-h-0 h-full flex-col gap-3 overflow-hidden">
      <div className="grid grid-cols-3 gap-1 rounded-lg border border-subtle bg-surface-muted/40 p-1">
        {([['history', 'workspace.tab.history'], ['watchlist', 'workspace.tab.watchlist'], ['today', 'workspace.tab.today']] as const).map(([key, label]) => (
          <button key={key} type="button" aria-pressed={tab === key} onClick={() => setTab(key)}
            className={`h-8 rounded-md text-xs font-medium ${tab === key ? 'bg-primary/15 text-primary' : 'text-secondary-text hover:bg-hover'}`}>{t(label)}</button>
        ))}
      </div>
      {tab === 'history' ? (
        <HistoryList items={historyItems} isLoading={isLoadingHistory} isLoadingMore={isLoadingMore} hasMore={hasMore}
          selectedId={selectedId} selectedIds={selectedIds} isDeleting={isDeletingHistory} onItemClick={onHistoryItemClick}
          onLoadMore={onLoadMore} onToggleItemSelection={onToggleItemSelection} onToggleSelectAll={onToggleSelectAll}
          onDeleteSelected={onDeleteSelected} className="flex-1 overflow-hidden" />
      ) : (
        <section className="ui-card ui-card-bordered ui-card-padding-sm flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-medium text-foreground">{tab === 'watchlist' ? t('workspace.watchlistTitle') : t('workspace.todayTitle')}</h2>
              <p className="mt-0.5 text-xs text-secondary-text">{t('workspace.coverage', { covered: watchlist.length - pendingStocks.length, total: watchlist.length, pending: pendingStocks.length })}</p>
            </div>
            <Button type="button" variant="outline" size="xsm" aria-label={t('workspace.refreshWatchlist')} onClick={() => void loadWatchlist()} disabled={loading || actioning}>
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
          {error ? <InlineAlert variant="danger" message={error} className="mb-2 rounded-lg px-2 py-1.5 text-xs" /> : null}
          {notice ? <InlineAlert variant="success" message={notice} className="mb-2 rounded-lg px-2 py-1.5 text-xs" /> : null}
          {tab === 'watchlist' ? <>
            <div className="mb-3 grid grid-cols-2 gap-2">
              <Button type="button" size="sm" onClick={() => void analyzeStocks(watchlist)} disabled={!watchlist.length || actioning}><Play className="h-3.5 w-3.5" />{t('workspace.analyzeAll')}</Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => void analyzeStocks(pendingStocks)} disabled={!pendingStocks.length || actioning}><CheckCircle2 className="h-3.5 w-3.5" />{t('workspace.analyzePending')}</Button>
            </div>
            <div className="mb-3 flex gap-2">
              <input value={draftCode} onChange={(event) => setDraftCode(event.target.value)} placeholder={t('workspace.addPlaceholder')} className="ui-input min-w-0 flex-1 py-1.5 text-xs" disabled={actioning} />
              <Button type="button" variant="secondary" size="xsm" aria-label={t('workspace.addWatchlist')} onClick={() => void addStock()} disabled={actioning || !draftCode.trim()}><Plus className="h-3.5 w-3.5" /></Button>
            </div>
          </> : null}
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
            {loading ? <div className="flex items-center gap-2 py-4 text-xs text-secondary-text"><Loader2 className="h-4 w-4 animate-spin" />{t('workspace.loading')}</div> : null}
            {!loading && (tab === 'watchlist' ? watchlist : Array.from(latestByCode.values())).length === 0 ? <p className="py-4 text-sm text-secondary-text">{tab === 'watchlist' ? t('workspace.noWatchlist') : t('workspace.noToday')}</p> : null}
            {!loading && tab === 'watchlist' && watchlist.map((item) => {
              const latest = latestByCode.get(item.stockCode);
              const task = activeTasks.find((entry) => entry.stockCode.toUpperCase() === item.stockCode.toUpperCase());
              return <div key={item.stockCode} className="flex items-center gap-2 rounded-lg border border-subtle px-2.5 py-2 text-sm">
                <Star className="h-3.5 w-3.5 shrink-0 text-amber-400" /><button type="button" className="min-w-0 flex-1 text-left" onClick={() => latest && onHistoryItemClick(latest.id)}><span className="block truncate font-medium">{item.stockName || item.stockCode}</span><span className="block text-xs text-secondary-text">{item.stockCode} · {task ? t('workspace.analyzing') : latest ? t('workspace.sentiment', { score: latest.sentimentScore ?? '-' }) : t('workspace.pending')}</span></button><button type="button" aria-label={t('workspace.deleteStock', { code: item.stockCode })} className="text-secondary-text hover:text-danger" disabled={actioning} onClick={() => void removeStock(item.stockCode)}><Trash2 className="h-3.5 w-3.5" /></button>
              </div>;
            })}
            {!loading && tab === 'today' && Array.from(latestByCode.values()).map((item) => <button key={item.id} type="button" onClick={() => onHistoryItemClick(item.id)} className="flex w-full items-center justify-between rounded-lg border border-subtle px-2.5 py-2 text-left text-sm hover:bg-hover"><span className="min-w-0"><span className="block truncate font-medium">{item.stockName || item.stockCode}</span><span className="text-xs text-secondary-text">{item.stockCode}</span></span><span className="text-primary">{item.sentimentScore ?? '-'} / 100</span></button>)}
          </div>
        </section>
      )}
    </div>
  );
}
