import type React from 'react';
import { Badge, Card, StatusDot } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import type { TaskInfo } from '../../types/analysis';
import { formatDateTime } from '../../utils/format';

/**
 * 任务项组件属性
 */
interface TaskItemProps {
  task: TaskInfo;
}

/**
 * 单个任务项
 */
const TaskItem: React.FC<TaskItemProps> = ({ task }) => {
  const isPending = task.status === 'pending';
  const isProcessing = task.status === 'processing';
  const statusLabel = isProcessing ? '分析中' : '等待中';
  const statusVariant = isProcessing ? 'info' : 'default';
  const statusTone = isProcessing ? 'info' : 'neutral';
  const progress = Math.max(0, Math.min(100, task.progress || 0));
  const phaseLabel = isPending
    ? '排队等待'
    : task.message || (progress >= 80 ? '整理报告' : progress >= 40 ? '生成分析' : '准备数据');
  const timeLabel = formatDateTime(task.startedAt || task.createdAt);

  return (
    <div className="rounded-xl border border-subtle bg-surface/80 px-3 py-3">
      <div className="flex items-start gap-3">
        {/* 状态图标 */}
        <div className="mt-1 shrink-0">
          {isProcessing ? (
            <StatusDot tone="info" pulse className="h-2.5 w-2.5" aria-label="任务进行中" />
          ) : isPending ? (
            <StatusDot tone="neutral" className="h-2.5 w-2.5" aria-label="任务等待中" />
          ) : null}
        </div>

        {/* 任务信息 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <span className="text-sm font-medium text-foreground truncate">
                {task.stockName || task.stockCode}
              </span>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-text">
                <span className="font-mono">{task.stockCode}</span>
                <span aria-hidden="true">·</span>
                <span>{timeLabel}</span>
              </div>
            </div>
            <Badge
              variant={statusVariant}
              className="shrink-0 justify-center gap-1.5 shadow-none"
              aria-label={`任务状态：${statusLabel}`}
            >
              <StatusDot tone={statusTone} pulse={isProcessing} className="h-1.5 w-1.5" />
              {statusLabel}
            </Badge>
          </div>
          <div className="mt-2 flex items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate text-secondary-text">
              {phaseLabel}
            </span>
            <span className="shrink-0 text-[11px] text-muted-text tabular-nums">
              {progress}%
            </span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div
              className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-muted"
              role="progressbar"
              aria-label={`${task.stockName || task.stockCode} 分析进度`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress}
            >
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

/**
 * 任务面板属性
 */
interface TaskPanelProps {
  /** 任务列表 */
  tasks: TaskInfo[];
  /** 是否显示 */
  visible?: boolean;
  /** 标题 */
  title?: string;
  /** 自定义类名 */
  className?: string;
}

/**
 * 任务面板组件
 * 显示进行中的分析任务列表
 */
export const TaskPanel: React.FC<TaskPanelProps> = ({
  tasks,
  visible = true,
  title = '分析任务',
  className = '',
}) => {
  // 筛选活跃任务（pending 和 processing）
  const activeTasks = tasks.filter(
    (t) => t.status === 'pending' || t.status === 'processing'
  );

  // 无任务或不可见时不渲染
  if (!visible || activeTasks.length === 0) {
    return null;
  }

  const pendingCount = activeTasks.filter((t) => t.status === 'pending').length;
  const processingCount = activeTasks.filter((t) => t.status === 'processing').length;

  return (
    <Card
      variant="bordered"
      padding="none"
      className={`overflow-hidden ${className}`}
    >
      <div className="border-b border-subtle px-3 py-3">
        <DashboardPanelHeader
          className="mb-0"
          title={title}
          titleClassName="text-sm font-medium"
          leading={(
            <svg className="h-4 w-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
          )}
          headingClassName="items-center"
          actions={(
            <div className="flex items-center gap-2 text-xs text-muted-text">
              {processingCount > 0 && (
                <span className="flex items-center gap-1">
                  <StatusDot tone="info" pulse className="h-1.5 w-1.5" aria-label="进行中任务" />
                  {processingCount} 进行中
                </span>
              )}
              {pendingCount > 0 ? (
                <span className="flex items-center gap-1">
                  <StatusDot tone="neutral" className="h-1.5 w-1.5" aria-label="等待中任务" />
                  {pendingCount} 等待中
                </span>
              ) : null}
            </div>
          )}
        />
      </div>

      <div className="max-h-72 overflow-y-auto p-2">
        <div className="space-y-2">
          {activeTasks.map((task) => (
            <TaskItem key={task.taskId} task={task} />
          ))}
        </div>
      </div>
    </Card>
  );
};

export default TaskPanel;
