import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, CircleDashed, Clock3, Loader2, RefreshCw, RotateCcw, XCircle } from 'lucide-react';
import { analysisApi } from '../api/analysis';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { Badge, Button, Card, EmptyState } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import type { TaskInfo, TaskListResponse } from '../types/analysis';
import { formatDateTime, formatReportType } from '../utils/format';

type StatusFilter = 'all' | 'active' | 'completed' | 'failed';

const STATUS_FILTERS: Array<{ key: StatusFilter; label: string; status?: string }> = [
  { key: 'all', label: '全部' },
  { key: 'active', label: '进行中', status: 'pending,processing' },
  { key: 'completed', label: '已完成', status: 'completed' },
  { key: 'failed', label: '失败', status: 'failed' },
];

const LIMIT_OPTIONS = [20, 50, 100] as const;
const SELECT_CLASS = 'ui-input h-10 appearance-none px-3 text-sm disabled:cursor-not-allowed disabled:opacity-60';

function getStatusLabel(status: TaskInfo['status']): string {
  if (status === 'pending') return '等待中';
  if (status === 'processing') return '分析中';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
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

function getTaskDescription(task: TaskInfo): string {
  if (task.error) return task.error;
  if (task.message) return task.message;
  if (task.status === 'completed') return '分析任务已完成。';
  if (task.status === 'failed') return '分析任务执行失败。';
  if (task.status === 'processing') return '正在执行分析流程。';
  return '任务正在队列中等待执行。';
}

const TasksPage: React.FC = () => {
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
    document.title = '任务中心 - DSA';
  }, []);

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
          <h1 className="text-2xl font-bold tracking-tight text-foreground">任务中心</h1>
          <p className="text-sm text-secondary-text/80">
            查看异步分析任务的排队、执行、完成和失败状态。
          </p>
        </div>
        <Button variant="outline" onClick={() => void loadTasks()} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> 刷新
        </Button>
      </div>

      {error ? <SettingsAlert title="加载失败" message={error.message} variant="error" /> : null}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <Card padding="sm">
          <p className="text-xs text-muted-text">任务总数</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{data?.total ?? '--'}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">等待中</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{data?.pending ?? '--'}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">分析中</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{data?.processing ?? '--'}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">当前列表</p>
          <p className="mt-1 text-sm font-medium text-secondary-text">
            {completedCount} 完成 / {failedCount} 失败
          </p>
        </Card>
      </div>

      <Card title="任务列表" subtitle={`RECENT (${tasks.length})`}>
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
                {item.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 text-sm text-secondary-text">
            <span>数量</span>
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
            <Loader2 className="h-4 w-4 animate-spin" /> 加载任务中...
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState
            icon={<Clock3 className="h-6 w-6" />}
            title="暂无任务"
            description="提交股票分析或大盘复盘后，任务会显示在这里。"
            action={(
              <Link to="/">
                <Button variant="primary">
                  <RotateCcw className="h-4 w-4" /> 返回首页
                </Button>
              </Link>
            )}
          />
        ) : (
          <div className="overflow-x-auto rounded-xl border border-subtle">
            <table className="min-w-full divide-y divide-subtle text-sm">
              <thead className="bg-surface-muted/60 text-xs text-muted-text">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">任务</th>
                  <th className="px-3 py-2 text-left font-medium">状态</th>
                  <th className="px-3 py-2 text-left font-medium">进度</th>
                  <th className="px-3 py-2 text-left font-medium">类型</th>
                  <th className="px-3 py-2 text-left font-medium">时间</th>
                  <th className="px-3 py-2 text-left font-medium">说明</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-subtle bg-surface/30">
                {tasks.map((task) => {
                  const progress = Math.max(0, Math.min(100, task.progress || 0));
                  const displayCode = task.stockCode === 'market_review' ? '大盘复盘' : task.stockCode;
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
                        <Badge variant={getStatusVariant(task.status)}>{getStatusLabel(task.status)}</Badge>
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
                      <td className="min-w-[220px] px-3 py-3 text-xs text-secondary-text">{getTaskDescription(task)}</td>
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
