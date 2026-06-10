import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Lock,
  MessageSquare,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
  Unlock,
} from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { researchReportsApi, type ResearchComment, type ResearchReport } from '../api/researchReports';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { Button, Card, Loading } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import { useAuth } from '../hooks';
import { cn } from '../utils/cn';

const formatDate = (value?: string | null): string => {
  if (!value) return '未发布';
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

const ReportBody: React.FC<{ text: string }> = ({ text }) => (
  <div className="ui-prose max-w-none whitespace-pre-wrap text-sm leading-7 text-secondary-text">
    {text}
  </div>
);

const ResearchReportsPage: React.FC = () => {
  const { effectiveLoggedIn, userMode, refreshStatus } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [reports, setReports] = useState<ResearchReport[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<ResearchReport | null>(null);
  const [comments, setComments] = useState<ResearchComment[]>([]);
  const [commentText, setCommentText] = useState('');
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const detailRef = useRef<HTMLDivElement | null>(null);
  const detailRequestSeq = useRef(0);

  const creditBalance = userMode?.user?.creditBalance ?? 0;
  const isResearchOperator = Boolean(userMode?.user?.isResearchOperator);
  const selectedIdFromQuery = useMemo(() => {
    const rawId = Number(searchParams.get('id'));
    return Number.isInteger(rawId) && rawId > 0 ? rawId : null;
  }, [searchParams]);

  useEffect(() => {
    document.title = '研报 - AlphaLens';
  }, []);

  const loadList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await researchReportsApi.list();
      setReports(data.reports);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (id: number) => {
    const requestSeq = detailRequestSeq.current + 1;
    detailRequestSeq.current = requestSeq;
    setDetailLoading(true);
    setError(null);
    try {
      const [report, commentData] = await Promise.all([
        researchReportsApi.get(id),
        researchReportsApi.comments(id),
      ]);
      if (detailRequestSeq.current !== requestSeq) return;
      setSelected(report);
      setComments(commentData.comments);
    } catch (err) {
      if (detailRequestSeq.current !== requestSeq) return;
      setError(getParsedApiError(err));
    } finally {
      if (detailRequestSeq.current === requestSeq) {
        setDetailLoading(false);
      }
    }
  }, []);

  useEffect(() => { void loadList(); }, [loadList]);

  useEffect(() => {
    if (reports.length === 0) {
      setSelectedId(null);
      setSelected(null);
      setComments([]);
      return;
    }
    setSelectedId((current) => {
      if (selectedIdFromQuery && reports.some((report) => report.id === selectedIdFromQuery)) {
        return selectedIdFromQuery;
      }
      if (current && reports.some((report) => report.id === current)) {
        return current;
      }
      return reports[0].id;
    });
  }, [reports, selectedIdFromQuery]);

  useEffect(() => {
    if (selected && selected.id !== selectedId) {
      setSelected(null);
      setComments([]);
    }
  }, [selected, selectedId]);

  useEffect(() => {
    if (selectedId !== null) void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  const selectedInList = useMemo(
    () => reports.find((report) => report.id === selectedId) ?? null,
    [reports, selectedId],
  );

  const visibleSelected = selected ?? selectedInList;

  const handleSelectReport = useCallback((id: number) => {
    setSelectedId(id);
    setSelected(null);
    setSearchParams({ id: String(id) });

    if (window.innerWidth < 1024) {
      window.setTimeout(() => {
        detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 0);
    }
  }, [setSearchParams]);

  const handlePurchase = async () => {
    if (!visibleSelected) return;
    if (!effectiveLoggedIn) {
      navigate(`/login?redirect=${encodeURIComponent(`/research-reports?id=${visibleSelected.id}`)}`);
      return;
    }
    setActing(true);
    setError(null);
    try {
      const res = await researchReportsApi.purchase(visibleSelected.id);
      setSelected(res.report);
      setReports((prev) => prev.map((item) => (item.id === res.report.id ? { ...item, ...res.report } : item)));
      await refreshStatus();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActing(false);
    }
  };

  const handleReact = async (reaction: 'like' | 'dislike') => {
    if (!visibleSelected) return;
    setActing(true);
    try {
      const nextReaction = visibleSelected.myReaction === reaction ? null : reaction;
      const report = await researchReportsApi.react(visibleSelected.id, nextReaction);
      setSelected(report);
      setReports((prev) => prev.map((item) => (item.id === report.id ? { ...item, ...report } : item)));
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActing(false);
    }
  };

  const handleComment = async () => {
    if (!visibleSelected || !commentText.trim()) return;
    setActing(true);
    try {
      const comment = await researchReportsApi.comment(visibleSelected.id, commentText.trim());
      setComments((prev) => [comment, ...prev]);
      setCommentText('');
      setSelected((current) => current ? { ...current, commentsCount: current.commentsCount + 1 } : current);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActing(false);
    }
  };

  return (
    <StandardPageLayout className="!max-w-[88rem]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">研报</h1>
          <p className="mt-1 text-sm text-secondary-text">
            运营精选观点，未解锁可先阅读试读内容。
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => void loadList()} disabled={loading}>
          <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
          刷新
        </Button>
        {isResearchOperator ? (
          <Button type="button" variant="primary" size="sm" onClick={() => navigate('/research-reports/studio')}>
            写研报
          </Button>
        ) : null}
      </div>

      {error ? <SettingsAlert title="操作失败" message={error.message} variant="error" /> : null}

      {loading ? (
        <Loading />
      ) : reports.length === 0 ? (
        <Card className="p-8 text-center text-sm text-secondary-text">暂无已发布研报</Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
          <div className="space-y-3">
            {reports.map((report) => (
              <button
                key={report.id}
                type="button"
                onClick={() => handleSelectReport(report.id)}
                aria-current={selectedId === report.id ? 'true' : undefined}
                aria-label={`查看研报：${report.title}`}
                className={cn(
                  'w-full rounded-lg border bg-card/60 p-4 text-left transition-colors',
                  selectedId === report.id
                    ? 'border-primary/40 bg-primary/8'
                    : 'border-border/60 hover:border-primary/25'
                )}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="rounded-full bg-secondary-text/10 px-2 py-0.5 text-xs text-secondary-text">
                    {report.category || '精选'}
                  </span>
                  <span className="inline-flex items-center gap-1 text-xs text-secondary-text">
                    {report.isUnlocked ? <Unlock className="h-3 w-3" /> : <Lock className="h-3 w-3" />}
                    {report.priceCredits > 0 ? `${report.priceCredits} 积分` : '免费'}
                  </span>
                </div>
                <h2 className="line-clamp-2 text-sm font-semibold text-foreground">{report.title}</h2>
                <p className="mt-2 line-clamp-3 text-xs leading-5 text-secondary-text">{report.summary}</p>
                <div className="mt-3 flex items-center gap-3 text-xs text-secondary-text/70">
                  <span>{formatDate(report.publishedAt)}</span>
                  <span>{report.likes} 赞</span>
                  <span>{report.commentsCount} 评论</span>
                </div>
              </button>
            ))}
          </div>

          <div ref={detailRef} className="scroll-mt-20">
            <Card className="p-5">
              {detailLoading && !visibleSelected ? <Loading /> : null}
              {visibleSelected ? (
                <div className="space-y-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                        {visibleSelected.category || '精选研报'}
                      </span>
                      {visibleSelected.tags.map((tag) => (
                        <span key={tag} className="rounded-full bg-secondary-text/10 px-2 py-0.5 text-xs text-secondary-text">
                          {tag}
                        </span>
                      ))}
                    </div>
                    <h2 className="text-lg font-semibold text-foreground">{visibleSelected.title}</h2>
                    <p className="mt-1 text-xs text-secondary-text">{formatDate(visibleSelected.publishedAt)}</p>
                  </div>
                  <div className="rounded-lg border border-border/60 bg-card/50 px-3 py-2 text-right">
                    <p className="text-xs text-secondary-text">我的积分</p>
                    <p className="text-sm font-semibold text-foreground">{creditBalance}</p>
                  </div>
                </div>

                <div className="rounded-lg border border-border/60 bg-secondary/30 p-4">
                  <p className="text-sm leading-6 text-secondary-text">{visibleSelected.summary}</p>
                </div>

                <section className="space-y-2">
                  <h3 className="text-sm font-semibold text-foreground">试读</h3>
                  <ReportBody text={visibleSelected.previewContent} />
                </section>

                {visibleSelected.isUnlocked ? (
                  <section className="space-y-2">
                    <h3 className="text-sm font-semibold text-foreground">完整研报</h3>
                    <ReportBody text={visibleSelected.fullContent || ''} />
                  </section>
                ) : (
                  <div className="rounded-lg border border-primary/20 bg-primary/8 p-4">
                    <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
                      <Lock className="h-4 w-4 text-primary" />
                      解锁完整研报
                    </div>
                    <p className="mb-3 text-sm text-secondary-text">
                      本篇需要 {visibleSelected.priceCredits} 积分。购买后可永久查看全文，并继续参与评论互动。
                    </p>
                    <Button
                      type="button"
                      variant="primary"
                      onClick={() => void handlePurchase()}
                      isLoading={acting}
                      disabled={acting}
                    >
                      <Unlock className="h-4 w-4" />
                      {effectiveLoggedIn ? '积分购买' : '登录后购买'}
                    </Button>
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-4">
                  <Button
                    type="button"
                    size="sm"
                    variant={visibleSelected.myReaction === 'like' ? 'primary' : 'outline'}
                    disabled={!effectiveLoggedIn || acting}
                    onClick={() => void handleReact('like')}
                  >
                    <ThumbsUp className="h-4 w-4" /> {visibleSelected.likes}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={visibleSelected.myReaction === 'dislike' ? 'primary' : 'outline'}
                    disabled={!effectiveLoggedIn || acting}
                    onClick={() => void handleReact('dislike')}
                  >
                    <ThumbsDown className="h-4 w-4" /> {visibleSelected.dislikes}
                  </Button>
                  <span className="inline-flex items-center gap-1 text-xs text-secondary-text">
                    <MessageSquare className="h-3.5 w-3.5" /> {visibleSelected.commentsCount} 条评论
                  </span>
                </div>

                <section className="space-y-3">
                  <h3 className="text-sm font-semibold text-foreground">评论</h3>
                  {effectiveLoggedIn ? (
                    <div className="space-y-2">
                      <textarea
                        value={commentText}
                        onChange={(event) => setCommentText(event.target.value)}
                        rows={3}
                        placeholder="写下你的看法"
                        className="w-full rounded-lg border border-border/60 bg-card/60 px-3 py-2 text-sm text-foreground placeholder:text-secondary-text/50 focus:outline-none focus:ring-1 focus:ring-primary/40"
                      />
                      <Button
                        type="button"
                        size="sm"
                        onClick={() => void handleComment()}
                        disabled={!commentText.trim() || acting}
                      >
                        发表评论
                      </Button>
                    </div>
                  ) : (
                    <p className="text-sm text-secondary-text">登录后可点赞、点踩和评论。</p>
                  )}
                  <div className="space-y-2">
                    {comments.length === 0 ? (
                      <p className="text-sm text-secondary-text">暂无评论</p>
                    ) : comments.map((comment) => (
                      <div key={comment.id} className="rounded-lg border border-border/60 bg-card/40 p-3">
                        <div className="mb-1 flex items-center justify-between gap-2 text-xs text-secondary-text/70">
                          <span>{comment.authorEmail || `用户 ${comment.userId}`}</span>
                          <span>{formatDate(comment.createdAt)}</span>
                        </div>
                        <p className="whitespace-pre-wrap text-sm leading-6 text-secondary-text">{comment.content}</p>
                      </div>
                    ))}
                  </div>
                </section>
                </div>
              ) : null}
            </Card>
          </div>
        </div>
      )}
    </StandardPageLayout>
  );
};

export default ResearchReportsPage;
