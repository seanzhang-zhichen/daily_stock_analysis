import { beforeEach, describe, expect, it, vi } from 'vitest';
import { decisionSignalsApi } from '../decisionSignals';

const { get, post, patch } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
}));

vi.mock('../index', () => ({
  default: { get, post, patch },
}));

const rawItem = {
  id: 12,
  stock_code: '600519',
  stock_name: '贵州茅台',
  market: 'cn',
  source_type: 'analysis',
  source_report_id: 88,
  trigger_source: 'analysis_history',
  action: 'buy',
  action_label: '买入',
  plan_quality: 'complete',
  status: 'active',
};

describe('decisionSignalsApi', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    patch.mockReset();
  });

  it('maps list filters to the backend query contract', async () => {
    get.mockResolvedValueOnce({
      data: { items: [rawItem], total: 1, page: 2, page_size: 20 },
    });

    const result = await decisionSignalsApi.list({
      stockCode: '600519',
      market: 'cn',
      status: '',
      page: 2,
      pageSize: 20,
    });

    expect(get).toHaveBeenCalledWith('/api/v1/decision-signals', {
      params: {
        stock_code: '600519',
        market: 'cn',
        status: '',
        page: 2,
        page_size: 20,
      },
    });
    expect(result.items[0].stockCode).toBe('600519');
    expect(result.items[0].sourceReportId).toBe(88);
    expect(result.pageSize).toBe(20);
  });

  it('syncs history and updates a signal status', async () => {
    post.mockResolvedValueOnce({ data: { created: 3 } });
    patch.mockResolvedValueOnce({ data: { item: { ...rawItem, status: 'archived' } } });

    await expect(decisionSignalsApi.sync()).resolves.toEqual({ created: 3 });
    const updated = await decisionSignalsApi.updateStatus(12, 'archived');

    expect(post).toHaveBeenCalledWith('/api/v1/decision-signals/sync');
    expect(patch).toHaveBeenCalledWith('/api/v1/decision-signals/12/status', { status: 'archived' });
    expect(updated.status).toBe('archived');
  });
});
