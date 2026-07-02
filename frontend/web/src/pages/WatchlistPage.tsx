import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BarChart3, CheckSquare, Loader2, Plus, Star, Trash2 } from 'lucide-react';
import { analysisApi } from '../api/analysis';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { Button, Card, Checkbox } from '../components/common';
import { SettingsAlert } from '../components/settings';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { accountApi, type WatchlistItem } from '../api/account';
import { getParsedApiError } from '../api/error';
import { useAuth } from '../hooks';
import { useStockPoolStore } from '../stores';
import type { AnalyzeAsyncResponse, TaskInfo } from '../types/analysis';

type WatchlistNotice = {
  title: string;
  message: string;
  variant: 'success' | 'warning';
};

function buildWatchlistTasks(
  response: AnalyzeAsyncResponse,
  stocksByCode: Map<string, WatchlistItem>,
  fallbackCodes: string[],
): TaskInfo[] {
  const createdAt = new Date().toISOString();

  if ('taskId' in response) {
    const stockCode = fallbackCodes[0] || '';
    const stock = stocksByCode.get(stockCode);
    return [{
      taskId: response.taskId,
      stockCode,
      stockName: stock?.stockName ?? undefined,
      status: response.status,
      progress: 0,
      message: response.message,
      reportType: 'detailed',
      createdAt,
      originalQuery: stockCode,
      selectionSource: 'manual',
    }];
  }

  return response.accepted.map((accepted) => {
    const stock = stocksByCode.get(accepted.stockCode);
    const task: TaskInfo = {
      taskId: accepted.taskId,
      stockCode: accepted.stockCode,
      stockName: stock?.stockName ?? undefined,
      status: accepted.status,
      progress: 0,
      message: accepted.message,
      reportType: 'detailed',
      createdAt,
      originalQuery: accepted.stockCode,
      selectionSource: 'manual',
    };
    return task;
  });
}

function summarizeAnalyzeResponse(response: AnalyzeAsyncResponse): WatchlistNotice {
  if ('taskId' in response) {
    return {
      title: '已提交分析',
      message: '已创建 1 个分析任务，可在任务面板查看进度。',
      variant: 'success',
    };
  }

  const acceptedCount = response.accepted.length;
  const duplicateCount = response.duplicates.length;
  if (acceptedCount > 0 && duplicateCount > 0) {
    return {
      title: '部分任务已提交',
      message: `已创建 ${acceptedCount} 个分析任务，${duplicateCount} 只股票正在分析中。`,
      variant: 'warning',
    };
  }
  if (acceptedCount > 0) {
    return {
      title: '已提交分析',
      message: `已创建 ${acceptedCount} 个分析任务，可在任务面板查看进度。`,
      variant: 'success',
    };
  }
  return {
    title: '无需重复提交',
    message: `${duplicateCount} 只股票正在分析中，请等待现有任务完成。`,
    variant: 'warning',
  };
}

const WatchlistPage: React.FC = () => {
  const { userMode } = useAuth();
  const syncTaskCreated = useStockPoolStore((state) => state.syncTaskCreated);
  const plan = userMode?.plan ?? null;

  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [maxStocks, setMaxStocks] = useState<number>(3);
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [watchlistError, setWatchlistError] = useState<string | null>(null);
  const [analysisNotice, setAnalysisNotice] = useState<WatchlistNotice | null>(null);
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  const [analyzingCodes, setAnalyzingCodes] = useState<string[]>([]);
  const [addInput, setAddInput] = useState('');
  const [isAdding, setIsAdding] = useState(false);

  useEffect(() => {
    document.title = '我的自选股 - AlphaLens';
  }, []);

  const loadWatchlist = useCallback(async () => {
    setWatchlistLoading(true);
    setWatchlistError(null);
    try {
      const res = await accountApi.getWatchlist();
      setWatchlist(res.stocks);
      setMaxStocks(res.maxStocks);
      setSelectedCodes((prev) => prev.filter((code) => res.stocks.some((stock) => stock.stockCode === code)));
    } catch (err) {
      setWatchlistError(getParsedApiError(err).message);
    } finally {
      setWatchlistLoading(false);
    }
  }, []);

  useEffect(() => {
    if (userMode?.loggedIn) {
      void loadWatchlist();
    }
  }, [userMode?.loggedIn, loadWatchlist]);

  const handleAddStock = useCallback(async (code: string, name?: string) => {
    if (!code.trim()) return;
    setIsAdding(true);
    setWatchlistError(null);
    try {
      const res = await accountApi.addWatchlistStock({ stockCode: code.trim(), stockName: name });
      setWatchlist((prev) => {
        if (prev.some((s) => s.stockCode === res.stock.stockCode)) return prev;
        return [...prev, res.stock];
      });
      setAddInput('');
    } catch (err) {
      setWatchlistError(getParsedApiError(err).message);
    } finally {
      setIsAdding(false);
    }
  }, []);

  const handleRemoveStock = useCallback(async (stockCode: string) => {
    setWatchlistError(null);
    try {
      await accountApi.removeWatchlistStock(stockCode);
      setWatchlist((prev) => prev.filter((s) => s.stockCode !== stockCode));
      setSelectedCodes((prev) => prev.filter((code) => code !== stockCode));
    } catch (err) {
      setWatchlistError(getParsedApiError(err).message);
    }
  }, []);

  const toggleStockSelection = useCallback((stockCode: string) => {
    setSelectedCodes((prev) => (
      prev.includes(stockCode)
        ? prev.filter((code) => code !== stockCode)
        : [...prev, stockCode]
    ));
  }, []);

  const handleToggleAll = useCallback(() => {
    setSelectedCodes((prev) => (
      prev.length === watchlist.length ? [] : watchlist.map((item) => item.stockCode)
    ));
  }, [watchlist]);

  const handleAnalyzeStocks = useCallback(async (stockCodes: string[]) => {
    const codes = Array.from(new Set(stockCodes.filter(Boolean)));
    if (codes.length === 0) return;

    setAnalyzingCodes(codes);
    setWatchlistError(null);
    setAnalysisNotice(null);
    try {
      const response = await analysisApi.analyzeAsync({
        stockCodes: codes,
        reportType: 'detailed',
        originalQuery: codes.join(','),
        selectionSource: 'manual',
      });
      const stocksByCode = new Map(watchlist.map((item) => [item.stockCode, item]));
      buildWatchlistTasks(response, stocksByCode, codes).forEach(syncTaskCreated);
      setAnalysisNotice(summarizeAnalyzeResponse(response));
    } catch (err) {
      setWatchlistError(getParsedApiError(err).message);
    } finally {
      setAnalyzingCodes([]);
    }
  }, [syncTaskCreated, watchlist]);

  const selectedCodeSet = new Set(selectedCodes);
  const bulkAnalyzeCodes = selectedCodes.length > 0 ? selectedCodes : watchlist.map((item) => item.stockCode);
  const isAnalyzing = analyzingCodes.length > 0;

  if (userMode == null || !userMode.userModeEnabled) {
    return (
      <StandardPageLayout>
        <Card title="我的自选股" subtitle="WATCHLIST">
          <p className="text-sm text-secondary-text">当前实例未启用用户模式。</p>
        </Card>
      </StandardPageLayout>
    );
  }

  if (!userMode.loggedIn) {
    return (
      <StandardPageLayout>
        <Card title="我的自选股" subtitle="WATCHLIST">
          <p className="text-sm text-secondary-text">请先登录后查看自选股。</p>
          <div className="mt-4">
            <Link to="/login">
              <Button variant="primary">前往登录</Button>
            </Link>
          </div>
        </Card>
      </StandardPageLayout>
    );
  }

  return (
    <StandardPageLayout>
      <div className="space-y-1">
        <p className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-text">
          WATCHLIST
        </p>
        <h1 className="text-2xl font-bold tracking-tight text-foreground">我的自选股</h1>
        <p className="text-sm text-secondary-text/80">
          管理你关注的股票，系统将在每日推送中自动分析自选股行情。
        </p>
      </div>

      <Card title="自选股列表" subtitle="STOCKS">
        {watchlistLoading ? (
          <div className="flex items-center gap-2 text-secondary-text text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : (
          <div className="space-y-4">
            {watchlistError && (
              <SettingsAlert title="操作失败" message={watchlistError} variant="error" />
            )}
            {analysisNotice && (
              <SettingsAlert
                title={analysisNotice.title}
                message={analysisNotice.message}
                variant={analysisNotice.variant}
              />
            )}

            {watchlist.length === 0 ? (
              <p className="text-sm text-secondary-text">暂无自选股，在下方添加你关注的股票。</p>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/60 bg-card/40 px-3 py-2">
                  <Checkbox
                    checked={selectedCodes.length === watchlist.length}
                    onChange={handleToggleAll}
                    label={selectedCodes.length === watchlist.length ? '取消全选' : '全选'}
                    disabled={isAnalyzing}
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-secondary-text">
                      已选 {selectedCodes.length}/{watchlist.length}
                    </span>
                    <Button
                      variant="primary"
                      size="sm"
                      isLoading={isAnalyzing}
                      loadingText="提交中..."
                      disabled={bulkAnalyzeCodes.length === 0}
                      onClick={() => void handleAnalyzeStocks(bulkAnalyzeCodes)}
                    >
                      <CheckSquare className="h-4 w-4" />
                      {selectedCodes.length > 0 ? '分析已选' : '分析全部'}
                    </Button>
                  </div>
                </div>

                <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {watchlist.map((item) => {
                    const selected = selectedCodeSet.has(item.stockCode);
                    const itemAnalyzing = analyzingCodes.includes(item.stockCode);
                    return (
                      <li
                        key={item.stockCode}
                        className="flex min-h-12 items-center gap-2 rounded-lg border border-border/60 bg-card/60 px-3 py-2 text-sm"
                      >
                        <Checkbox
                          checked={selected}
                          onChange={() => toggleStockSelection(item.stockCode)}
                          aria-label={`选择 ${item.stockCode}`}
                          disabled={isAnalyzing}
                          containerClassName="gap-0"
                        />
                        <Star className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                        <div className="min-w-0 flex-1">
                          <Link
                            to={`/stocks/${encodeURIComponent(item.stockCode)}`}
                            className="block truncate font-medium text-foreground transition-colors hover:text-primary"
                          >
                            {item.stockCode}
                          </Link>
                          {item.stockName && (
                            <span className="block truncate text-xs text-secondary-text">{item.stockName}</span>
                          )}
                        </div>
                        <button
                          type="button"
                          className="text-secondary-text transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
                          onClick={() => void handleAnalyzeStocks([item.stockCode])}
                          title="分析"
                          disabled={isAnalyzing}
                        >
                          {itemAnalyzing ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <BarChart3 className="h-3.5 w-3.5" />
                          )}
                        </button>
                        <button
                          type="button"
                          className="text-secondary-text hover:text-red-400 transition-colors"
                          onClick={() => void handleRemoveStock(item.stockCode)}
                          title="删除"
                          disabled={isAnalyzing}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            {watchlist.length < maxStocks ? (
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <p className="mb-1.5 text-xs text-secondary-text">
                    添加自选股（{watchlist.length}/{maxStocks}）
                  </p>
                  <StockAutocomplete
                    value={addInput}
                    onChange={setAddInput}
                    onSubmit={(code, name) => void handleAddStock(code, name)}
                    disabled={isAdding}
                    placeholder="输入股票代码或名称"
                  />
                </div>
                <Button
                  variant="primary"
                  isLoading={isAdding}
                  onClick={() => void handleAddStock(addInput)}
                  disabled={!addInput.trim() || isAdding}
                >
                  <Plus className="h-4 w-4" /> 添加
                </Button>
              </div>
            ) : (
              <div className="rounded-lg border border-amber-400/20 bg-amber-500/5 px-3 py-2 text-sm text-amber-300">
                已达到当前套餐自选股上限（{maxStocks} 只）。
                {!plan?.isPro && (
                  <Link to="/billing" className="ml-1 underline hover:text-amber-200">
                    升级套餐解锁更多
                  </Link>
                )}
              </div>
            )}
          </div>
        )}
      </Card>
    </StandardPageLayout>
  );
};

export default WatchlistPage;
