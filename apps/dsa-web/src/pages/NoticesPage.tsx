import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Bell,
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
    containerClass: 'border-l-4 border-l-primary/60 bg-primary/5',
    iconClass: 'text-primary',
    badgeClass: 'bg-primary/10 text-primary',
  },
  warning: {
    icon: AlertTriangle,
    label: '警示',
    containerClass: 'border-l-4 border-l-amber-400/70 bg-amber-400/5',
    iconClass: 'text-amber-400',
    badgeClass: 'bg-amber-400/10 text-amber-400',
  },
  danger: {
    icon: ShieldAlert,
    label: '重要',
    containerClass: 'border-l-4 border-l-red-400/70 bg-red-400/5',
    iconClass: 'text-red-400',
    badgeClass: 'bg-red-400/10 text-red-400',
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
    <div className={cn('rounded-2xl p-4 sm:p-5', cfg.containerClass)}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5 shrink-0">
          <Icon className={cn('h-5 w-5', cfg.iconClass)} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground">{notice.title}</h3>
            {notice.isPinned ? (
              <span className="flex items-center gap-0.5 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                <Pin className="h-2.5 w-2.5" />
                置顶
              </span>
            ) : null}
            <span className={cn('rounded-full px-2 py-0.5 text-xs', cfg.badgeClass)}>
              {cfg.label}
            </span>
          </div>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-secondary-text">
            {notice.content}
          </p>
          <p className="mt-2 text-xs text-secondary-text/60">
            {formatDate(notice.publishedAt)}
          </p>
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
        <div className="flex items-center gap-2">
          <Bell className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold text-foreground">公告中心</h1>
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
        <Card className="mb-4 p-4">
          <p className="text-sm text-red-400">{error}</p>
        </Card>
      ) : null}

      {/* 空状态 */}
      {!isLoading && !error && notices.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-secondary-text">
          <Bell className="mb-3 h-10 w-10 opacity-30" />
          <p className="text-sm">暂无公告</p>
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
                <div className="my-1 flex items-center gap-2">
                  <div className="h-px flex-1 bg-border/40" />
                  <span className="text-xs text-secondary-text/50">历史公告</span>
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
