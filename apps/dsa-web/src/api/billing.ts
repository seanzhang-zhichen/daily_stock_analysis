import apiClient from './index';

export type BillingPlan = {
  code: string;
  name: string;
  dailyAnalysisLimit: number;
  dailyAgentLimit: number;
  maxStocks: number;
  canWebhook: boolean;
  priceCents: number;
  currency: string;
  isActive: boolean;
};

export type BillingPlansResponse = {
  userModeEnabled: boolean;
  plans: BillingPlan[];
  currentPlan: {
    code: string;
    name: string;
    isPro: boolean;
    expiresAt: string | null;
  } | null;
};

export type BillingSubscriptionRecord = {
  id: number;
  planCode: string;
  source: string;
  startedAt: string | null;
  expiresAt: string | null;
  note: string | null;
  createdAt: string | null;
};

export type BillingSubscriptionResponse = {
  plan: {
    code: string;
    name: string;
    isPro: boolean;
    dailyAnalysisLimit: number;
    dailyAgentLimit: number;
    maxStocks: number;
    canWebhook: boolean;
    expiresAt: string | null;
    isActivePaid: boolean;
  };
  subscriptions: BillingSubscriptionRecord[];
};

export type BillingOrder = {
  orderNo: string;
  planCode: string;
  grantDays: number;
  amountCents: number;
  originalAmountCents: number;
  discountCents: number;
  couponCode: string | null;
  currency: string;
  provider: string;
  status: string;
  paidAt: string | null;
  expiresAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

export type BillingRefund = {
  refundNo: string;
  orderNo: string;
  amountCents: number;
  reason: string | null;
  status: string;
  createdAt: string | null;
  approvedAt: string | null;
  refundedAt: string | null;
};

export type BillingInvoice = {
  invoiceNo: string;
  orderNo: string;
  invoiceType: string;
  title: string;
  taxId: string | null;
  amountCents: number;
  email: string;
  status: string;
  issuedUrl: string | null;
  createdAt: string | null;
  issuedAt: string | null;
};

export const billingApi = {
  async listPlans(): Promise<BillingPlansResponse> {
    const { data } = await apiClient.get<BillingPlansResponse>('/api/v1/billing/plans');
    return data;
  },

  async getSubscription(): Promise<BillingSubscriptionResponse> {
    const { data } = await apiClient.get<BillingSubscriptionResponse>('/api/v1/billing/subscription');
    return data;
  },

  async createOrder(input: { planCode: string; provider: string; couponCode?: string }): Promise<{ order: BillingOrder }> {
    const { data } = await apiClient.post<{ order: BillingOrder }>('/api/v1/billing/orders', {
      planCode: input.planCode,
      provider: input.provider,
      couponCode: input.couponCode,
    });
    return data;
  },

  async getOrder(orderNo: string): Promise<{ order: BillingOrder }> {
    const { data } = await apiClient.get<{ order: BillingOrder }>(`/api/v1/billing/orders/${encodeURIComponent(orderNo)}`);
    return data;
  },

  async listOrders(): Promise<{ orders: BillingOrder[] }> {
    const { data } = await apiClient.get<{ orders: BillingOrder[] }>('/api/v1/billing/orders');
    return data;
  },

  async cancelOrder(orderNo: string): Promise<{ order: BillingOrder }> {
    const { data } = await apiClient.post<{ order: BillingOrder }>(`/api/v1/billing/orders/${encodeURIComponent(orderNo)}/cancel`);
    return data;
  },

  async payOrder(orderNo: string): Promise<{ provider: string; codeUrl: string; expiresAt: string | null; mock?: boolean; hint?: string }> {
    const { data } = await apiClient.post<{ provider: string; codeUrl: string; expiresAt: string | null; mock?: boolean; hint?: string }>(
      `/api/v1/billing/orders/${encodeURIComponent(orderNo)}/pay`
    );
    return data;
  },

  async mockPayOrder(orderNo: string): Promise<{ order: BillingOrder; alreadyPaid: boolean }> {
    const { data } = await apiClient.post<{ order: BillingOrder; alreadyPaid: boolean }>(
      `/api/v1/billing/orders/${encodeURIComponent(orderNo)}/mock-pay`
    );
    return data;
  },

  async requestRefund(input: { orderNo: string; reason: string }): Promise<{ refund: BillingRefund }> {
    const { data } = await apiClient.post<{ refund: BillingRefund }>('/api/v1/billing/refunds', {
      orderNo: input.orderNo,
      reason: input.reason,
    });
    return data;
  },

  async getRefund(refundNo: string): Promise<{ refund: BillingRefund }> {
    const { data } = await apiClient.get<{ refund: BillingRefund }>(`/api/v1/billing/refunds/${encodeURIComponent(refundNo)}`);
    return data;
  },

  async requestInvoice(input: {
    orderNo: string;
    invoiceType: 'personal' | 'company';
    title: string;
    email: string;
    taxId?: string;
  }): Promise<{ invoice: BillingInvoice }> {
    const { data } = await apiClient.post<{ invoice: BillingInvoice }>('/api/v1/billing/invoices', {
      orderNo: input.orderNo,
      invoiceType: input.invoiceType,
      title: input.title,
      email: input.email,
      taxId: input.taxId,
    });
    return data;
  },

  async listInvoices(): Promise<{ invoices: BillingInvoice[] }> {
    const { data } = await apiClient.get<{ invoices: BillingInvoice[] }>('/api/v1/billing/invoices');
    return data;
  },
};
