import apiClient from './index';
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
};
