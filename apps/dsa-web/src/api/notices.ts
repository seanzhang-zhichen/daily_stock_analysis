import apiClient from './index';

export type Notice = {
  id: number;
  title: string;
  content: string;
  noticeType: 'info' | 'warning' | 'danger';
  isPinned: boolean;
  isPublished: boolean;
  targetPlan: string | null;
  publishedAt: string | null;
  expiresAt: string | null;
  createdAt: string;
};

export type NoticeCreateInput = {
  title: string;
  content: string;
  noticeType?: 'info' | 'warning' | 'danger';
  isPinned?: boolean;
  targetPlan?: string | null;
  expiresAt?: string | null;
};

export type NoticeUpdateInput = Partial<NoticeCreateInput>;

export const noticesApi = {
  async list(page = 1, pageSize = 20): Promise<Notice[]> {
    const { data } = await apiClient.get<Notice[]>('/api/v1/notices', {
      params: { page, page_size: pageSize },
    });
    return data;
  },

  async getUnreadCount(): Promise<number> {
    const { data } = await apiClient.get<{ count: number }>('/api/v1/notices/unread-count');
    return data.count;
  },

  async adminList(page = 1, pageSize = 50): Promise<Notice[]> {
    const { data } = await apiClient.get<Notice[]>('/api/v1/notices/admin/list', {
      params: { page, page_size: pageSize },
    });
    return data;
  },

  async create(input: NoticeCreateInput): Promise<Notice> {
    const { data } = await apiClient.post<Notice>('/api/v1/notices', input);
    return data;
  },

  async update(id: number, input: NoticeUpdateInput): Promise<Notice> {
    const { data } = await apiClient.patch<Notice>(`/api/v1/notices/${id}`, input);
    return data;
  },

  async remove(id: number): Promise<void> {
    await apiClient.delete(`/api/v1/notices/${id}`);
  },

  async publish(id: number): Promise<Notice> {
    const { data } = await apiClient.post<Notice>(`/api/v1/notices/${id}/publish`);
    return data;
  },

  async unpublish(id: number): Promise<Notice> {
    const { data } = await apiClient.post<Notice>(`/api/v1/notices/${id}/unpublish`);
    return data;
  },
};
