export type DecisionAction = 'buy' | 'add' | 'hold' | 'reduce' | 'sell' | 'watch' | 'avoid' | 'alert';
export type DecisionSignalStatus = 'active' | 'expired' | 'invalidated' | 'closed' | 'archived';
export type DecisionSignalMarket = 'cn' | 'hk' | 'us';

export interface DecisionSignalItem {
  id: number;
  stockCode: string;
  stockName?: string | null;
  market: string;
  sourceType: string;
  sourceReportId: number;
  traceId?: string | null;
  triggerSource: string;
  action: DecisionAction;
  actionLabel?: string | null;
  confidence?: number | null;
  score?: number | null;
  horizon?: string | null;
  entryLow?: number | null;
  entryHigh?: number | null;
  stopLoss?: number | null;
  targetPrice?: number | null;
  invalidation?: string | null;
  watchConditions?: string | null;
  reason?: string | null;
  riskSummary?: string | null;
  catalystSummary?: string | null;
  evidence?: unknown;
  dataQualitySummary?: unknown;
  metadata?: unknown;
  planQuality: string;
  status: DecisionSignalStatus;
  expiresAt?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface DecisionSignalListParams {
  market?: string;
  stockCode?: string;
  action?: string;
  status?: string;
  createdFrom?: string;
  createdTo?: string;
  page?: number;
  pageSize?: number;
}

export interface DecisionSignalListResponse {
  items: DecisionSignalItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface DecisionSignalLatestResponse {
  items: DecisionSignalItem[];
}
