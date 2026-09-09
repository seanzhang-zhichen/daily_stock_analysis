import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, CircleDashed, Clock3, Loader2, RefreshCw, RotateCcw, XCircle } from 'lucide-react';
import { analysisApi } from '../api/analysis';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { Badge, Button, Card, EmptyState } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { TaskInfo, TaskListResponse } from '../types/analysis';
import { formatDateTime, formatReportType } from '../utils/format';
import type { UiTextKey } from '../i18n/uiText';

type StatusFilter = 'all' | 'active' | 'completed' | 'failed';

const STATUS_FILTERS: Array<{ key: StatusFilter; labelKey: UiTextKey; status?: string }> = [
  { key: 'all', labelKey: 'tasks.all' },
  { key: 'active', labelKey: 'tasks.active', status: 'pending,processing' },
  { key: 'completed', labelKey: 'tasks.completed', status: 'completed' },
  { key: 'failed', labelKey: 'tasks.failed', status: 'failed' },
];

const LIMIT_OPTIONS = [20, 50, 100] as const;
const SELECT_CLASS = 'ui-input h-10 appearance-none px-3 text-sm disabled:cursor-not-allowed disabled:opacity-60';

function getStatusLabel(status: TaskInfo['status'], t: (key: UiTextKey) => string): string {
  if (status === 'pending') return t('tasks.pending');
  if (status === 'processing') return t('tasks.processing');
  if (status === 'completed') return t('tasks.completed');
  if (status === 'failed') return t('tasks.failed');
  return status;
}

function getStatusVariant(status: TaskInfo['status']): 'default' | 'info' | 'success' | 'danger' {
  if (status === 'processing') return 'info';
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'danger';
  return 'default';
}

function getStatusIcon(status: TaskInfo['status']): React.ReactNode {
  if (status === 'processing') return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
  if (status === 'completed') return <CheckCircle2 className="h-4 w-4 text-success" />;
  if (status === 'failed') return <XCircle className="h-4 w-4 text-danger" />;
  return <CircleDashed className="h-4 w-4 text-muted-text" />;
}

function getTaskTime(task: TaskInfo): string {
  return formatDateTime(task.completedAt || task.startedAt || task.createdAt);
}

function getTaskDescription(task: TaskInfo, t: (key: UiTextKey) => string): string {
  if (task.error) return task.error;
  if (task.message) return task.message;
  if (task.status === 'completed') return t('tasks.completedDescription');
  if (task.status === 'failed') return t('tasks.failedDescription');
  if (task.status === 'processing') return t('tasks.processingDescription');
  return t('tasks.pendingDescription');
}

const TasksPage: React.FC = () => {
  const { t } = useUiLanguage();
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [limit, setLimit] = useState<(typeof LIMIT_OPTIONS)[number]>(50);
  const [data, setData] = useState<TaskListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const activeFilter = useMemo(
    () => STATUS_FILTERS.find((item) => item.key === filter) || STATUS_FILTERS[0],
    [filter],
  );

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await analysisApi.getTasks({
        status: activeFilter.status,
        limit,
      });
      setData(next);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [activeFilter.status, limit]);

  useEffect(() => {
    document.title = `${t('tasks.title')} - AlphaLens`;
  }, [t]);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  const tasks = data?.tasks || [];
  const completedCount = tasks.filter((task) => task.status === 'completed').length;
  const failedCount = tasks.filter((task) => task.status === 'failed').length;

  return (
    <StandardPageLayout>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-text">TASKS</p>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">{t('tasks.title')}</h1>
          <p className="text-sm text-secondary-text/80">
            {t('tasks.description')}
          </p>
        </div>
        <Button variant="outline" onClick={() => void loadTasks()} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> {t('tasks.refresh')}
        </Button>
      </div>

      {error ? <SettingsAlert title={t('tasks.loadFailed')} message={error.message} variant="error" /> : null}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <Card padding="sm">
          <p className="text-xs text-muted-text">{t('tasks.total')}</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{data?.total ?? '--'}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">{t('tasks.pending')}</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{data?.pending ?? '--'}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">{t('tasks.processing')}</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{data?.processing ?? '--'}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">{t('tasks.current')}</p>
          <p className="mt-1 text-sm font-medium text-secondary-text">
            {completedCount} {t('tasks.completed')} / {failedCount} {t('tasks.failed')}
          </p>
        </Card>
      </div>

      <Card title={t('tasks.list')} subtitle={`RECENT (${tasks.length})`}>
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {STATUS_FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                  filter === item.key
                    ? 'border-primary/40 bg-primary/10 text-primary'
                    : 'border-border/60 bg-card/50 text-secondary-text hover:text-foreground'
                }`}
                onClick={() => setFilter(item.key)}
              >
                {t(item.labelKey)}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 text-sm text-secondary-text">
            <span>{t('tasks.count')}</span>
            <select
              className={SELECT_CLASS}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value) as (typeof LIMIT_OPTIONS)[number])}
            >
              {LIMIT_OPTIONS.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </div>
        </div>

        {loading && tasks.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-secondary-text">
            <Loader2 className="h-4 w-4 animate-spin" /> {t('tasks.loading')}
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState
            icon={<Clock3 className="h-6 w-6" />}
            title={t('tasks.emptyTitle')}
            description={t('tasks.emptyDescription')}
            action={(
              <Link to="/">
                <Button variant="primary">
                  <RotateCcw className="h-4 w-4" /> {t('tasks.backHome')}
                </Button>
              </Link>
            )}
          />
        ) : (
          <div className="overflow-x-auto rounded-xl border border-subtle">
            <table className="min-w-full divide-y divide-subtle text-sm">
              <thead className="bg-surface-muted/60 text-xs text-muted-text">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">{t('tasks.task')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('tasks.status')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('tasks.progress')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('tasks.type')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('tasks.time')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('tasks.detail')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-subtle bg-surface/30">
                {tasks.map((task) => {
                  const progress = Math.max(0, Math.min(100, task.progress || 0));
                  const displayCode = task.stockCode === 'market_review' ? t('tasks.marketReview') : task.stockCode;
                  return (
                    <tr key={task.taskId}>
                      <td className="min-w-[180px] px-3 py-3">
                        <div className="flex items-start gap-2">
                          {getStatusIcon(task.status)}
                          <div className="min-w-0">
                            <div className="font-medium text-foreground">{task.stockName || displayCode}</div>
                            {task.stockCode !== 'market_review' ? (
                              <Link
                                to={`/stocks/${encodeURIComponent(task.stockCode)}`}
                                className="mt-0.5 inline-flex font-mono text-xs text-secondary-text transition-colors hover:text-primary"
                              >
                                {task.stockCode}
                              </Link>
                            ) : (
                              <div className="mt-0.5 text-xs text-secondary-text">market_review</div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-3 py-3">
                        <Badge variant={getStatusVariant(task.status)}>{getStatusLabel(task.status, t)}</Badge>
                      </td>
                      <td className="min-w-[160px] px-3 py-3">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-muted">
                            <div className="h-full rounded-full bg-primary" style={{ width: `${progress}%` }} />
                          </div>
                          <span className="w-9 text-right text-xs tabular-nums text-muted-text">{progress}%</span>
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-3 py-3 text-secondary-text">{formatReportType(task.reportType)}</td>
                      <td className="whitespace-nowrap px-3 py-3 text-xs text-muted-text">{getTaskTime(task)}</td>
                      <td className="min-w-[220px] px-3 py-3 text-xs text-secondary-text">{getTaskDescription(task, t)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </StandardPageLayout>
  );
};

export default TasksPage;
