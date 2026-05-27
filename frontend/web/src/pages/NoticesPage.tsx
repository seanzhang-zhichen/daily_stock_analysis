import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Bell,
  BellOff,
  CalendarDays,
  Info,
  Loader2,
  Pin,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import { Button, Card, Loading } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { noticesApi, type Notice } from '../api/notices';
import { cn } from '../utils/cn';

const formatDate = (value?: string | null): string => {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

const NOTICE_TYPE_CONFIG = {
  info: {
    icon: Info,
    label: '公告',
    accentColor: 'bg-primary',
    headerClass: 'bg-primary/8 dark:bg-primary/12',
    iconWrapClass: 'bg-primary/15 text-primary',
    badgeClass: 'bg-primary/12 text-primary ring-1 ring-primary/20',
    borderClass: 'border-primary/20',
  },
  warning: {
    icon: AlertTriangle,
    label: '警示',
    accentColor: 'bg-amber-400',
    headerClass: 'bg-amber-50 dark:bg-amber-400/10',
    iconWrapClass: 'bg-amber-100 text-amber-500 dark:bg-amber-400/20 dark:text-amber-400',
    badgeClass: 'bg-amber-100 text-amber-600 ring-1 ring-amber-300/50 dark:bg-amber-400/15 dark:text-amber-400 dark:ring-amber-400/25',
    borderClass: 'border-amber-200/60 dark:border-amber-400/20',
  },
  danger: {
    icon: ShieldAlert,
    label: '重要',
    accentColor: 'bg-red-400',
    headerClass: 'bg-red-50 dark:bg-red-400/10',
    iconWrapClass: 'bg-red-100 text-red-500 dark:bg-red-400/20 dark:text-red-400',
    badgeClass: 'bg-red-100 text-red-600 ring-1 ring-red-300/50 dark:bg-red-400/15 dark:text-red-400 dark:ring-red-400/25',
    borderClass: 'border-red-200/60 dark:border-red-400/20',
  },
} as const;

type NoticeCardProps = {
  notice: Notice;
};

const NoticeCard: React.FC<NoticeCardProps> = ({ notice }) => {
  const typeKey = (notice.noticeType as keyof typeof NOTICE_TYPE_CONFIG) ?? 'info';
  const cfg = NOTICE_TYPE_CONFIG[typeKey] ?? NOTICE_TYPE_CONFIG.info;
  const Icon = cfg.icon;

  return (
    <div className={cn(
      'overflow-hidden rounded-2xl border bg-card shadow-sm transition-shadow hover:shadow-md',
      cfg.borderClass,
    )}>
      {/* 卡片头部 */}
      <div className={cn('flex items-center gap-3 px-4 py-3', cfg.headerClass)}>
        <div className={cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-lg', cfg.iconWrapClass)}>
          <Icon className="h-4 w-4" />
        </div>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <h3 className="truncate text-sm font-semibold text-foreground">{notice.title}</h3>
          {notice.isPinned ? (
            <span className="flex shrink-0 items-center gap-1 rounded-full bg-primary/12 px-2 py-0.5 text-xs font-medium text-primary ring-1 ring-primary/20">
              <Pin className="h-2.5 w-2.5" />
              置顶
            </span>
          ) : null}
          <span className={cn('shrink-0 rounded-full px-2 py-0.5 text-xs font-medium', cfg.badgeClass)}>
            {cfg.label}
          </span>
        </div>
      </div>

      {/* 卡片正文 */}
      <div className="px-4 py-4">
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-secondary-text">
          {notice.content}
        </p>
        <div className="mt-3 flex items-center gap-1.5 text-xs text-secondary-text/50">
          <CalendarDays className="h-3 w-3" />
          <span>{formatDate(notice.publishedAt)}</span>
        </div>
      </div>
    </div>
  );
};

const NoticesPage: React.FC = () => {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const PAGE_SIZE = 20;

  const loadNotices = useCallback(async (targetPage: number, replace = false) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await noticesApi.list(targetPage, PAGE_SIZE);
      setNotices((prev) => (replace ? data : [...prev, ...data]));
      setHasMore(data.length === PAGE_SIZE);
    } catch {
      setError('加载公告失败，请稍后重试');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadNotices(1, true);
  }, [loadNotices]);

  const handleRefresh = () => {
    setPage(1);
    void loadNotices(1, true);
  };

  const handleLoadMore = () => {
    const nextPage = page + 1;
    setPage(nextPage);
    void loadNotices(nextPage, false);
  };

  const pinnedNotices = notices.filter((n) => n.isPinned);
  const normalNotices = notices.filter((n) => !n.isPinned);

  return (
    <StandardPageLayout>
      {/* 标题区 */}
      <div className="mb-6 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10">
            <Bell className="h-4.5 w-4.5 text-primary" />
          </div>
          <div>
            <h1 className="text-base font-semibold text-foreground">公告中心</h1>
            {notices.length > 0 ? (
              <p className="text-xs text-secondary-text/60">共 {notices.length} 条公告</p>
            ) : null}
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRefresh}
          disabled={isLoading}
          className="gap-1.5 text-xs text-secondary-text"
        >
          <RefreshCw className={cn('h-3.5 w-3.5', isLoading && 'animate-spin')} />
          刷新
        </Button>
      </div>

      {/* 加载中 */}
      {isLoading && notices.length === 0 ? (
        <div className="flex justify-center py-16">
          <Loading />
        </div>
      ) : null}

      {/* 错误提示 */}
      {error ? (
        <Card className="mb-4 border-red-200/60 bg-red-50/50 p-4 dark:border-red-400/20 dark:bg-red-400/5">
          <p className="text-sm text-red-500">{error}</p>
        </Card>
      ) : null}

      {/* 空状态 */}
      {!isLoading && !error && notices.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-secondary-text">
          <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-secondary/60">
            <BellOff className="h-7 w-7 opacity-40" />
          </div>
          <p className="text-sm font-medium text-secondary-text/70">暂无公告</p>
          <p className="mt-1 text-xs text-secondary-text/40">系统公告将在这里展示</p>
        </div>
      ) : null}

      {/* 公告列表 */}
      {notices.length > 0 ? (
        <div className="space-y-3">
          {pinnedNotices.length > 0 ? (
            <>
              {pinnedNotices.map((n) => (
                <NoticeCard key={n.id} notice={n} />
              ))}
              {normalNotices.length > 0 ? (
                <div className="my-4 flex items-center gap-3">
                  <div className="h-px flex-1 bg-border/40" />
                  <span className="text-xs text-secondary-text/40">历史公告</span>
                  <div className="h-px flex-1 bg-border/40" />
                </div>
              ) : null}
            </>
          ) : null}
          {normalNotices.map((n) => (
            <NoticeCard key={n.id} notice={n} />
          ))}
        </div>
      ) : null}

      {/* 加载更多 */}
      {hasMore && notices.length > 0 ? (
        <div className="mt-6 flex justify-center">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleLoadMore}
            disabled={isLoading}
            className="text-xs text-secondary-text"
          >
            {isLoading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
            加载更多
          </Button>
        </div>
      ) : null}
    </StandardPageLayout>
  );
};

export default NoticesPage;
