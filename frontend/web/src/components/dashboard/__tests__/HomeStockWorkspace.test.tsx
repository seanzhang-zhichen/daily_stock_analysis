import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { HomeStockWorkspace } from '../HomeStockWorkspace';
import { accountApi } from '../../../api/account';
import { analysisApi } from '../../../api/analysis';

vi.mock('../../../api/account', () => ({
  accountApi: {
    getWatchlist: vi.fn(),
    addWatchlistStock: vi.fn(),
    removeWatchlistStock: vi.fn(),
  },
}));

vi.mock('../../../api/analysis', () => ({
  analysisApi: { analyzeAsync: vi.fn() },
}));

const props = {
  historyItems: [],
  isLoadingHistory: false,
  isLoadingMore: false,
  hasMore: false,
  selectedIds: new Set<number>(),
  isDeletingHistory: false,
  activeTasks: [],
  onHistoryItemClick: vi.fn(),
  onLoadMore: vi.fn(),
  onToggleItemSelection: vi.fn(),
  onToggleSelectAll: vi.fn(),
  onDeleteSelected: vi.fn(),
  onTaskCreated: vi.fn(),
};

describe('HomeStockWorkspace', () => {
  it('filters non-A-share watchlist entries and submits A-share batch analysis', async () => {
    vi.mocked(accountApi.getWatchlist).mockResolvedValue({
      stocks: [
        { stockCode: '600519', stockName: '贵州茅台' },
        { stockCode: 'sz000001', stockName: '平安银行' },
        { stockCode: 'AAPL', stockName: 'Apple' },
      ],
      count: 3,
      maxStocks: 10,
    });
    vi.mocked(analysisApi.analyzeAsync).mockResolvedValue({
      accepted: [
        { taskId: 'task-1', stockCode: '600519', status: 'pending', message: '已提交' },
        { taskId: 'task-2', stockCode: 'sz000001', status: 'pending', message: '已提交' },
      ],
      duplicates: [],
      message: '已提交',
    });

    render(<HomeStockWorkspace {...props} />);
    fireEvent.click(screen.getByRole('button', { name: '自选' }));

    expect(await screen.findByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('平安银行')).toBeInTheDocument();
    expect(screen.queryByText('Apple')).not.toBeInTheDocument();
    expect(screen.getByText('当日覆盖 0/2，待分析 2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '分析全部' }));
    await waitFor(() => {
      expect(analysisApi.analyzeAsync).toHaveBeenCalledWith(expect.objectContaining({
        stockCodes: ['600519', 'sz000001'],
        reportType: 'detailed',
      }));
    });
    expect(props.onTaskCreated).toHaveBeenCalledTimes(2);
  });
});
