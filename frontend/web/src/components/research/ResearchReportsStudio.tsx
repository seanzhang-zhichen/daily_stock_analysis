import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Pin, Trash2 } from 'lucide-react';
import { researchReportsApi, type ResearchReport } from '../../api/researchReports';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { Button, Card, Input, Loading } from '../common';
import { SettingsAlert } from '../settings';
import { cn } from '../../utils/cn';

const formatDate = (value: string | null): string => {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

export const ResearchReportsStudio: React.FC = () => {
  const [reports, setReports] = useState<ResearchReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [previewContent, setPreviewContent] = useState('');
  const [fullContent, setFullContent] = useState('');
  const [priceCredits, setPriceCredits] = useState('20');
  const [category, setCategory] = useState('策略');
  const [tagsText, setTagsText] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await researchReportsApi.mineList();
      setReports(data.reports);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const handleCreate = async () => {
    setSaving(true);
    setError(null);
    try {
      const tags = tagsText.split(',').map((tag) => tag.trim()).filter(Boolean);
      await researchReportsApi.create({
        title,
        summary,
        previewContent,
        fullContent,
        priceCredits: Math.max(0, Number.parseInt(priceCredits, 10) || 0),
        category: category.trim() || null,
        tags,
      });
      setTitle('');
      setSummary('');
      setPreviewContent('');
      setFullContent('');
      setPriceCredits('20');
      setCategory('策略');
      setTagsText('');
      await load();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setSaving(false);
    }
  };

  const handlePublish = async (report: ResearchReport) => {
    try {
      if (report.isPublished) await researchReportsApi.unpublish(report.id);
      else await researchReportsApi.publish(report.id);
      await load();
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  const handleDelete = async (report: ResearchReport) => {
    if (!window.confirm(`确认删除研报「${report.title}」？`)) return;
    try {
      await researchReportsApi.remove(report.id);
      await load();
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  const canCreate = title.trim() && summary.trim() && previewContent.trim() && fullContent.trim();

  return (
    <div className="research-studio space-y-6">
      {error ? <SettingsAlert title="操作失败" message={error.message} variant="error" /> : null}

      <Card className="research-studio-editor" variant="gradient">
        <h3 className="mb-3 text-sm font-semibold text-foreground">创建研报草稿</h3>
        <div className="grid gap-3 lg:grid-cols-2">
          <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="研报标题" />
          <div className="grid grid-cols-[1fr_8rem] gap-2">
            <Input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="分类" />
            <Input
              type="number"
              value={priceCredits}
              onChange={(event) => setPriceCredits(event.target.value)}
              placeholder="积分"
            />
          </div>
          <Input
            value={tagsText}
            onChange={(event) => setTagsText(event.target.value)}
            placeholder="标签，用英文逗号分隔"
            className="lg:col-span-2"
          />
          <textarea
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
            rows={3}
            placeholder="摘要"
            className="ui-input px-4 py-3 text-sm lg:col-span-2"
          />
          <textarea
            value={previewContent}
            onChange={(event) => setPreviewContent(event.target.value)}
            rows={5}
            placeholder="免费试读内容"
            className="ui-input px-4 py-3 text-sm"
          />
          <textarea
            value={fullContent}
            onChange={(event) => setFullContent(event.target.value)}
            rows={5}
            placeholder="完整研报内容"
            className="ui-input px-4 py-3 text-sm"
          />
        </div>
        <div className="mt-3">
          <Button type="button" size="sm" onClick={() => void handleCreate()} disabled={!canCreate} isLoading={saving}>
            创建草稿
          </Button>
        </div>
      </Card>

      {loading ? <Loading /> : null}
      <div className="space-y-3">
        {reports.map((report) => (
          <Card key={report.id} className="research-studio-report" hoverable>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-foreground">{report.title}</span>
                  <span className={cn(
                    'rounded-full px-2 py-0.5 text-xs',
                    report.isPublished ? 'bg-emerald-400/10 text-emerald-400' : 'bg-secondary-text/10 text-secondary-text'
                  )}>
                    {report.isPublished ? '已发布' : '草稿'}
                  </span>
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                    {report.priceCredits} 积分
                  </span>
                  {report.category ? <span className="text-xs text-secondary-text">{report.category}</span> : null}
                </div>
                <p className="line-clamp-2 text-xs leading-5 text-secondary-text">{report.summary}</p>
                <p className="mt-1 text-xs text-secondary-text/50">
                  创建 {formatDate(report.createdAt)} · 发布 {formatDate(report.publishedAt)} ·
                  {report.likes} 赞 / {report.dislikes} 踩 / {report.commentsCount} 评论
                </p>
              </div>
              <div className="flex shrink-0 gap-1.5">
                <Button
                  type="button"
                  size="sm"
                  variant={report.isPublished ? 'ghost' : 'primary'}
                  onClick={() => void handlePublish(report)}
                >
                  <Pin className="h-3.5 w-3.5" />
                  {report.isPublished ? '下架' : '发布'}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => void handleDelete(report)}
                  className="text-red-400 hover:text-red-300"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </Card>
        ))}
        {!loading && reports.length === 0 ? (
          <p className="text-sm text-secondary-text">暂无研报</p>
        ) : null}
      </div>
    </div>
  );
};
