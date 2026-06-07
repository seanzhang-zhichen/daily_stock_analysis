import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  AlertNotificationListResponse,
  AlertRuleInput,
  AlertRuleItem,
  AlertRuleListResponse,
  AlertRuleTestResponse,
  AlertTriggerListResponse,
} from '../types/alerts';

type ListRulesQuery = {
  enabled?: boolean;
  alertType?: string;
  target?: string;
  page?: number;
  pageSize?: number;
};

type ListTriggersQuery = {
  ruleId?: number;
  target?: string;
  status?: string;
  page?: number;
  pageSize?: number;
};

type ListNotificationsQuery = {
  triggerId?: number;
  channel?: string;
  success?: boolean;
  page?: number;
  pageSize?: number;
};

function buildRulePayload(input: Partial<AlertRuleInput>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (input.name !== undefined) payload.name = input.name;
  if (input.targetScope !== undefined) payload.target_scope = input.targetScope;
  if (input.target !== undefined) payload.target = input.target;
  if (input.alertType !== undefined) payload.alert_type = input.alertType;
  if (input.parameters !== undefined) {
    payload.parameters = {
      ...input.parameters,
      ...(input.parameters.changePct !== undefined ? { change_pct: input.parameters.changePct } : {}),
    };
    delete (payload.parameters as Record<string, unknown>).changePct;
  }
  if (input.severity !== undefined) payload.severity = input.severity;
  if (input.enabled !== undefined) payload.enabled = input.enabled;
  if (input.cooldownPolicy !== undefined) payload.cooldown_policy = input.cooldownPolicy;
  if (input.notificationPolicy !== undefined) payload.notification_policy = input.notificationPolicy;
  return payload;
}

function buildListParams(query: ListRulesQuery | ListTriggersQuery | ListNotificationsQuery): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') {
      return;
    }
    if (key === 'pageSize') {
      params.page_size = value;
    } else if (key === 'alertType') {
      params.alert_type = value;
    } else if (key === 'ruleId') {
      params.rule_id = value;
    } else {
      params[key] = value;
    }
  });
  return params;
}

export const alertsApi = {
  async listRules(query: ListRulesQuery = {}): Promise<AlertRuleListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/alerts/rules', {
      params: buildListParams(query),
    });
    return toCamelCase<AlertRuleListResponse>(response.data);
  },

  async createRule(input: AlertRuleInput): Promise<AlertRuleItem> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/alerts/rules', buildRulePayload({
      targetScope: 'single_symbol',
      ...input,
    }));
    return toCamelCase<AlertRuleItem>(response.data);
  },

  async updateRule(ruleId: number, input: Partial<AlertRuleInput>): Promise<AlertRuleItem> {
    const response = await apiClient.patch<Record<string, unknown>>(
      `/api/v1/alerts/rules/${ruleId}`,
      buildRulePayload(input),
    );
    return toCamelCase<AlertRuleItem>(response.data);
  },

  async deleteRule(ruleId: number): Promise<{ deleted: number }> {
    const response = await apiClient.delete<Record<string, unknown>>(`/api/v1/alerts/rules/${ruleId}`);
    return toCamelCase<{ deleted: number }>(response.data);
  },

  async enableRule(ruleId: number): Promise<AlertRuleItem> {
    const response = await apiClient.post<Record<string, unknown>>(`/api/v1/alerts/rules/${ruleId}/enable`);
    return toCamelCase<AlertRuleItem>(response.data);
  },

  async disableRule(ruleId: number): Promise<AlertRuleItem> {
    const response = await apiClient.post<Record<string, unknown>>(`/api/v1/alerts/rules/${ruleId}/disable`);
    return toCamelCase<AlertRuleItem>(response.data);
  },

  async testRule(ruleId: number): Promise<AlertRuleTestResponse> {
    const response = await apiClient.post<Record<string, unknown>>(`/api/v1/alerts/rules/${ruleId}/test`);
    return toCamelCase<AlertRuleTestResponse>(response.data);
  },

  async listTriggers(query: ListTriggersQuery = {}): Promise<AlertTriggerListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/alerts/triggers', {
      params: buildListParams(query),
    });
    return toCamelCase<AlertTriggerListResponse>(response.data);
  },

  async listNotifications(query: ListNotificationsQuery = {}): Promise<AlertNotificationListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/alerts/notifications', {
      params: buildListParams(query),
    });
    return toCamelCase<AlertNotificationListResponse>(response.data);
  },
};
