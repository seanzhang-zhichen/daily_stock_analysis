import type React from 'react';
import { Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { PriceHistoryItem, ReportLanguage } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { formatDate } from '../../utils/format';
import { normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportPriceHistoryProps {
  data?: PriceHistoryItem[];
  language?: ReportLanguage;
}

type ChartPoint = PriceHistoryItem & {
  closeValue: number;
  label: string;
};

const coerceNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : undefined;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
};

const formatNumber = (value: unknown, digits = 2): string => {
  const number = coerceNumber(value);
  return number === undefined ? '—' : number.toFixed(digits);
};

const formatPct = (value: unknown): string => {
  const number = coerceNumber(value);
  if (number === undefined) {
    return '—';
  }
  const sign = number > 0 ? '+' : '';
  return `${sign}${number.toFixed(2)}%`;
};

const getChangeClassName = (value: unknown): string => {
  const number = coerceNumber(value);
  if (number === undefined || number === 0) {
    return 'text-secondary-text';
  }
  return number > 0 ? 'text-danger' : 'text-success';
};

const formatVolume = (value: unknown): string => {
  const number = coerceNumber(value);
  if (number === undefined) {
    return '—';
  }
  if (number >= 100_000_000) {
    return `${(number / 100_000_000).toFixed(2)}亿`;
  }
  if (number >= 10_000) {
    return `${(number / 10_000).toFixed(2)}万`;
  }
  return number.toFixed(0);
};

export const ReportPriceHistory: React.FC<ReportPriceHistoryProps> = ({
  data,
  language = 'zh',
}) => {
  const reportLanguage = normalizeReportLanguage(language);
  const labels = reportLanguage === 'en'
    ? {
        eyebrow: 'PRICE HISTORY',
        title: 'Historical Prices',
        description: 'Recent daily close, percentage change, and moving averages from the saved analysis data.',
        close: 'Close',
        date: 'Date',
        change: 'Change',
        volume: 'Volume',
        ma5: 'MA5',
        ma20: 'MA20',
      }
    : {
        eyebrow: '历史股价',
        title: '近期股价走势',
        description: '展示本次分析已保存的近期日线收盘价、涨跌幅与均线。',
        close: '收盘价',
        date: '日期',
        change: '涨跌幅',
        volume: '成交量',
        ma5: 'MA5',
        ma20: 'MA20',
      };
  const chartData: ChartPoint[] = (data || [])
    .map((item) => {
      const closeValue = coerceNumber(item.close);
      if (closeValue === undefined) {
        return null;
      }
      return {
        ...item,
        closeValue,
        label: formatDate(item.date),
      };
    })
    .filter((item): item is ChartPoint => item !== null);

  if (chartData.length === 0) {
    return null;
  }

  const recentRows = chartData.slice(-8).reverse();

  return (
    <Card variant="bordered" padding="md" className="text-left">
      <DashboardPanelHeader
        eyebrow={labels.eyebrow}
        title={labels.title}
        className="mb-2"
      />
      <p className="mb-4 text-xs leading-5 text-muted-text">{labels.description}</p>

      <div className="h-64 min-w-0">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="price-history-close" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="hsl(var(--color-primary))" stopOpacity={0.3} />
                <stop offset="95%" stopColor="hsl(var(--color-primary))" stopOpacity={0.02} />
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
              width={54}
              domain={["dataMin", "dataMax"]}
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
              formatter={(value, name) => {
                const label = name === 'closeValue' ? labels.close : String(name);
                return [formatNumber(value), label];
              }}
            />
            <Area
              type="monotone"
              dataKey="closeValue"
              stroke="hsl(var(--color-primary))"
              strokeWidth={2}
              fill="url(#price-history-close)"
              name={labels.close}
              dot={false}
              activeDot={{ r: 4 }}
            />
            <Line type="monotone" dataKey="ma5" stroke="hsl(var(--color-warning))" strokeWidth={1.5} dot={false} name={labels.ma5} />
            <Line type="monotone" dataKey="ma20" stroke="hsl(var(--color-success))" strokeWidth={1.5} dot={false} name={labels.ma20} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-4 overflow-x-auto rounded-xl border border-subtle">
        <table className="min-w-full divide-y divide-subtle text-xs">
          <thead className="bg-surface-muted/60 text-muted-text">
            <tr>
              <th className="px-3 py-2 text-left font-medium">{labels.date}</th>
              <th className="px-3 py-2 text-right font-medium">{labels.close}</th>
              <th className="px-3 py-2 text-right font-medium">{labels.change}</th>
              <th className="px-3 py-2 text-right font-medium">{labels.volume}</th>
              <th className="px-3 py-2 text-right font-medium">{labels.ma5}</th>
              <th className="px-3 py-2 text-right font-medium">{labels.ma20}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-subtle bg-surface/30">
            {recentRows.map((item) => (
              <tr key={`${item.code}-${item.date}`}>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-secondary-text">{formatDate(item.date)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-foreground">{formatNumber(item.close)}</td>
                <td className={`whitespace-nowrap px-3 py-2 text-right font-mono ${getChangeClassName(item.pctChg)}`}>{formatPct(item.pctChg)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-secondary-text">{formatVolume(item.volume)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-secondary-text">{formatNumber(item.ma5)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-secondary-text">{formatNumber(item.ma20)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

export default ReportPriceHistory;
