export type StockSelectionSortBy = 'volatility_then_return' | 'return_then_volatility' | 'score';

export type StockSelectionStrategyItem = {
  name: string;
  displayName: string;
  description: string;
  aliases: string[];
  defaultParams: Record<string, unknown>;
};

export type StockSelectionStrategiesResponse = {
  items: StockSelectionStrategyItem[];
};

export type StockSelectionRequest = {
  strategy?: string;
  stockCodes?: string[];
  markets?: string[];
  limit?: number;
  targetDate?: string;
  lookbackDays?: number;
  minHighPosition?: number;
  recentHighDays?: number;
  sortBy?: StockSelectionSortBy;
};

export type StockSelectionCandidate = {
  code: string;
  name?: string | null;
  market: string;
  strategy: string;
  latestDate: string;
  latestClose: number;
  windowHigh: number;
  windowHighDate: string;
  daysSinceHigh: number;
  distanceToHighPct: number;
  windowReturnPct: number;
  volatilityPct: number;
  score: number;
  source: string;
};

export type StockSelectionDiagnostics = {
  total: number;
  processed: number;
  matched: number;
  noData: number;
  insufficientData: number;
  errors: number;
  skippedUnsupportedMarket: number;
};

export type StockSelectionResponse = {
  strategy: string;
  params: Record<string, unknown>;
  items: StockSelectionCandidate[];
  diagnostics: StockSelectionDiagnostics;
  generatedAt?: string | null;
  targetDate?: string | null;
};
