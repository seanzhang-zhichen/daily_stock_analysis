import apiClient from './index';
import type { BillingInvoice, BillingOrder, BillingPlan, BillingRefund } from './billing';

export type AdminUser = {
  id: number;
  email: string;
  plan: string;
  planExpiresAt: string | null;
  isAdmin: boolean;
  createdAt: string | null;
  lastLoginAt: string | null;
  termsVersion: string | null;
  status: string;
};

export type AdminStats = {
  users: { total: number; paid: number };
  orders: { total: number; paid: number; revenueCents: number };
  pending: { refunds: number; invoices: number };
};

export type AdminGrantSubscription = {
  id: number;
  planCode: string;
  source: string;
  startedAt: string | null;
  expiresAt: string | null;
  note: string | null;
};

export type AdminPlan = BillingPlan & {
  source?: 'default' | 'db';
  isPersisted?: boolean;
  allowedModels?: string[];
};

export type AdminPlatformSetting = {
  key: string;
  title: string;
  description: string;
  category: 'registration' | 'risk_control' | 'compliance' | string;
  valueType: 'boolean' | 'integer' | 'string' | string;
  value: string | number | boolean;
  rawValue: string;
  defaultValue: string | number | boolean;
  source: 'db' | 'env' | 'default' | string;
  minimum?: number | null;
  maximum?: number | null;
  maxLength?: number | null;
  multiline?: boolean;
  updatedAt?: string | null;
};

export type AuditLogEntry = {
  id: number;
  action: string;
  userId: number | null;
  adminId: number | null;
  targetUserId: number | null;
  targetRef: string | null;
  detail: string | null;
  ip: string | null;
  userAgent: string | null;
  createdAt: string | null;
};

export const adminApi = {
  async me(): Promise<{ admin: AdminUser }> {
    const { data } = await apiClient.get<{ admin: AdminUser }>('/api/v1/admin/me');
    return data;
  },

  async listUsers(input?: {
    emailLike?: string;
    planCode?: string;
    isAdmin?: boolean;
    limit?: number;
  }): Promise<{ users: AdminUser[]; count: number }> {
    const params: Record<string, string | number | boolean> = {};
    if (input?.emailLike) params.emailLike = input.emailLike;
    if (input?.planCode) params.planCode = input.planCode;
    if (typeof input?.isAdmin === 'boolean') params.isAdmin = input.isAdmin;
    if (input?.limit) params.limit = input.limit;
    const { data } = await apiClient.get<{ users: AdminUser[]; count: number }>(
      '/api/v1/admin/users',
      { params }
    );
    return data;
  },

  async listPlans(): Promise<{ plans: AdminPlan[]; count: number }> {
    const { data } = await apiClient.get<{ plans: AdminPlan[]; count: number }>('/api/v1/admin/plans');
    return data;
  },

  async updatePlan(planCode: string, input: {
    name: string;
    dailyAnalysisLimit: number;
    dailyAgentLimit: number;
    maxStocks: number;
    canWebhook: boolean;
    priceCents: number;
    currency: string;
    isActive: boolean;
  }): Promise<{ plan: AdminPlan }> {
    const { data } = await apiClient.put<{ plan: AdminPlan }>(
      `/api/v1/admin/plans/${encodeURIComponent(planCode)}`,
      input
    );
    return data;
  },

  async listPlatformSettings(): Promise<{ settings: AdminPlatformSetting[]; count: number }> {
    const { data } = await apiClient.get<{ settings: AdminPlatformSetting[]; count: number }>(
      '/api/v1/admin/platform-settings'
    );
    return data;
  },

  async updatePlatformSettings(input: {
    settings: Array<{ key: string; value: string | number | boolean }>;
  }): Promise<{ settings: AdminPlatformSetting[]; count: number }> {
    const { data } = await apiClient.put<{ settings: AdminPlatformSetting[]; count: number }>(
      '/api/v1/admin/platform-settings',
      input
    );
    return data;
  },

  async listOrders(input?: {
    status?: string;
    userId?: number;
    provider?: string;
    limit?: number;
  }): Promise<{ orders: BillingOrder[]; count: number }> {
    const params: Record<string, string | number> = {};
    if (input?.status) params.status = input.status;
    if (typeof input?.userId === 'number') params.userId = input.userId;
    if (input?.provider) params.provider = input.provider;
    if (input?.limit) params.limit = input.limit;
    const { data } = await apiClient.get<{ orders: BillingOrder[]; count: number }>(
      '/api/v1/admin/orders',
      { params }
    );
    return data;
  },

  async listRefunds(input?: { status?: string; limit?: number }): Promise<{ refunds: BillingRefund[]; count: number }> {
    const params: Record<string, string | number> = {};
    if (input?.status) params.status = input.status;
    if (input?.limit) params.limit = input.limit;
    const { data } = await apiClient.get<{ refunds: BillingRefund[]; count: number }>(
      '/api/v1/admin/refunds',
      { params }
    );
    return data;
  },

  async approveRefund(refundNo: string, providerRefundNo?: string): Promise<{ refund: BillingRefund }> {
    const { data } = await apiClient.post<{ refund: BillingRefund }>(
      `/api/v1/admin/refunds/${encodeURIComponent(refundNo)}/approve`,
      providerRefundNo ? { providerRefundNo } : {}
    );
    return data;
  },

  async rejectRefund(refundNo: string, note?: string): Promise<{ refund: BillingRefund }> {
    const { data } = await apiClient.post<{ refund: BillingRefund }>(
      `/api/v1/admin/refunds/${encodeURIComponent(refundNo)}/reject`,
      note ? { note } : {}
    );
    return data;
  },

  async listInvoices(input?: { status?: string; limit?: number }): Promise<{ invoices: BillingInvoice[]; count: number }> {
    const params: Record<string, string | number> = {};
    if (input?.status) params.status = input.status;
    if (input?.limit) params.limit = input.limit;
    const { data } = await apiClient.get<{ invoices: BillingInvoice[]; count: number }>(
      '/api/v1/admin/invoices',
      { params }
    );
    return data;
  },

  async issueInvoice(invoiceNo: string, issuedUrl?: string): Promise<{ invoice: BillingInvoice }> {
    const { data } = await apiClient.post<{ invoice: BillingInvoice }>(
      `/api/v1/admin/invoices/${encodeURIComponent(invoiceNo)}/issue`,
      issuedUrl ? { issuedUrl } : {}
    );
    return data;
  },

  async rejectInvoice(invoiceNo: string): Promise<{ invoice: BillingInvoice }> {
    const { data } = await apiClient.post<{ invoice: BillingInvoice }>(
      `/api/v1/admin/invoices/${encodeURIComponent(invoiceNo)}/reject`
    );
    return data;
  },

  async grantPlan(input: {
    userId: number;
    planCode: string;
    grantDays: number;
    note?: string;
  }): Promise<{ user: AdminUser; subscription: AdminGrantSubscription }> {
    const { data } = await apiClient.post<{ user: AdminUser; subscription: AdminGrantSubscription }>(
      '/api/v1/admin/grant-plan',
      {
        userId: input.userId,
        planCode: input.planCode,
        grantDays: input.grantDays,
        note: input.note,
      }
    );
    return data;
  },

  async stats(): Promise<AdminStats> {
    const { data } = await apiClient.get<AdminStats>('/api/v1/admin/stats');
    return data;
  },

  async listAuditLogs(input?: {
    action?: string;
    userId?: number;
    adminId?: number;
    limit?: number;
  }): Promise<{ logs: AuditLogEntry[]; count: number }> {
    const params: Record<string, string | number> = {};
    if (input?.action) params.action = input.action;
    if (typeof input?.userId === 'number') params.userId = input.userId;
    if (typeof input?.adminId === 'number') params.adminId = input.adminId;
    if (input?.limit) params.limit = input.limit;
    const { data } = await apiClient.get<{ logs: AuditLogEntry[]; count: number }>(
      '/api/v1/admin/audit-logs',
      { params }
    );
    return data;
  },
};
