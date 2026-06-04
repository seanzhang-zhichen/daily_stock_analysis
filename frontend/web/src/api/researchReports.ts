import apiClient from './index';

export type ResearchReport = {
  id: number;
  title: string;
  summary: string;
  previewContent: string;
  fullContent?: string | null;
  category: string | null;
  tags: string[];
  coverImageUrl: string | null;
  priceCredits: number;
  isPublished: boolean;
  isUnlocked: boolean;
  likes: number;
  dislikes: number;
  commentsCount: number;
  myReaction: 'like' | 'dislike' | null;
  publishedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

export type ResearchComment = {
  id: number;
  reportId: number;
  userId: number;
  authorEmail: string | null;
  content: string;
  createdAt: string | null;
};

export type ResearchReportInput = {
  title: string;
  summary: string;
  previewContent: string;
  fullContent: string;
  priceCredits: number;
  category?: string | null;
  tags?: string[];
  coverImageUrl?: string | null;
};

export const researchReportsApi = {
  async list(page = 1, pageSize = 20): Promise<{ reports: ResearchReport[]; count: number }> {
    const { data } = await apiClient.get<{ reports: ResearchReport[]; count: number }>(
      '/api/v1/research-reports',
      { params: { page, page_size: pageSize } }
    );
    return data;
  },

  async get(id: number): Promise<ResearchReport> {
    const { data } = await apiClient.get<{ report: ResearchReport }>(`/api/v1/research-reports/${id}`);
    return data.report;
  },

  async purchase(id: number): Promise<{ report: ResearchReport; creditBalance: number }> {
    const { data } = await apiClient.post<{ report: ResearchReport; creditBalance: number }>(
      `/api/v1/research-reports/${id}/purchase`
    );
    return data;
  },

  async react(id: number, reaction: 'like' | 'dislike' | null): Promise<ResearchReport> {
    const { data } = await apiClient.post<{ report: ResearchReport }>(
      `/api/v1/research-reports/${id}/reaction`,
      { reaction }
    );
    return data.report;
  },

  async comments(id: number): Promise<{ comments: ResearchComment[]; count: number }> {
    const { data } = await apiClient.get<{ comments: ResearchComment[]; count: number }>(
      `/api/v1/research-reports/${id}/comments`
    );
    return data;
  },

  async comment(id: number, content: string): Promise<ResearchComment> {
    const { data } = await apiClient.post<{ comment: ResearchComment }>(
      `/api/v1/research-reports/${id}/comments`,
      { content }
    );
    return data.comment;
  },

  async mineList(): Promise<{ reports: ResearchReport[]; count: number }> {
    const { data } = await apiClient.get<{ reports: ResearchReport[]; count: number }>(
      '/api/v1/research-reports/mine/list'
    );
    return data;
  },

  async create(input: ResearchReportInput): Promise<ResearchReport> {
    const { data } = await apiClient.post<{ report: ResearchReport }>('/api/v1/research-reports', input);
    return data.report;
  },

  async update(id: number, input: Partial<ResearchReportInput>): Promise<ResearchReport> {
    const { data } = await apiClient.patch<{ report: ResearchReport }>(`/api/v1/research-reports/${id}`, input);
    return data.report;
  },

  async remove(id: number): Promise<void> {
    await apiClient.delete(`/api/v1/research-reports/${id}`);
  },

  async publish(id: number): Promise<ResearchReport> {
    const { data } = await apiClient.post<{ report: ResearchReport }>(`/api/v1/research-reports/${id}/publish`);
    return data.report;
  },

  async unpublish(id: number): Promise<ResearchReport> {
    const { data } = await apiClient.post<{ report: ResearchReport }>(`/api/v1/research-reports/${id}/unpublish`);
    return data.report;
  },
};
