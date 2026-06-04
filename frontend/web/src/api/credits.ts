import apiClient from './index';

export type CreditPackage = {
  code: string;
  name: string;
  creditAmount: number;
  priceCents: number;
  currency: string;
  isActive: boolean;
  sortOrder: number;
};

export type CreditOrder = {
  orderNo: string;
  packageCode: string;
  creditAmount: number;
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

export const creditsApi = {
  async listPackages(): Promise<{ packages: CreditPackage[] }> {
    const { data } = await apiClient.get<{ packages: CreditPackage[] }>('/api/v1/credits/packages');
    return data;
  },

  async createOrder(input: { packageCode: string; provider: string; couponCode?: string }): Promise<{ order: CreditOrder }> {
    const { data } = await apiClient.post<{ order: CreditOrder }>('/api/v1/credits/orders', {
      packageCode: input.packageCode,
      provider: input.provider,
      couponCode: input.couponCode,
    });
    return data;
  },

  async getOrder(orderNo: string): Promise<{ order: CreditOrder }> {
    const { data } = await apiClient.get<{ order: CreditOrder }>(`/api/v1/credits/orders/${encodeURIComponent(orderNo)}`);
    return data;
  },

  async cancelOrder(orderNo: string): Promise<{ order: CreditOrder }> {
    const { data } = await apiClient.post<{ order: CreditOrder }>(`/api/v1/credits/orders/${encodeURIComponent(orderNo)}/cancel`);
    return data;
  },

  async payOrder(orderNo: string): Promise<{ provider: string; codeUrl: string; expiresAt: string | null; mock?: boolean; hint?: string }> {
    const { data } = await apiClient.post<{ provider: string; codeUrl: string; expiresAt: string | null; mock?: boolean; hint?: string }>(
      `/api/v1/credits/orders/${encodeURIComponent(orderNo)}/pay`
    );
    return data;
  },

  async mockPayOrder(orderNo: string): Promise<{ order: CreditOrder; alreadyPaid: boolean }> {
    const { data } = await apiClient.post<{ order: CreditOrder; alreadyPaid: boolean }>(
      `/api/v1/credits/orders/${encodeURIComponent(orderNo)}/mock-pay`
    );
    return data;
  },
};
