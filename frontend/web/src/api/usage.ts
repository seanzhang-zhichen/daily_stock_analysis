import apiClient from './index';
import { toCamelCase } from './utils';
import type { UsageDashboardResponse, UsagePeriod, UsageSummaryResponse } from '../types/usage';

export const usageApi = {
  async getSummary(period: UsagePeriod = 'month'): Promise<UsageSummaryResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/usage/summary', {
      params: { period },
    });
    return toCamelCase<UsageSummaryResponse>(response.data);
  },

  async getDashboard(
    params: { period?: UsagePeriod; limit?: number } = {},
  ): Promise<UsageDashboardResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/usage/dashboard', {
      params: {
        period: params.period ?? 'month',
        limit: params.limit ?? 50,
      },
    });
    return toCamelCase<UsageDashboardResponse>(response.data);
  },
};
