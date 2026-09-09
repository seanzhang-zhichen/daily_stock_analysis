import apiClient from './index';

export type IntelligenceSource = { id: number; name: string; source_type: string; url: string; enabled: boolean; scope_type: string; scope_value?: string | null; description?: string | null; last_error?: string | null };
export type IntelligenceItem = { id: number; source_name?: string | null; source_type: string; title: string; summary?: string | null; url: string; published_at?: string | null; fetched_at?: string | null };
type SourceList = { items: IntelligenceSource[]; total: number };
type ItemList = { items: IntelligenceItem[]; total: number };

export const intelligenceApi = {
  async listSources(): Promise<SourceList> { const { data } = await apiClient.get<SourceList>('/api/v1/intelligence/sources'); return data; },
  async listItems(query?: string): Promise<ItemList> { const { data } = await apiClient.get<ItemList>('/api/v1/intelligence/items', { params: { page_size: 50, days: 7, query: query || undefined } }); return data; },
  async createDefaults(): Promise<void> { await apiClient.post('/api/v1/intelligence/sources/defaults', null, { params: { enabled: true } }); },
  async createSource(input: Pick<IntelligenceSource, 'name' | 'source_type' | 'url' | 'scope_type'> & { description?: string }): Promise<void> { await apiClient.post('/api/v1/intelligence/sources', { ...input, market: 'cn', enabled: true }); },
  async setSourceEnabled(id: number, enabled: boolean): Promise<void> { await apiClient.patch(`/api/v1/intelligence/sources/${id}`, { enabled }); },
  async deleteSource(id: number): Promise<void> { await apiClient.delete(`/api/v1/intelligence/sources/${id}`); },
  async fetchEnabled(): Promise<void> { await apiClient.post('/api/v1/intelligence/sources/fetch-enabled'); },
  async fetchSource(id: number): Promise<void> { await apiClient.post(`/api/v1/intelligence/sources/${id}/fetch`); },
};
