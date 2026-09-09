import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Database, ExternalLink, RefreshCw, Rss, Search } from 'lucide-react';
import { intelligenceApi, type IntelligenceItem, type IntelligenceSource } from '../api/intelligence';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, Button, Card, EmptyState } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SourceManager } from '../components/intelligence/SourceManager';
import { useUiLanguage } from '../contexts/UiLanguageContext';

const IntelligencePage: React.FC = () => {
  const { t } = useUiLanguage();
  const [sources, setSources] = useState<IntelligenceSource[]>([]);
  const [items, setItems] = useState<IntelligenceItem[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const load = useCallback(async (search = query) => {
    setLoading(true); setError(null);
    try { const [nextSources, nextItems] = await Promise.all([intelligenceApi.listSources(), intelligenceApi.listItems(search.trim())]); setSources(nextSources.items); setItems(nextItems.items); }
    catch (cause) { setError(getParsedApiError(cause)); } finally { setLoading(false); }
  }, [query]);
  useEffect(() => { document.title = `${t('intelligence.title')} - AlphaLens`; void load(''); }, [load, t]);
  const run = async (key: string, operation: () => Promise<void>) => { setAction(key); setError(null); try { await operation(); await load(); } catch (cause) { setError(getParsedApiError(cause)); } finally { setAction(null); } };
  return <StandardPageLayout>
    <header className="ui-page-header flex flex-col gap-4 md:flex-row md:items-end md:justify-between"><div><p className="ui-eyebrow">A-SHARE INTELLIGENCE</p><h1 className="mt-1 text-2xl font-bold text-foreground">{t('intelligence.title')}</h1><p className="mt-1 max-w-2xl text-sm text-secondary-text">{t('intelligence.description')}</p></div><div className="flex flex-wrap gap-2"><Button variant="outline" isLoading={action === 'defaults'} loadingText={t('intelligence.initializing')} onClick={() => void run('defaults', intelligenceApi.createDefaults)}><Database className="h-4 w-4" />{t('intelligence.initialize')}</Button><Button isLoading={action === 'all'} loadingText={t('intelligence.fetching')} onClick={() => void run('all', intelligenceApi.fetchEnabled)}><RefreshCw className="h-4 w-4" />{t('intelligence.refreshEnabled')}</Button></div></header>
    {error ? <ApiErrorAlert error={error} onDismiss={() => setError(null)} /> : null}
    {loading && !sources.length ? <p className="text-sm text-secondary-text">{t('intelligence.loadingSources')}</p> : null}{!loading && !sources.length ? <EmptyState icon={<Rss className="h-6 w-6" />} title={t('intelligence.emptySourcesTitle')} description={t('intelligence.emptySourcesDescription')} /> : null}{sources.length ? <SourceManager sources={sources} activeAction={action} onRun={(key, operation) => void run(key, operation)} /> : null}
    <Card title={t('intelligence.items')} subtitle={`ITEMS (${items.length})`}><form className="mb-4 flex gap-2" onSubmit={(event) => { event.preventDefault(); void load(); }}><label className="sr-only" htmlFor="intelligence-query">{t('intelligence.searchLabel')}</label><input id="intelligence-query" className="ui-input h-10 flex-1 px-3 text-sm" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('intelligence.searchPlaceholder')} /><Button type="submit" variant="outline"><Search className="h-4 w-4" />{t('intelligence.search')}</Button></form>{!loading && !items.length ? <EmptyState title={t('intelligence.emptyItemsTitle')} description={t('intelligence.emptyItemsDescription')} /> : null}<div className="divide-y divide-border/60">{items.map((item) => <article key={item.id} className="py-3"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><a className="font-medium text-foreground hover:text-primary" href={item.url} target="_blank" rel="noreferrer">{item.title}</a><p className="mt-1 line-clamp-2 text-sm text-secondary-text">{item.summary}</p><p className="mt-2 text-xs text-muted-text">{item.source_name || item.source_type} · {item.published_at || item.fetched_at || t('intelligence.unknownTime')}</p></div><ExternalLink className="mt-1 h-4 w-4 shrink-0 text-muted-text" /></div></article>)}</div></Card>
  </StandardPageLayout>;
};
export default IntelligencePage;
