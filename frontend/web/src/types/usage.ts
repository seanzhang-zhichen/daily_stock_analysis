export type UsagePeriod = 'today' | 'month' | 'all';

export type UsageCallTypeBreakdown = {
  callType: string;
  calls: number;
  totalTokens: number;
};

export type UsageModelBreakdown = {
  model: string;
  calls: number;
  totalTokens: number;
};

export type UsageSummaryResponse = {
  period: UsagePeriod | string;
  fromDate: string;
  toDate: string;
  totalCalls: number;
  totalTokens: number;
  byCallType: UsageCallTypeBreakdown[];
  byModel: UsageModelBreakdown[];
};
