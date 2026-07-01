import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StockSelectionPage from '../StockSelectionPage';

const { mockListStrategies, mockRun } = vi.hoisted(() => ({
  mockListStrategies: vi.fn(),
  mockRun: vi.fn(),
}));

vi.mock('../../api/stockSelection', () => ({
  stockSelectionApi: {
    listStrategies: mockListStrategies,
    run: mockRun,
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockListStrategies.mockResolvedValue({
    items: [
      {
        name: 'near_new_high',
        displayName: '近新高策略',
        description: '筛选当前价接近 120 日新高，且新高发生在近 15 个交易日内的股票。',
        aliases: ['new_high', '近新高'],
        defaultParams: {
          lookbackDays: 120,
          minHighPosition: 0.85,
          recentHighDays: 15,
        },
      },
    ],
  });
  mockRun.mockResolvedValue({
    strategy: 'near_new_high',
    params: {
      sortBy: 'volatility_then_return',
    },
    items: [
      {
        code: '600519',
        name: '贵州茅台',
        market: 'CN',
        strategy: 'near_new_high',
        latestDate: '2026-06-30',
        latestClose: 118,
        windowHigh: 120,
        windowHighDate: '2026-06-26',
        daysSinceHigh: 2,
        distanceToHighPct: -1.6667,
        windowReturnPct: 18,
        volatilityPct: 21.5,
        score: 77,
        source: 'db_cache',
      },
    ],
    diagnostics: {
      total: 1,
      processed: 1,
      matched: 1,
      noData: 0,
      insufficientData: 0,
      errors: 0,
      skippedUnsupportedMarket: 0,
    },
    generatedAt: '2026-06-30T12:00:00',
    targetDate: null,
  });
});

describe('StockSelectionPage', () => {
  it('runs near-new-high selection with default parameters and renders candidates', async () => {
    render(
      <MemoryRouter>
        <StockSelectionPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('策略选股')).toBeInTheDocument();
    expect(await screen.findAllByText('近新高策略')).toHaveLength(3);

    fireEvent.click(screen.getByRole('button', { name: /执行/ }));

    await waitFor(() => {
      expect(mockRun).toHaveBeenCalledWith({
        strategy: 'near_new_high',
        markets: ['cn'],
        stockCodes: undefined,
        targetDate: undefined,
        lookbackDays: 120,
        minHighPosition: 0.85,
        recentHighDays: 15,
        limit: 50,
        sortBy: 'volatility_then_return',
      });
    });

    expect(await screen.findByText('600519')).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('-1.67%')).toBeInTheDocument();
    expect(screen.getByText('+18.00%')).toBeInTheDocument();
    expect(screen.getByText('本地缓存')).toBeInTheDocument();
  });

  it('uses explicit stock codes and converted percentage threshold', async () => {
    render(
      <MemoryRouter>
        <StockSelectionPage />
      </MemoryRouter>,
    );

    await screen.findByText('策略选股');
    fireEvent.change(screen.getByPlaceholderText('600519, 000001, AAPL'), {
      target: { value: '600519, aapl' },
    });
    fireEvent.change(screen.getByDisplayValue('85'), {
      target: { value: '90' },
    });
    fireEvent.click(screen.getByRole('button', { name: /执行/ }));

    await waitFor(() => {
      expect(mockRun).toHaveBeenCalledWith(expect.objectContaining({
        stockCodes: ['600519', 'AAPL'],
        minHighPosition: 0.9,
      }));
    });
  });
});
