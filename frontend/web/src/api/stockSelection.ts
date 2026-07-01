import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  StockSelectionRequest,
  StockSelectionResponse,
  StockSelectionStrategiesResponse,
} from '../types/stockSelection';

function buildRunPayload(params: StockSelectionRequest): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (params.strategy) payload.strategy = params.strategy;
  if (params.stockCodes?.length) payload.stock_codes = params.stockCodes;
  if (params.markets?.length) payload.markets = params.markets;
  if (params.limit != null) payload.limit = params.limit;
  if (params.targetDate) payload.target_date = params.targetDate;
  if (params.lookbackDays != null) payload.lookback_days = params.lookbackDays;
  if (params.minHighPosition != null) payload.min_high_position = params.minHighPosition;
  if (params.recentHighDays != null) payload.recent_high_days = params.recentHighDays;
  if (params.sortBy) payload.sort_by = params.sortBy;
  return payload;
}

export const stockSelectionApi = {
  async listStrategies(): Promise<StockSelectionStrategiesResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/stock-selection/strategies');
    return toCamelCase<StockSelectionStrategiesResponse>(response.data);
  },

  async run(params: StockSelectionRequest): Promise<StockSelectionResponse> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/stock-selection/run',
      buildRunPayload(params),
      { timeout: 120000 },
    );
    return toCamelCase<StockSelectionResponse>(response.data);
  },
};
