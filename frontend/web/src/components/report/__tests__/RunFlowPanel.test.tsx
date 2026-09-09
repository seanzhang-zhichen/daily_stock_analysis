import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import { RunFlowPanel } from '../RunFlowPanel';

vi.mock('../../../api/history', () => ({ historyApi: { getRunFlow: vi.fn() } }));

describe('RunFlowPanel', () => {
  it('renders lanes, selects nodes, and filters events', async () => {
    vi.mocked(historyApi.getRunFlow).mockResolvedValue({ taskId: 'task-1', stockCode: '600519', status: 'degraded', lanes: [{ id: 'entry', label: '入口', order: 1 }, { id: 'data', label: '数据', order: 2 }], nodes: [{ id: 'request', lane: 'entry', kind: 'entry', label: '分析请求', status: 'success', metadata: {} }, { id: 'provider', lane: 'data', kind: 'data_source', label: '日线数据', status: 'fallback', provider: 'akshare', durationMs: 35, metadata: { record_count: 3 } }], edges: [], events: [{ id: 'ok', type: 'request_completed', severity: 'success', nodeId: 'request', title: '分析完成' }, { id: 'warn', type: 'provider_attempt', severity: 'warning', nodeId: 'provider', title: '数据源降级' }] });
    render(<RunFlowPanel recordId={1} />);
    expect(await screen.findByText('日线数据')).toBeInTheDocument();
    fireEvent.click(screen.getByText('日线数据'));
    expect(screen.getAllByText('akshare')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: '告警' }));
    expect(screen.getByText('数据源降级')).toBeInTheDocument();
    expect(screen.queryByText('分析完成')).not.toBeInTheDocument();
  });
});
