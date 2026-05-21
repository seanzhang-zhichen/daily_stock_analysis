import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Plus, Star, Trash2 } from 'lucide-react';
import { Button, Card } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { accountApi, type WatchlistItem } from '../api/account';
import { getParsedApiError } from '../api/error';
import { useAuth } from '../hooks';

const WatchlistPage: React.FC = () => {
  const { userMode } = useAuth();
  const plan = userMode?.plan ?? null;

  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [maxStocks, setMaxStocks] = useState<number>(3);
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [watchlistError, setWatchlistError] = useState<string | null>(null);
  const [addInput, setAddInput] = useState('');
  const [isAdding, setIsAdding] = useState(false);

  useEffect(() => {
    document.title = '我的自选股 - DSA';
  }, []);

  const loadWatchlist = useCallback(async () => {
    setWatchlistLoading(true);
    setWatchlistError(null);
    try {
      const res = await accountApi.getWatchlist();
      setWatchlist(res.stocks);
      setMaxStocks(res.maxStocks);
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
    } catch (err) {
      setWatchlistError(getParsedApiError(err).message);
    }
  }, []);

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

            {watchlist.length === 0 ? (
              <p className="text-sm text-secondary-text">暂无自选股，在下方添加你关注的股票。</p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {watchlist.map((item) => (
                  <li
                    key={item.stockCode}
                    className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-card/60 px-3 py-1.5 text-sm"
                  >
                    <Star className="h-3.5 w-3.5 text-amber-400" />
                    <span className="font-medium text-foreground">{item.stockCode}</span>
                    {item.stockName && (
                      <span className="text-secondary-text">{item.stockName}</span>
                    )}
                    <button
                      type="button"
                      className="ml-1 text-secondary-text hover:text-red-400 transition-colors"
                      onClick={() => void handleRemoveStock(item.stockCode)}
                      title="删除"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
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
                    升级 Pro 解锁更多
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
