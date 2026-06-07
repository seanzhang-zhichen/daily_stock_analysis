import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ArrowLeft, BarChart3, BellRing, Loader2, MessageSquareQuote, RefreshCw, Star } from 'lucide-react';
import { stocksApi, type KLineData, type StockHistoryResponse, type StockQuote } from '../api/stocks';
import { accountApi } from '../api/account';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { Badge, Button, Card, EmptyState } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import { useAuth } from '../hooks';
import { formatDate, formatDateTime } from '../utils/format';

type ChartPoint = KLineData & {
  label: string;
};

const STAT_LABELS: Array<{ key: keyof StockQuote; label: string; formatter?: (value: unknown) => string }> = [
  { key: 'open', label: '开盘' },
  { key: 'high', label: '最高' },
  { key: 'low', label: '最低' },
  { key: 'prevClose', label: '昨收' },
  { key: 'volume', label: '成交量', formatter: formatCompactNumber },
  { key: 'amount', label: '成交额', formatter: formatCompactMoney },
];

function normalizeStockCode(value?: string): string {
  return decodeURIComponent(value || '').trim();
}

function formatNumber(value: unknown, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSignedPercent(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

function formatSignedNumber(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}`;
}

function formatCompactNumber(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(2)}万`;
  return value.toLocaleString('zh-CN');
}

function formatCompactMoney(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(2)}万`;
  return value.toLocaleString('zh-CN');
}

function getChangeTone(value?: number | null): 'success' | 'danger' | 'default' {
  if (typeof value !== 'number' || value === 0) return 'default';
  return value > 0 ? 'danger' : 'success';
}

const StockDetailPage: React.FC = () => {
  const params = useParams<{ code: string }>();
  const navigate = useNavigate();
  const { userMode } = useAuth();
  const stockCode = normalizeStockCode(params.code);

  const [quote, setQuote] = useState<StockQuote | null>(null);
  const [history, setHistory] = useState<StockHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [watchlistSaving, setWatchlistSaving] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [successMessage, setSuccessMessage] = useState('');

  const displayName = quote?.stockName || history?.stockName || stockCode;
  const userModeEnabled = Boolean(userMode?.userModeEnabled);
  const userLoggedIn = Boolean(userMode?.loggedIn);
  const canUseUserActions = userModeEnabled && userLoggedIn;

  const chartData = useMemo<ChartPoint[]>(() => (
    (history?.data || [])
      .filter((item) => Number.isFinite(item.close))
      .map((item) => ({ ...item, label: formatDate(item.date) }))
  ), [history?.data]);

  const loadStock = useCallback(async () => {
    if (!stockCode) return;
    setLoading(true);
    setError(null);
    try {
      const [nextQuote, nextHistory] = await Promise.all([
        stocksApi.getQuote(stockCode),
        stocksApi.getHistory(stockCode, { period: 'daily', days: 90 }),
      ]);
      setQuote(nextQuote);
      setHistory(nextHistory);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [stockCode]);

  useEffect(() => {
    document.title = `${stockCode || '股票详情'} - DSA`;
  }, [stockCode]);

  useEffect(() => {
    void loadStock();
  }, [loadStock]);

  const handleAddWatchlist = async () => {
    if (!stockCode) return;
    setWatchlistSaving(true);
    setError(null);
    setSuccessMessage('');
    try {
      await accountApi.addWatchlistStock({ stockCode, stockName: quote?.stockName || history?.stockName || undefined });
      setSuccessMessage('已加入自选股。');
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setWatchlistSaving(false);
    }
  };

  if (!stockCode) {
    return (
      <StandardPageLayout>
        <Card title="股票详情" subtitle="STOCK">
          <p className="text-sm text-secondary-text">缺少股票代码。</p>
          <div className="mt-4">
            <Button variant="outline" onClick={() => navigate('/')}>返回首页</Button>
          </div>
        </Card>
      </StandardPageLayout>
    );
  }

  return (
    <StandardPageLayout>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <button
            type="button"
            className="inline-flex items-center gap-2 text-sm text-secondary-text transition-colors hover:text-foreground"
            onClick={() => navigate(-1)}
          >
            <ArrowLeft className="h-4 w-4" /> 返回
          </button>
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-text">STOCK DETAIL</p>
            <h1 className="mt-1 flex flex-wrap items-end gap-3 text-3xl font-bold tracking-tight text-foreground">
              <span>{displayName}</span>
              <span className="font-mono text-xl text-secondary-text">{stockCode}</span>
            </h1>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => void loadStock()} disabled={loading}>
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
          <Link to={`/chat?stock=${encodeURIComponent(stockCode)}&name=${encodeURIComponent(displayName)}`}>
            <Button variant="outline">
              <MessageSquareQuote className="h-4 w-4" /> 问股
            </Button>
          </Link>
          <Link to={`/alerts?stock=${encodeURIComponent(stockCode)}&name=${encodeURIComponent(displayName)}`}>
            <Button variant="outline">
              <BellRing className="h-4 w-4" /> 设置提醒
            </Button>
          </Link>
          {canUseUserActions ? (
            <Button variant="primary" isLoading={watchlistSaving} onClick={() => void handleAddWatchlist()}>
              <Star className="h-4 w-4" /> 加入自选
            </Button>
          ) : null}
        </div>
      </div>

      {error ? <SettingsAlert title="加载失败" message={error.message} variant="error" /> : null}
      {successMessage ? <SettingsAlert title="操作成功" message={successMessage} variant="success" /> : null}

      {loading && !quote && !history ? (
        <Card>
          <div className="flex items-center gap-2 text-sm text-secondary-text">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载行情中...
          </div>
        </Card>
      ) : (
        <>
          <Card title="实时行情" subtitle="QUOTE">
            {quote ? (
              <div className="space-y-5">
                <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                  <div>
                    <div className="text-4xl font-semibold tracking-tight text-foreground">
                      {formatNumber(quote.currentPrice)}
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <Badge variant={getChangeTone(quote.changePercent)}>
                        {formatSignedNumber(quote.change)} / {formatSignedPercent(quote.changePercent)}
                      </Badge>
                      <span className="text-xs text-muted-text">
                        更新时间 {formatDateTime(quote.updateTime || undefined)}
                      </span>
                    </div>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => navigate(`/?stock=${encodeURIComponent(stockCode)}&name=${encodeURIComponent(displayName)}`)}
                  >
                    <BarChart3 className="h-4 w-4" /> 发起分析
                  </Button>
                </div>

                <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                  {STAT_LABELS.map((item) => {
                    const value = quote[item.key];
                    return (
                      <div key={item.key} className="rounded-xl border border-border/60 bg-card/50 p-3">
                        <p className="text-xs text-muted-text">{item.label}</p>
                        <p className="mt-1 font-mono text-sm font-semibold text-foreground">
                          {item.formatter ? item.formatter(value) : formatNumber(value)}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <EmptyState title="暂无实时行情" description="当前数据源未返回该股票的实时行情。" />
            )}
          </Card>

          <Card title="历史走势" subtitle="PRICE HISTORY">
            {chartData.length > 0 ? (
              <div className="space-y-4">
                <div className="h-72 min-w-0">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="stock-detail-close" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="hsl(var(--color-primary))" stopOpacity={0.32} />
                          <stop offset="95%" stopColor="hsl(var(--color-primary))" stopOpacity={0.03} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="hsl(var(--color-border) / 0.45)" strokeDasharray="3 3" vertical={false} />
                      <XAxis
                        dataKey="label"
                        tick={{ fill: 'hsl(var(--color-muted-foreground))', fontSize: 11 }}
                        tickLine={false}
                        axisLine={{ stroke: 'hsl(var(--color-border) / 0.6)' }}
                        minTickGap={24}
                      />
                      <YAxis
                        width={58}
                        domain={['dataMin', 'dataMax']}
                        tick={{ fill: 'hsl(var(--color-muted-foreground))', fontSize: 11 }}
                        tickFormatter={(value) => formatNumber(value)}
                        tickLine={false}
                        axisLine={false}
                      />
                      <Tooltip
                        contentStyle={{
                          borderRadius: 12,
                          borderColor: 'hsl(var(--color-border))',
                          background: 'hsl(var(--color-surface))',
                          color: 'hsl(var(--color-foreground))',
                          boxShadow: 'var(--shadow-card)',
                        }}
                        labelStyle={{ color: 'hsl(var(--color-subtle-foreground))' }}
                        formatter={(value, name) => [
                          name === 'changePercent' ? formatSignedPercent(value) : formatNumber(value),
                          name === 'close' ? '收盘价' : name === 'changePercent' ? '涨跌幅' : String(name),
                        ]}
                      />
                      <Area
                        type="monotone"
                        dataKey="close"
                        stroke="hsl(var(--color-primary))"
                        strokeWidth={2}
                        fill="url(#stock-detail-close)"
                        name="收盘价"
                        dot={false}
                        activeDot={{ r: 4 }}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>

                <div className="overflow-x-auto rounded-xl border border-subtle">
                  <table className="min-w-full divide-y divide-subtle text-xs">
                    <thead className="bg-surface-muted/60 text-muted-text">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium">日期</th>
                        <th className="px-3 py-2 text-right font-medium">开盘</th>
                        <th className="px-3 py-2 text-right font-medium">最高</th>
                        <th className="px-3 py-2 text-right font-medium">最低</th>
                        <th className="px-3 py-2 text-right font-medium">收盘</th>
                        <th className="px-3 py-2 text-right font-medium">涨跌幅</th>
                        <th className="px-3 py-2 text-right font-medium">成交量</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-subtle bg-surface/30">
                      {chartData.slice(-8).reverse().map((item) => (
                        <tr key={item.date}>
                          <td className="whitespace-nowrap px-3 py-2 font-mono text-secondary-text">{formatDate(item.date)}</td>
                          <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-secondary-text">{formatNumber(item.open)}</td>
                          <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-secondary-text">{formatNumber(item.high)}</td>
                          <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-secondary-text">{formatNumber(item.low)}</td>
                          <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-foreground">{formatNumber(item.close)}</td>
                          <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-secondary-text">{formatSignedPercent(item.changePercent)}</td>
                          <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-secondary-text">{formatCompactNumber(item.volume)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <EmptyState title="暂无历史行情" description="当前数据源未返回可展示的历史 K 线。" />
            )}
          </Card>
        </>
      )}
    </StandardPageLayout>
  );
};

export default StockDetailPage;
