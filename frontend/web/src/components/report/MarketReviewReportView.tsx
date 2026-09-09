import React from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BarChart3, TrendingDown, TrendingUp } from 'lucide-react';
import type { AnalysisReport, MarketReviewPayload, SectorRankingItem } from '../../types/analysis';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface MarketReviewReportViewProps { report: AnalysisReport; }

const getPayload = (report: AnalysisReport): MarketReviewPayload | null => {
  const payload = report.details?.contextSnapshot?.marketReviewPayload;
  return payload && typeof payload === 'object' ? payload : null;
};
const number = (value: unknown, digits = 0): string => {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : '-';
};
const percent = (value: unknown): string => {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? `${parsed > 0 ? '+' : ''}${parsed.toFixed(2)}%` : '-';
};
const Rankings: React.FC<{ title: string; values?: SectorRankingItem[]; positive: boolean }> = ({ title, values, positive }) => {
  if (!values?.length) return null;
  const Icon = positive ? TrendingUp : TrendingDown;
  return <div className="min-w-0"><div className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground"><Icon className={positive ? 'h-4 w-4 text-success' : 'h-4 w-4 text-danger'} aria-hidden="true" />{title}</div><div className="space-y-1.5">{values.slice(0, 5).map((item, index) => <div key={`${item.name}-${index}`} className="flex items-center justify-between gap-3 text-sm"><span className="min-w-0 truncate text-secondary-text">{index + 1}. {item.name}</span><span className={positive ? 'shrink-0 text-success' : 'shrink-0 text-danger'}>{percent(item.changePct)}</span></div>)}</div></div>;
};

export const MarketReviewReportView: React.FC<MarketReviewReportViewProps> = ({ report }) => {
  const { t } = useUiLanguage();
  const payload = getPayload(report);
  const markdown = payload?.markdownReport || report.details?.newsContent || '';
  const breadth = payload?.breadth;
  const sections = payload?.sections?.filter((section) => section.markdown?.trim()) || [];
  const overview = breadth ? [[t('marketReview.upCount'), number(breadth.upCount)], [t('marketReview.downCount'), number(breadth.downCount)], [t('marketReview.limitCount'), `${number(breadth.limitUpCount)} / ${number(breadth.limitDownCount)}`], [t('marketReview.turnover'), `${number(breadth.totalAmount, 0)} ${breadth.turnoverUnit || ''}`.trim()]] : [];
  return <div className="space-y-5 pb-8 animate-fade-in">
    <section className="rounded-lg border border-subtle bg-card p-5"><div className="flex items-start gap-3"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><BarChart3 className="h-5 w-5" aria-hidden="true" /></span><div className="min-w-0"><h2 className="text-xl font-semibold text-foreground">{payload?.title || report.meta.stockName || t('marketReview.defaultTitle')}</h2><p className="mt-1 text-sm text-secondary-text">{payload?.date || report.meta.createdAt}</p>{report.summary?.analysisSummary ? <p className="mt-3 text-sm leading-6 text-foreground">{report.summary.analysisSummary}</p> : null}</div></div></section>
    {breadth || payload?.indices?.length ? <section className="rounded-lg border border-subtle bg-card p-5"><h3 className="mb-4 text-base font-semibold text-foreground">{t('marketReview.structure')}</h3>{overview.length ? <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{overview.map(([label, value]) => <div key={label} className="rounded-md border border-subtle px-3 py-2.5"><p className="text-xs text-muted-text">{label}</p><p className="mt-1 text-base font-semibold text-foreground">{value}</p></div>)}</div> : null}{payload?.indices?.length ? <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[460px] text-left text-sm"><thead className="border-b border-subtle text-xs text-muted-text"><tr><th className="pb-2 font-medium">{t('marketReview.index')}</th><th className="pb-2 font-medium">{t('marketReview.current')}</th><th className="pb-2 font-medium">{t('marketReview.change')}</th><th className="pb-2 font-medium">{t('marketReview.highLow')}</th></tr></thead><tbody>{payload.indices.map((index) => <tr key={index.code} className="border-b border-subtle/70 last:border-0"><td className="py-2.5 text-foreground">{index.name}</td><td className="py-2.5">{number(index.current, 2)}</td><td className={Number(index.changePct) >= 0 ? 'py-2.5 text-success' : 'py-2.5 text-danger'}>{percent(index.changePct)}</td><td className="py-2.5 text-secondary-text">{number(index.high, 2)} / {number(index.low, 2)}</td></tr>)}</tbody></table></div> : null}</section> : null}
    {(payload?.sectors?.top?.length || payload?.sectors?.bottom?.length || payload?.concepts?.top?.length || payload?.concepts?.bottom?.length) ? <section className="rounded-lg border border-subtle bg-card p-5"><h3 className="mb-4 text-base font-semibold text-foreground">{t('marketReview.sectors')}</h3><div className="grid gap-5 md:grid-cols-2"><Rankings title={t('marketReview.sectorTop')} values={payload.sectors?.top} positive /><Rankings title={t('marketReview.sectorBottom')} values={payload.sectors?.bottom} positive={false} /><Rankings title={t('marketReview.conceptTop')} values={payload.concepts?.top} positive /><Rankings title={t('marketReview.conceptBottom')} values={payload.concepts?.bottom} positive={false} /></div></section> : null}
    <section className="rounded-lg border border-subtle bg-card p-5"><h3 className="mb-4 text-base font-semibold text-foreground">{t('marketReview.body')}</h3><div className="prose prose-sm max-w-none text-secondary-text dark:prose-invert">{sections.length ? sections.map((section, index) => <div key={`${section.key || index}-${section.title}`} className="mb-6 last:mb-0"><h4 className="text-base font-semibold text-foreground">{section.title}</h4><Markdown remarkPlugins={[remarkGfm]}>{section.markdown}</Markdown></div>) : <Markdown remarkPlugins={[remarkGfm]}>{markdown || t('marketReview.emptyBody')}</Markdown>}</div></section>
  </div>;
};
