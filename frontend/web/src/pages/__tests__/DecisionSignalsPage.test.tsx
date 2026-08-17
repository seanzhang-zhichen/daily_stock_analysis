import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DecisionSignalsPage from '../DecisionSignalsPage';

const { list, sync, updateStatus } = vi.hoisted(() => ({
  list: vi.fn(),
  sync: vi.fn(),
  updateStatus: vi.fn(),
}));

vi.mock('../../api/decisionSignals', () => ({
  decisionSignalsApi: { list, sync, updateStatus },
}));

vi.mock('../../components/StockAutocomplete', () => ({
  StockAutocomplete: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (value: string) => void;
  }) => (
    <input
      aria-label="股票代码或名称"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

const signal = {
  id: 12,
  stockCode: '600519',
  stockName: '贵州茅台',
  market: 'cn',
  sourceType: 'analysis',
  sourceReportId: 88,
  triggerSource: 'analysis_history',
  action: 'buy' as const,
  actionLabel: '买入',
  confidence: 0.85,
  score: 82,
  horizon: '5d',
  entryLow: 98,
  entryHigh: 100,
  stopLoss: 92,
  targetPrice: 118,
  reason: '趋势转强，等待量能确认。',
  riskSummary: '估值偏高',
  catalystSummary: '行业景气改善',
  planQuality: 'complete',
  status: 'active' as const,
  expiresAt: '2026-08-20T12:00:00',
  createdAt: '2026-08-14T12:00:00',
};

describe('DecisionSignalsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    list.mockResolvedValue({ items: [signal], total: 1, page: 1, pageSize: 20 });
    sync.mockResolvedValue({ created: 0 });
    updateStatus.mockResolvedValue({ ...signal, status: 'archived' });
  });

  it('renders signals and opens the structured detail drawer', async () => {
    render(
      <MemoryRouter>
        <DecisionSignalsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'AI 建议' })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));

    expect(await screen.findByRole('heading', { name: '贵州茅台 AI 建议' })).toBeInTheDocument();
    expect(screen.getByText('98 - 100')).toBeInTheDocument();
    expect(screen.getByText('估值偏高')).toBeInTheDocument();
    expect(screen.getByText('行业景气改善')).toBeInTheDocument();
  });

  it('archives an active signal through the confirmation flow', async () => {
    render(
      <MemoryRouter>
        <DecisionSignalsPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 AI 建议详情' }));
    fireEvent.click(screen.getByRole('button', { name: '归档' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认归档' }));

    await waitFor(() => {
      expect(updateStatus).toHaveBeenCalledWith(12, 'archived');
    });
  });

  it('applies stock and status filters', async () => {
    render(
      <MemoryRouter>
        <DecisionSignalsPage />
      </MemoryRouter>,
    );
    await screen.findByText('贵州茅台');
    fireEvent.change(screen.getByRole('textbox', { name: '股票代码或名称' }), {
      target: { value: 'aapl' },
    });
    fireEvent.change(screen.getByRole('combobox', { name: '建议状态' }), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));

    await waitFor(() => {
      expect(list).toHaveBeenLastCalledWith(expect.objectContaining({
        stockCode: 'AAPL',
        status: '',
      }));
    });
  });
});
