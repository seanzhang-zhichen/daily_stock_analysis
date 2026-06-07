export type AlertSeverity = 'info' | 'warning' | 'critical';

export type AlertRuleParameters = {
  direction?: 'above' | 'below' | 'up' | 'down';
  price?: number;
  changePct?: number;
  multiplier?: number;
  [key: string]: unknown;
};

export type AlertRuleItem = {
  id: number;
  name: string;
  targetScope: 'single_symbol' | string;
  target: string;
  alertType: string;
  parameters: AlertRuleParameters;
  severity: AlertSeverity | string;
  enabled: boolean;
  source: string;
  cooldownPolicy?: Record<string, unknown> | null;
  notificationPolicy?: Record<string, unknown> | null;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type AlertRuleInput = {
  name?: string;
  targetScope?: 'single_symbol';
  target: string;
  alertType: string;
  parameters: AlertRuleParameters;
  severity: AlertSeverity;
  enabled: boolean;
  cooldownPolicy?: Record<string, unknown> | null;
  notificationPolicy?: Record<string, unknown> | null;
};

export type AlertRuleListResponse = {
  items: AlertRuleItem[];
  total: number;
  page: number;
  pageSize: number;
};

export type AlertRuleTestResponse = {
  ruleId: number;
  status: 'triggered' | 'not_triggered' | 'evaluation_error';
  triggered: boolean;
  observedValue?: unknown;
  message: string;
};

export type AlertTriggerItem = {
  id: number;
  ruleId?: number | null;
  target: string;
  observedValue?: number | null;
  threshold?: number | null;
  reason?: string | null;
  dataSource?: string | null;
  dataTimestamp?: string | null;
  triggeredAt?: string | null;
  status: string;
  diagnostics?: string | null;
};

export type AlertTriggerListResponse = {
  items: AlertTriggerItem[];
  total: number;
  page: number;
  pageSize: number;
};

export type AlertNotificationItem = {
  id: number;
  triggerId?: number | null;
  channel: string;
  attempt: number;
  success: boolean;
  errorCode?: string | null;
  retryable: boolean;
  latencyMs?: number | null;
  diagnostics?: string | null;
  createdAt?: string | null;
};

export type AlertNotificationListResponse = {
  items: AlertNotificationItem[];
  total: number;
  page: number;
  pageSize: number;
};
