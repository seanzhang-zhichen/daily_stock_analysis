import React, { useEffect, useState } from 'react';
import { AlertTriangle, BellOff, CalendarDays, Info, Loader2, Pin, RefreshCw, ShieldAlert } from 'lucide-react';
import { Link } from 'react-router-dom';
import { noticesApi, type Notice } from '../../api/notices';
import { Button, Drawer } from '../common';
import { cn } from '../../utils/cn';

const NOTICE_CONFIG = {
  info: { icon: Info, label: '公告', tone: 'text-primary', bg: 'bg-primary/8 dark:bg-primary/12' },
  warning: { icon: AlertTriangle, label: '警示', tone: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-50 dark:bg-amber-400/10' },
  danger: { icon: ShieldAlert, label: '重要', tone: 'text-red-600 dark:text-red-400', bg: 'bg-red-50 dark:bg-red-400/10' },
} as const;

const formatDate = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '时间未知';

export const NoticeDrawer: React.FC<{ isOpen: boolean; onClose: () => void }> = ({ isOpen, onClose }) => {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setNotices(await noticesApi.list(1, 5));
    } catch {
      setError('加载公告失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (isOpen) void load(); }, [isOpen]);

  return (
    <Drawer isOpen={isOpen} onClose={onClose} title="公告" width="max-w-md">
      <div className="flex h-full flex-col">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-xs text-secondary-text">最新系统公告</p>
          <Button variant="ghost" size="xsm" onClick={() => void load()} disabled={loading} aria-label="刷新公告">
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          </Button>
        </div>
        {loading && notices.length === 0 ? <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-primary" /></div> : null}
        {error ? <div className="rounded-lg border border-red-200/60 bg-red-50/60 p-3 text-sm text-red-600 dark:border-red-400/20 dark:bg-red-400/10 dark:text-red-400">{error}</div> : null}
        {!loading && !error && notices.length === 0 ? <div className="flex flex-col items-center py-16 text-secondary-text"><BellOff className="mb-3 h-8 w-8 opacity-40" /><p className="text-sm">暂无公告</p></div> : null}
        <div className="space-y-3">
          {notices.map((notice) => {
            const config = NOTICE_CONFIG[notice.noticeType] ?? NOTICE_CONFIG.info;
            const Icon = config.icon;
            return <article key={notice.id} className="overflow-hidden rounded-xl border border-border/60 bg-card">
              <div className={cn('flex items-center gap-2 px-3 py-2.5', config.bg)}>
                <Icon className={cn('h-4 w-4 shrink-0', config.tone)} />
                <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{notice.title}</h3>
                {notice.isPinned ? <Pin className="h-3.5 w-3.5 shrink-0 text-primary" /> : null}
                <span className={cn('shrink-0 text-xs', config.tone)}>{config.label}</span>
              </div>
              <div className="px-3 py-3">
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-secondary-text">{notice.content}</p>
                <div className="mt-2 flex items-center gap-1.5 text-xs text-secondary-text/50"><CalendarDays className="h-3 w-3" />{formatDate(notice.publishedAt)}</div>
              </div>
            </article>;
          })}
        </div>
        <div className="mt-auto border-t border-border/50 pt-4">
          <Link to="/notices" onClick={onClose} className="ui-button ui-button-outline ui-button-size-sm flex w-full items-center justify-center">查看全部公告</Link>
        </div>
      </div>
    </Drawer>
  );
};
