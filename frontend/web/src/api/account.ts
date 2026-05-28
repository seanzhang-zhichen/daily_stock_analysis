import apiClient from './index';

export type AccountUser = {
  id: number;
  email: string;
  plan: string;
  planExpiresAt: string | null;
  preferredModel: string | null;
  emailVerified: boolean;
  createdAt: string | null;
  lastLoginAt: string | null;
  isAdmin?: boolean;
  termsVersion?: string | null;
  needsReacceptTerms?: boolean;
};

export type AccountQuota = {
  analysisUsed: number;
  analysisLimit: number;
  analysisRemaining: number | null;
  agentUsed: number;
  agentLimit: number;
  agentRemaining: number | null;
};

export type AccountPlan = {
  code: string;
  name: string;
  isPro: boolean;
  dailyAnalysisLimit: number;
  dailyAgentLimit: number;
  maxStocks: number;
  canWebhook: boolean;
  expiresAt: string | null;
};

export type AccountRenewal = {
  planCode: string;
  expiresAt: string | null;
  daysRemaining: number;
  willExpireSoon: boolean;
  expired: boolean;
  thresholdDays: number;
};

export type AccountStatusResponse = {
  userModeEnabled: boolean;
  registrationEnabled: boolean;
  requireEmailVerification: boolean;
  inviteRequired: boolean;
  loggedIn: boolean;
  user: AccountUser | null;
  plan: AccountPlan | null;
  quota: AccountQuota | null;
  renewal: AccountRenewal | null;
  termsVersion?: string;
};

export type RegisterResponse = {
  user: AccountUser;
};

export type LoginResponse = {
  user: AccountUser;
};

export type RedeemSubscription = {
  id: number;
  planCode: string;
  source: string;
  startedAt: string | null;
  expiresAt: string | null;
  note: string | null;
};

export type RedeemResponse = {
  user: AccountUser;
  subscription: RedeemSubscription;
  plan: AccountPlan;
};

export type ModelPreferenceResponse = {
  models: string[];
  preferredModel: string | null;
  effectiveModel: string | null;
};

export type WatchlistItem = {
  stockCode: string;
  stockName: string | null;
};

export type WatchlistResponse = {
  stocks: WatchlistItem[];
  count: number;
  maxStocks: number;
};

export type NotificationPrefs = {
  dailyPushEnabled: boolean;
  emailEnabled: boolean;
  webhookUrl: string | null;
  webhookType: string | null;
};

export type NotificationPrefsResponse = {
  prefs: NotificationPrefs;
};

export const accountApi = {
  async getStatus(): Promise<AccountStatusResponse> {
    const { data } = await apiClient.get<AccountStatusResponse>('/api/v1/account/status');
    return data;
  },

  async register(input: {
    email: string;
    password: string;
    passwordConfirm: string;
    inviteCode?: string;
    termsAgreed?: boolean;
    termsVersion?: string;
  }): Promise<RegisterResponse> {
    const { data } = await apiClient.post<RegisterResponse>('/api/v1/account/register', {
      email: input.email,
      password: input.password,
      passwordConfirm: input.passwordConfirm,
      inviteCode: input.inviteCode,
      termsAgreed: input.termsAgreed ?? false,
      termsVersion: input.termsVersion,
    });
    return data;
  },

  async login(input: { email: string; password: string }): Promise<LoginResponse> {
    const { data } = await apiClient.post<LoginResponse>('/api/v1/account/login', {
      email: input.email,
      password: input.password,
    });
    return data;
  },

  async logout(): Promise<void> {
    await apiClient.post('/api/v1/account/logout');
  },

  async me(): Promise<{ user: AccountUser }> {
    const { data } = await apiClient.get<{ user: AccountUser }>('/api/v1/account/me');
    return data;
  },

  async verifyEmail(token: string): Promise<{ user: AccountUser }> {
    const { data } = await apiClient.post<{ user: AccountUser }>('/api/v1/account/verify-email', {
      token,
    });
    return data;
  },

  async requestPasswordReset(email: string): Promise<void> {
    await apiClient.post('/api/v1/account/request-password-reset', { email });
  },

  async resetPassword(input: {
    token: string;
    newPassword: string;
    newPasswordConfirm: string;
  }): Promise<void> {
    await apiClient.post('/api/v1/account/reset-password', {
      token: input.token,
      newPassword: input.newPassword,
      newPasswordConfirm: input.newPasswordConfirm,
    });
  },

  async changePassword(input: {
    currentPassword: string;
    newPassword: string;
    newPasswordConfirm: string;
  }): Promise<void> {
    await apiClient.post('/api/v1/account/change-password', {
      currentPassword: input.currentPassword,
      newPassword: input.newPassword,
      newPasswordConfirm: input.newPasswordConfirm,
    });
  },

  async redeem(code: string): Promise<RedeemResponse> {
    const { data } = await apiClient.post<RedeemResponse>('/api/v1/account/redeem', {
      code,
    });
    return data;
  },

  async getModelPreference(): Promise<ModelPreferenceResponse> {
    const { data } = await apiClient.get<ModelPreferenceResponse>('/api/v1/account/model-preference');
    return data;
  },

  async updateModelPreference(preferredModel: string | null): Promise<ModelPreferenceResponse> {
    const { data } = await apiClient.patch<ModelPreferenceResponse>('/api/v1/account/model-preference', {
      preferredModel,
    });
    return data;
  },

  // Watchlist
  async getWatchlist(): Promise<WatchlistResponse> {
    const { data } = await apiClient.get<WatchlistResponse>('/api/v1/account/watchlist');
    return data;
  },

  async addWatchlistStock(input: { stockCode: string; stockName?: string }): Promise<{ stock: WatchlistItem }> {
    const { data } = await apiClient.post<{ stock: WatchlistItem }>('/api/v1/account/watchlist', {
      stockCode: input.stockCode,
      stockName: input.stockName,
    });
    return data;
  },

  async setWatchlist(stocks: WatchlistItem[]): Promise<WatchlistResponse> {
    const { data } = await apiClient.put<WatchlistResponse>('/api/v1/account/watchlist', { stocks });
    return data;
  },

  async removeWatchlistStock(stockCode: string): Promise<void> {
    await apiClient.delete(`/api/v1/account/watchlist/${encodeURIComponent(stockCode)}`);
  },

  // Notification preferences
  async getNotificationPrefs(): Promise<NotificationPrefsResponse> {
    const { data } = await apiClient.get<NotificationPrefsResponse>('/api/v1/account/notification-prefs');
    return data;
  },

  async updateNotificationPrefs(input: {
    dailyPushEnabled?: boolean;
    emailEnabled?: boolean;
    webhookUrl?: string | null;
    webhookType?: string | null;
    clearWebhook?: boolean;
  }): Promise<NotificationPrefsResponse> {
    const { data } = await apiClient.patch<NotificationPrefsResponse>('/api/v1/account/notification-prefs', {
      dailyPushEnabled: input.dailyPushEnabled,
      emailEnabled: input.emailEnabled,
      webhookUrl: input.webhookUrl,
      webhookType: input.webhookType,
      clearWebhook: input.clearWebhook,
    });
    return data;
  },

  // Account deletion
  async getDeletionStatus(): Promise<{ hasPendingDeletion: boolean; deletionRequestedAt: string | null; coolingOffDays: number }> {
    const { data } = await apiClient.get('/api/v1/account/deletion');
    return data;
  },

  async requestDeletion(): Promise<void> {
    await apiClient.post('/api/v1/account/deletion');
  },

  async cancelDeletion(): Promise<{ ok: boolean; message: string }> {
    const { data } = await apiClient.delete('/api/v1/account/deletion');
    return data;
  },

  // Personal data export
  async requestDataExport(): Promise<{ ok: boolean; message: string }> {
    const { data } = await apiClient.post('/api/v1/account/data-export');
    return data;
  },
};
