/**
 * Stock Index Loader
 *
 * Responsible for loading and parsing stock index data
 */

import type { StockIndexItem, StockIndexTuple } from '../types/stockIndex';

export interface IndexLoadResult {
  /** Index data */
  data: StockIndexItem[];
  /** Successfully loaded */
  loaded: boolean;
  /** Error information */
  error?: Error;
  /** Whether fallback mode is used */
  fallback: boolean;
}

/**
 * Load stock index
 *
 * @returns Index load result
 */
export async function loadStockIndex(): Promise<IndexLoadResult> {
  return {
    data: [],
    loaded: true,
    fallback: false,
  };
}

/**
 * Compress object format to tuple format
 *
 * For reducing index file size
 */
export function compressIndex(items: StockIndexItem[]): StockIndexTuple[] {
  return items.map(item => [
    item.canonicalCode,
    item.displayCode,
    item.nameZh,
    item.pinyinFull,
    item.pinyinAbbr,
    item.aliases || [],
    item.market,
    item.assetType,
    item.active,
    item.popularity,
  ]);
}

/**
 * Find stock in index
 *
 * @param canonicalCode - Canonical code
 * @param index - Stock index
 * @returns Stock index item or null
 */
export function findStockInIndex(
  canonicalCode: string,
  index: StockIndexItem[]
): StockIndexItem | null {
  return index.find(item => item.canonicalCode === canonicalCode) || null;
}

/**
 * Get popular stocks list
 *
 * @param index - Stock index
 * @param limit - Number of results to return
 * @returns Popular stocks list
 */
export function getPopularStocks(
  index: StockIndexItem[],
  limit: number = 20
): StockIndexItem[] {
  return [...index]
    .filter(item => item.active)
    .sort((a, b) => (b.popularity || 0) - (a.popularity || 0))
    .slice(0, limit);
}

/**
 * Group stocks by market
 *
 * @param index - Stock index
 * @returns Map of stocks grouped by market
 */
export function groupStocksByMarket(
  index: StockIndexItem[]
): Map<string, StockIndexItem[]> {
  const grouped = new Map<string, StockIndexItem[]>();

  for (const item of index) {
    if (!item.active) continue;

    const market = item.market;
    if (!grouped.has(market)) {
      grouped.set(market, []);
    }
    const group = grouped.get(market);
    if (group) {
      group.push(item);
    }
  }

  return grouped;
}
