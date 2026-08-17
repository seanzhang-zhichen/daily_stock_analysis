import { describe, expect, it } from 'vitest';
import type { PortfolioPositionItem } from '../../types/portfolio';
import {
  buildFxRefreshFeedback,
  formatMoney,
  formatPositionMoney,
  formatPositionPrice,
  formatSignedPct,
  getPositionPriceLabel,
} from '../portfolioFormat';

const pricedPosition: PortfolioPositionItem = {
  symbol: 'HK00700',
  market: 'hk',
  currency: 'HKD',
  quantity: 100,
  avgCost: 300,
  totalCost: 30000,
  lastPrice: 321.12345,
  marketValueBase: 32112.345,
  unrealizedPnlBase: 2112.345,
  unrealizedPnlPct: 7.04,
  valuationCurrency: 'CNY',
  priceSource: 'realtime_quote',
  priceProvider: 'longbridge',
  priceAvailable: true,
};

describe('portfolioFormat', () => {
  it('formats money and signed percentages', () => {
    expect(formatMoney(1234.5, 'USD')).toContain('1,234.50');
    expect(formatMoney(null)).toBe('--');
    expect(formatSignedPct(3.456)).toBe('+3.46%');
    expect(formatSignedPct(-1.2)).toBe('-1.20%');
  });

  it('uses price availability for position values', () => {
    expect(formatPositionPrice(pricedPosition)).toBe('321.1234');
    expect(formatPositionMoney(123, pricedPosition)).toContain('123.00');
    expect(getPositionPriceLabel(pricedPosition)).toBe('实时价 · longbridge');

    const missingPosition = { ...pricedPosition, priceAvailable: false, priceSource: 'missing' };
    expect(formatPositionPrice(missingPosition)).toBe('--');
    expect(formatPositionMoney(123, missingPosition)).toBe('--');
    expect(getPositionPriceLabel(missingPosition)).toBe('缺价');
  });

  it('summarizes FX refresh outcomes', () => {
    expect(buildFxRefreshFeedback({
      asOf: '2026-08-14',
      accountCount: 1,
      refreshEnabled: false,
      pairCount: 0,
      updatedCount: 0,
      staleCount: 0,
      errorCount: 0,
    })).toMatchObject({ tone: 'neutral' });
    expect(buildFxRefreshFeedback({
      asOf: '2026-08-14',
      accountCount: 1,
      refreshEnabled: true,
      pairCount: 1,
      updatedCount: 1,
      staleCount: 0,
      errorCount: 0,
    })).toMatchObject({ tone: 'success' });
  });
});
