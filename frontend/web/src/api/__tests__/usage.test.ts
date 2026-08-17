import { beforeEach, describe, expect, it, vi } from 'vitest';
import { usageApi } from '../usage';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../index', () => ({
  default: { get },
}));

describe('usageApi', () => {
  beforeEach(() => get.mockReset());

  it('loads the dashboard with the selected period and limit', async () => {
    get.mockResolvedValueOnce({
      data: {
        period: 'today',
        total_prompt_tokens: 10,
        total_completion_tokens: 20,
        recent_calls: [{ called_at: '2026-06-11T09:30:00', total_tokens: 30 }],
      },
    });

    const result = await usageApi.getDashboard({ period: 'today', limit: 10 });

    expect(get).toHaveBeenCalledWith('/api/v1/usage/dashboard', {
      params: { period: 'today', limit: 10 },
    });
    expect(result.totalPromptTokens).toBe(10);
    expect(result.totalCompletionTokens).toBe(20);
    expect(result.recentCalls[0].calledAt).toBe('2026-06-11T09:30:00');
  });
});
