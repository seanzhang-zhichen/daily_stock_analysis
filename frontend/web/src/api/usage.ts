import apiClient from './index';
import { toCamelCase } from './utils';
import type { UsagePeriod, UsageSummaryResponse } from '../types/usage';

export const usageApi = {
  async getSummary(period: UsagePeriod = 'month'): Promise<UsageSummaryResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/usage/summary', {
      params: { period },
    });
    return toCamelCase<UsageSummaryResponse>(response.data);
  },
};
