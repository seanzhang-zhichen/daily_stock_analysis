import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  DecisionSignalItem,
  DecisionSignalLatestResponse,
  DecisionSignalListParams,
  DecisionSignalListResponse,
  DecisionSignalStatus,
} from '../types/decisionSignals';

function buildParams(query: DecisionSignalListParams): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    const mappedKey = {
      stockCode: 'stock_code',
      createdFrom: 'created_from',
      createdTo: 'created_to',
      pageSize: 'page_size',
    }[key] ?? key;
    params[mappedKey] = value;
  });
  return params;
}

export const decisionSignalsApi = {
  async list(query: DecisionSignalListParams = {}): Promise<DecisionSignalListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/decision-signals', {
      params: buildParams(query),
    });
    return toCamelCase<DecisionSignalListResponse>(response.data);
  },

  async latest(stockCode: string): Promise<DecisionSignalLatestResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/decision-signals/latest/${encodeURIComponent(stockCode)}`,
    );
    return toCamelCase<DecisionSignalLatestResponse>(response.data);
  },

  async get(signalId: number): Promise<DecisionSignalItem> {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/decision-signals/${signalId}`);
    return toCamelCase<DecisionSignalItem>(response.data);
  },

  async sync(): Promise<{ created: number }> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/decision-signals/sync');
    return toCamelCase<{ created: number }>(response.data);
  },

  async updateStatus(signalId: number, status: DecisionSignalStatus): Promise<DecisionSignalItem> {
    const response = await apiClient.patch<Record<string, unknown>>(
      `/api/v1/decision-signals/${signalId}/status`,
      { status },
    );
    const data = toCamelCase<{ item: DecisionSignalItem }>(response.data);
    return data.item;
  },
};
