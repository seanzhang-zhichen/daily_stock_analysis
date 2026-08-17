import type { PortfolioFxRefreshResponse, PortfolioImportCommitResponse, PortfolioImportParseResponse, PortfolioPositionItem } from '../types/portfolio';

export type PortfolioAlertVariant = 'info' | 'success' | 'warning' | 'neutral';

export function formatMoney(value: number | null | undefined, currency = 'CNY'): string {
  if (value == null || !Number.isFinite(Number(value))) return '--';
  return `${currency} ${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatSignedPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return '--';
  return `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
}

export function hasPositionPrice(position: PortfolioPositionItem): boolean {
  return position.priceAvailable !== false && position.priceSource !== 'missing';
}

export function formatPositionPrice(position: PortfolioPositionItem): string {
  if (!hasPositionPrice(position)) return '--';
  const price = Number(position.lastPrice);
  return Number.isFinite(price) && price > 0 ? price.toFixed(4) : '--';
}

export function formatPositionMoney(value: number | null | undefined, position: PortfolioPositionItem): string {
  if (!hasPositionPrice(position)) return '--';
  return formatMoney(value, position.valuationCurrency || 'CNY');
}

export function getPositionPriceLabel(position: PortfolioPositionItem): string {
  if (!hasPositionPrice(position)) return '缺价';
  if (position.priceSource === 'realtime_quote') {
    return `实时价${position.priceProvider ? ` · ${position.priceProvider}` : ''}`;
  }
  if (position.priceSource === 'history_close') {
    return position.priceStale && position.priceDate
      ? `收盘价 · ${position.priceDate}`
      : '收盘价';
  }
  return position.priceSource || '未知';
}

export function formatBrokerLabel(broker: string, displayName?: string | null): string {
  const id = String(broker || '').trim();
  const label = String(displayName || '').trim();
  return label ? `${id}（${label}）` : id;
}

export function getCsvParseVariant(result: PortfolioImportParseResponse): PortfolioAlertVariant {
  return result.errorCount > 0 || result.skippedCount > 0 ? 'warning' : 'info';
}

export function getCsvCommitVariant(result: PortfolioImportCommitResponse, isDryRun: boolean): PortfolioAlertVariant {
  if (isDryRun) return 'info';
  return result.failedCount > 0 || result.duplicateCount > 0 ? 'warning' : 'success';
}

export function buildFxRefreshFeedback(result: PortfolioFxRefreshResponse): {
  tone: PortfolioAlertVariant;
  message: string;
} {
  if (result.refreshEnabled === false) return { tone: 'neutral', message: '汇率在线刷新已禁用' };
  if (result.errorCount > 0) return { tone: 'warning', message: `汇率刷新完成，${result.errorCount} 个失败` };
  if (result.updatedCount > 0) return { tone: 'success', message: `已更新 ${result.updatedCount} 个汇率` };
  if (result.staleCount > 0) return { tone: 'warning', message: '汇率仍为 stale' };
  return { tone: 'info', message: '当前没有可刷新的汇率对' };
}
