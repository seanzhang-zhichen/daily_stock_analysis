import apiClient from './index';
import { toCamelCase } from './utils';
import type { StockSuggestion } from '../types/stockIndex';

export type ExtractItem = {
  code?: string | null;
  name?: string | null;
  confidence: string;
};

export type ExtractFromImageResponse = {
  items: ExtractItem[];
  rawText?: string;
};

export type StockSearchResponse = {
  items: StockSuggestion[];
};

export type StockQuote = {
  stockCode: string;
  stockName?: string | null;
  currentPrice: number;
  change?: number | null;
  changePercent?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  prevClose?: number | null;
  volume?: number | null;
  amount?: number | null;
  updateTime?: string | null;
};

export type KLineData = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  amount?: number | null;
  changePercent?: number | null;
};

export type StockHistoryResponse = {
  stockCode: string;
  stockName?: string | null;
  period: 'daily' | 'weekly' | 'monthly' | string;
  data: KLineData[];
};

export const stocksApi = {
  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
    const response = await apiClient.post(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers,
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    const data = response.data as { items?: ExtractItem[]; raw_text?: string };
    return {
      items: data.items ?? [],
      rawText: data.raw_text,
    };
  },

  async parseImport(file?: File, text?: string): Promise<ExtractFromImageResponse> {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
      const response = await apiClient.post('/api/v1/stocks/parse-import', formData, { headers });
      const data = response.data as { items?: ExtractItem[] };
      return { items: data.items ?? [] };
    }
    if (text) {
      const response = await apiClient.post('/api/v1/stocks/parse-import', { text });
      const data = response.data as { items?: ExtractItem[] };
      return { items: data.items ?? [] };
    }
    throw new Error('请提供文件或粘贴文本');
  },

  async search(query: string, limit = 20, signal?: AbortSignal): Promise<StockSuggestion[]> {
    const response = await apiClient.get<StockSearchResponse>('/api/v1/stocks/search', {
      params: { q: query, limit },
      signal,
    });
    return response.data.items ?? [];
  },

  async getQuote(stockCode: string): Promise<StockQuote> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/quote`,
    );
    return toCamelCase<StockQuote>(response.data);
  },

  async getHistory(stockCode: string, params: {
    period?: 'daily' | 'weekly' | 'monthly';
    days?: number;
  } = {}): Promise<StockHistoryResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/history`,
      {
        params: {
          period: params.period ?? 'daily',
          days: params.days ?? 90,
        },
      },
    );
    return toCamelCase<StockHistoryResponse>(response.data);
  },
};
