import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Activity, BellRing, CheckCircle2, FlaskConical, Loader2, Pause, Play, Plus, Trash2 } from 'lucide-react';
import { alertsApi } from '../api/alerts';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { Badge, Button, Card, EmptyState, Pagination } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import { StockAutocomplete } from '../components/StockAutocomplete';
import type {
  AlertNotificationItem,
  AlertRuleInput,
  AlertRuleItem,
  AlertRuleParameters,
  AlertRuleTestResponse,
  AlertSeverity,
  AlertTriggerItem,
} from '../types/alerts';
import { formatDateTime } from '../utils/format';

const PAGE_SIZE = 10;

type AlertType = 'price_cross' | 'price_change_percent' | 'volume_spike';
type RuleFormState = {
  target: string;
  stockName: string;
  alertType: AlertType;
  direction: 'above' | 'below' | 'up' | 'down';
  threshold: string;
  severity: AlertSeverity;
  enabled: boolean;
};

const INITIAL_FORM: RuleFormState = {
  target: '',
  stockName: '',
  alertType: 'price_cross',
  direction: 'above',
  threshold: '',
  severity: 'warning',
  enabled: true,
};

const ALERT_TYPE_OPTIONS: Array<{ value: AlertType; label: string; unit: string; directions: Array<{ value: RuleFormState['direction']; label: string }> }> = [
  {
    value: 'price_cross',
    label: '价格穿越',
    unit: '价格',
    directions: [
      { value: 'above', label: '上穿' },
      { value: 'below', label: '下破' },
    ],
  },
  {
    value: 'price_change_percent',
    label: '涨跌幅',
    unit: '百分比',
    directions: [
      { value: 'up', label: '上涨达到' },
      { value: 'down', label: '下跌达到' },
    ],
  },
  {
    value: 'volume_spike',
    label: '放量提醒',
    unit: '倍数',
    directions: [
      { value: 'above', label: '成交量达到' },
    ],
  },
];

const INPUT_CLASS = 'ui-input h-11 w-full px-4 text-sm disabled:cursor-not-allowed disabled:opacity-60';
const SELECT_CLASS = `${INPUT_CLASS} appearance-none pr-10`;

function getTypeLabel(value: string): string {
  if (value === 'price_cross') return '价格穿越';
  if (value === 'price_change_percent') return '涨跌幅';
  if (value === 'volume_spike') return '放量提醒';
  return value;
}

function getSeverityBadge(severity: string): 'info' | 'warning' | 'danger' | 'default' {
  if (severity === 'info') return 'info';
  if (severity === 'critical') return 'danger';
  if (severity === 'warning') return 'warning';
  return 'default';
}

function getStatusBadge(status: string): 'success' | 'warning' | 'danger' | 'default' {
  if (status === 'triggered' || status === 'sent' || status === 'completed') return 'success';
  if (status === 'failed' || status === 'evaluation_error') return 'danger';
  if (status === 'skipped' || status === 'not_triggered') return 'warning';
  return 'default';
}

function formatNumber(value: unknown, digits = 4): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Number(value.toFixed(digits)).toString();
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Number(parsed.toFixed(digits)).toString() : value;
  }
  return '--';
}

function formatRuleCondition(rule: AlertRuleItem): string {
  const params = rule.parameters || {};
  if (rule.alertType === 'price_cross') {
    const direction = params.direction === 'below' ? '下破' : '上穿';
    return `${direction} ${formatNumber(params.price)}`;
  }
  if (rule.alertType === 'price_change_percent') {
    const direction = params.direction === 'down' ? '下跌达到' : '上涨达到';
    return `${direction} ${formatNumber(params.changePct)}%`;
  }
  if (rule.alertType === 'volume_spike') {
    return `成交量达到均量 ${formatNumber(params.multiplier)} 倍`;
  }
  return JSON.stringify(params);
}

function buildParameters(form: RuleFormState): AlertRuleParameters {
  const value = Number(form.threshold);
  if (form.alertType === 'price_cross') {
    return { direction: form.direction === 'below' ? 'below' : 'above', price: value };
  }
  if (form.alertType === 'price_change_percent') {
    return { direction: form.direction === 'down' ? 'down' : 'up', changePct: value };
  }
  return { multiplier: value };
}

function buildRuleName(form: RuleFormState): string {
  const displayTarget = form.stockName ? `${form.target} ${form.stockName}` : form.target;
  const option = ALERT_TYPE_OPTIONS.find((item) => item.value === form.alertType);
  const direction = option?.directions.find((item) => item.value === form.direction)?.label || '';
  const suffix = form.alertType === 'price_change_percent' ? '%' : form.alertType === 'volume_spike' ? 'x' : '';
  return `${displayTarget} ${option?.label || '提醒'} ${direction} ${form.threshold}${suffix}`.trim();
}

const AlertsPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const [rules, setRules] = useState<AlertRuleItem[]>([]);
  const [triggers, setTriggers] = useState<AlertTriggerItem[]>([]);
  const [notifications, setNotifications] = useState<AlertNotificationItem[]>([]);
  const [ruleTotal, setRuleTotal] = useState(0);
  const [triggerTotal, setTriggerTotal] = useState(0);
  const [notificationTotal, setNotificationTotal] = useState(0);
  const [rulePage, setRulePage] = useState(1);
  const [triggerPage, setTriggerPage] = useState(1);
  const [notificationPage, setNotificationPage] = useState(1);
  const [isLoading, setIsLoading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [actionRuleId, setActionRuleId] = useState<number | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [successMessage, setSuccessMessage] = useState('');
  const [testResult, setTestResult] = useState<AlertRuleTestResponse | null>(null);
  const [form, setForm] = useState<RuleFormState>(INITIAL_FORM);
  const prefillTarget = searchParams.get('stock')?.trim() || '';
  const prefillName = searchParams.get('name')?.trim() || '';

  const selectedType = useMemo(
    () => ALERT_TYPE_OPTIONS.find((item) => item.value === form.alertType) || ALERT_TYPE_OPTIONS[0],
    [form.alertType],
  );

  const totalRulePages = Math.max(1, Math.ceil(ruleTotal / PAGE_SIZE));
  const totalTriggerPages = Math.max(1, Math.ceil(triggerTotal / PAGE_SIZE));
  const totalNotificationPages = Math.max(1, Math.ceil(notificationTotal / PAGE_SIZE));

  useEffect(() => {
    document.title = '提醒规则 - DSA';
  }, []);

  useEffect(() => {
    if (!prefillTarget) return;
    setForm((current) => {
      if (current.target.trim()) return current;
      return {
        ...current,
        target: prefillTarget,
        stockName: prefillName,
      };
    });
  }, [prefillName, prefillTarget]);

  const loadRules = useCallback(async (page = rulePage) => {
    const data = await alertsApi.listRules({ page, pageSize: PAGE_SIZE });
    setRules(data.items);
    setRuleTotal(data.total);
    setRulePage(data.page);
  }, [rulePage]);

  const loadTriggers = useCallback(async (page = triggerPage) => {
    const data = await alertsApi.listTriggers({ page, pageSize: PAGE_SIZE });
    setTriggers(data.items);
    setTriggerTotal(data.total);
    setTriggerPage(data.page);
  }, [triggerPage]);

  const loadNotifications = useCallback(async (page = notificationPage) => {
    const data = await alertsApi.listNotifications({ page, pageSize: PAGE_SIZE });
    setNotifications(data.items);
    setNotificationTotal(data.total);
    setNotificationPage(data.page);
  }, [notificationPage]);

  const refreshAll = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      await Promise.all([loadRules(), loadTriggers(), loadNotifications()]);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [loadRules, loadNotifications, loadTriggers]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const handleTypeChange = (nextType: AlertType) => {
    const option = ALERT_TYPE_OPTIONS.find((item) => item.value === nextType) || ALERT_TYPE_OPTIONS[0];
    setForm((current) => ({
      ...current,
      alertType: nextType,
      direction: option.directions[0].value,
      threshold: '',
    }));
  };

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const target = form.target.trim();
    const threshold = Number(form.threshold);
    if (!target || !Number.isFinite(threshold) || threshold <= 0) {
      setError({
        title: '输入有误',
        status: 400,
        message: '请填写股票代码和大于 0 的阈值。',
        rawMessage: '请填写股票代码和大于 0 的阈值。',
        category: 'http_error',
      });
      return;
    }

    const payload: AlertRuleInput = {
      name: buildRuleName({ ...form, target }),
      targetScope: 'single_symbol',
      target,
      alertType: form.alertType,
      parameters: buildParameters(form),
      severity: form.severity,
      enabled: form.enabled,
    };

    setIsCreating(true);
    setError(null);
    setSuccessMessage('');
    try {
      await alertsApi.createRule(payload);
      setForm(INITIAL_FORM);
      setSuccessMessage('提醒规则已创建。');
      await Promise.all([loadRules(1), loadTriggers(1), loadNotifications(1)]);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsCreating(false);
    }
  };

  const handleToggleRule = async (rule: AlertRuleItem) => {
    setActionRuleId(rule.id);
    setError(null);
    setSuccessMessage('');
    try {
      const updated = rule.enabled ? await alertsApi.disableRule(rule.id) : await alertsApi.enableRule(rule.id);
      setRules((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setSuccessMessage(rule.enabled ? '提醒规则已暂停。' : '提醒规则已启用。');
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActionRuleId(null);
    }
  };

  const handleTestRule = async (ruleId: number) => {
    setActionRuleId(ruleId);
    setError(null);
    setSuccessMessage('');
    try {
      const result = await alertsApi.testRule(ruleId);
      setTestResult(result);
      await loadTriggers(1);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActionRuleId(null);
    }
  };

  const handleDeleteRule = async (ruleId: number) => {
    setActionRuleId(ruleId);
    setError(null);
    setSuccessMessage('');
    try {
      await alertsApi.deleteRule(ruleId);
      setSuccessMessage('提醒规则已删除。');
      const nextPage = rules.length === 1 && rulePage > 1 ? rulePage - 1 : rulePage;
      await loadRules(nextPage);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActionRuleId(null);
    }
  };

  return (
    <StandardPageLayout>
      <header className="ui-page-header space-y-1">
        <p className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-text">ALERTS</p>
        <h1 className="text-2xl font-bold tracking-tight text-foreground">提醒规则</h1>
        <p className="text-sm text-secondary-text/80">
          为关注的股票设置价格、涨跌幅或放量提醒，并查看最近触发与通知投递记录。
        </p>
      </header>

      {error ? <SettingsAlert title="操作失败" message={error.message} variant="error" /> : null}
      {successMessage ? <SettingsAlert title="操作成功" message={successMessage} variant="success" /> : null}
      {testResult ? (
        <SettingsAlert
          title={testResult.triggered ? '测试已触发' : '测试未触发'}
          message={`${testResult.message}${testResult.observedValue != null ? ` 当前值: ${formatNumber(testResult.observedValue)}` : ''}`}
          variant={testResult.triggered ? 'success' : testResult.status === 'evaluation_error' ? 'error' : 'warning'}
        />
      ) : null}

      <Card title="新建提醒" subtitle="NEW RULE" className="alerts-rule-editor" variant="gradient">
        <form className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.2fr)_180px_160px_160px_140px_auto]" onSubmit={handleCreate}>
          <div>
            <p className="mb-1.5 text-xs text-secondary-text">股票</p>
            <StockAutocomplete
              value={form.target}
              onChange={(value) => setForm((current) => ({ ...current, target: value }))}
              onSubmit={(code, name) => setForm((current) => ({ ...current, target: code, stockName: name || '' }))}
              placeholder="输入股票代码或名称"
              disabled={isCreating}
            />
          </div>
          <div>
            <p className="mb-1.5 text-xs text-secondary-text">类型</p>
            <select
              className={SELECT_CLASS}
              value={form.alertType}
              disabled={isCreating}
              onChange={(event) => handleTypeChange(event.target.value as AlertType)}
            >
              {ALERT_TYPE_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </div>
          <div>
            <p className="mb-1.5 text-xs text-secondary-text">条件</p>
            <select
              className={SELECT_CLASS}
              value={form.direction}
              disabled={isCreating || form.alertType === 'volume_spike'}
              onChange={(event) => setForm((current) => ({ ...current, direction: event.target.value as RuleFormState['direction'] }))}
            >
              {selectedType.directions.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </div>
          <div>
            <p className="mb-1.5 text-xs text-secondary-text">{selectedType.unit}</p>
            <input
              className={INPUT_CLASS}
              type="number"
              min="0"
              step="0.0001"
              value={form.threshold}
              disabled={isCreating}
              placeholder={form.alertType === 'volume_spike' ? '2' : '10'}
              onChange={(event) => setForm((current) => ({ ...current, threshold: event.target.value }))}
            />
          </div>
          <div>
            <p className="mb-1.5 text-xs text-secondary-text">级别</p>
            <select
              className={SELECT_CLASS}
              value={form.severity}
              disabled={isCreating}
              onChange={(event) => setForm((current) => ({ ...current, severity: event.target.value as AlertSeverity }))}
            >
              <option value="info">信息</option>
              <option value="warning">警告</option>
              <option value="critical">紧急</option>
            </select>
          </div>
          <div className="flex items-end">
            <Button type="submit" variant="primary" isLoading={isCreating} disabled={isCreating}>
              <Plus className="h-4 w-4" /> 创建
            </Button>
          </div>
        </form>
      </Card>

      <Card title="规则列表" subtitle={`RULES (${ruleTotal})`}>
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-secondary-text">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中...
          </div>
        ) : rules.length === 0 ? (
          <EmptyState
            icon={<BellRing className="h-6 w-6" />}
            title="暂无提醒规则"
            description="创建第一条提醒后，系统会按后台监控流程记录触发和通知结果。"
          />
        ) : (
          <div className="space-y-4">
            <div className="alerts-table-wrapper overflow-x-auto rounded-xl border border-subtle">
              <table className="alerts-rules-table min-w-full divide-y divide-subtle text-sm">
                <thead className="bg-surface-muted/60 text-xs text-muted-text">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">规则</th>
                    <th className="px-3 py-2 text-left font-medium">类型</th>
                    <th className="px-3 py-2 text-left font-medium">条件</th>
                    <th className="px-3 py-2 text-left font-medium">级别</th>
                    <th className="px-3 py-2 text-left font-medium">状态</th>
                    <th className="px-3 py-2 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-subtle bg-surface/30">
                  {rules.map((rule) => {
                    const busy = actionRuleId === rule.id;
                    return (
                      <tr key={rule.id}>
                        <td className="px-3 py-3">
                          <div className="font-medium text-foreground">{rule.name}</div>
                          <Link
                            to={`/stocks/${encodeURIComponent(rule.target)}`}
                            className="mt-0.5 inline-flex font-mono text-xs text-secondary-text transition-colors hover:text-primary"
                          >
                            {rule.target}
                          </Link>
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 text-secondary-text">{getTypeLabel(rule.alertType)}</td>
                        <td className="whitespace-nowrap px-3 py-3 font-mono text-secondary-text">{formatRuleCondition(rule)}</td>
                        <td className="whitespace-nowrap px-3 py-3">
                          <Badge variant={getSeverityBadge(rule.severity)}>{rule.severity}</Badge>
                        </td>
                        <td className="whitespace-nowrap px-3 py-3">
                          <Badge variant={rule.enabled ? 'success' : 'default'}>{rule.enabled ? '启用' : '暂停'}</Badge>
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex justify-end gap-2">
                            <Button size="xsm" variant="outline" disabled={busy} onClick={() => void handleToggleRule(rule)}>
                              {rule.enabled ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                              {rule.enabled ? '暂停' : '启用'}
                            </Button>
                            <Button size="xsm" variant="outline" disabled={busy} onClick={() => void handleTestRule(rule.id)}>
                              <FlaskConical className="h-3.5 w-3.5" /> 测试
                            </Button>
                            <Button size="xsm" variant="danger-subtle" disabled={busy} onClick={() => void handleDeleteRule(rule.id)}>
                              <Trash2 className="h-3.5 w-3.5" /> 删除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination currentPage={rulePage} totalPages={totalRulePages} onPageChange={(page) => void loadRules(page)} />
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="最近触发" subtitle={`TRIGGERS (${triggerTotal})`}>
          {triggers.length === 0 ? (
            <EmptyState icon={<Activity className="h-6 w-6" />} title="暂无触发记录" description="测试规则或后台监控触发后会显示在这里。" />
          ) : (
            <div className="space-y-3">
              <div className="space-y-2">
                {triggers.map((item) => (
                  <div key={item.id} className="rounded-xl border border-border/60 bg-card/50 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <Link
                          to={`/stocks/${encodeURIComponent(item.target)}`}
                          className="font-mono text-sm text-foreground transition-colors hover:text-primary"
                        >
                          {item.target}
                        </Link>
                        <p className="mt-1 text-xs leading-5 text-secondary-text">{item.reason || '无触发说明'}</p>
                      </div>
                      <Badge variant={getStatusBadge(item.status)}>{item.status}</Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-text">
                      <span>当前值 {formatNumber(item.observedValue)}</span>
                      <span>阈值 {formatNumber(item.threshold)}</span>
                      <span>{formatDateTime(item.triggeredAt || item.dataTimestamp || undefined)}</span>
                    </div>
                  </div>
                ))}
              </div>
              <Pagination currentPage={triggerPage} totalPages={totalTriggerPages} onPageChange={(page) => void loadTriggers(page)} />
            </div>
          )}
        </Card>

        <Card title="通知投递" subtitle={`NOTIFICATIONS (${notificationTotal})`}>
          {notifications.length === 0 ? (
            <EmptyState icon={<CheckCircle2 className="h-6 w-6" />} title="暂无通知记录" description="提醒触发后的渠道投递结果会显示在这里。" />
          ) : (
            <div className="space-y-3">
              <div className="space-y-2">
                {notifications.map((item) => (
                  <div key={item.id} className="rounded-xl border border-border/60 bg-card/50 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-medium text-foreground">{item.channel}</div>
                        <p className="mt-1 text-xs leading-5 text-secondary-text">
                          {item.success ? '投递成功' : item.errorCode || item.diagnostics || '投递失败'}
                        </p>
                      </div>
                      <Badge variant={item.success ? 'success' : 'danger'}>{item.success ? '成功' : '失败'}</Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-text">
                      <span>触发 #{item.triggerId || '--'}</span>
                      <span>第 {item.attempt} 次</span>
                      <span>{item.latencyMs != null ? `${item.latencyMs}ms` : '--'}</span>
                      <span>{formatDateTime(item.createdAt || undefined)}</span>
                    </div>
                  </div>
                ))}
              </div>
              <Pagination currentPage={notificationPage} totalPages={totalNotificationPages} onPageChange={(page) => void loadNotifications(page)} />
            </div>
          )}
        </Card>
      </div>
    </StandardPageLayout>
  );
};

export default AlertsPage;
