import { useState } from 'react';
import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { intelligenceApi, type IntelligenceSource } from '../../api/intelligence';
import { Button, Card } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

type Draft = {
  name: string;
  url: string;
  sourceType: 'rss' | 'atom' | 'newsnow';
  scopeType: 'market' | 'symbol' | 'sector';
};

const EMPTY_DRAFT: Draft = { name: '', url: '', sourceType: 'rss', scopeType: 'market' };

type Props = {
  sources: IntelligenceSource[];
  activeAction: string | null;
  onRun: (key: string, action: () => Promise<void>) => void;
};

export function SourceManager({ sources, activeAction, onRun }: Props) {
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const { t } = useUiLanguage();

  return (
    <Card title={t('intelligence.sources')} subtitle={`SOURCES (${sources.length})`}>
      <form
        className="mb-4 grid gap-2 md:grid-cols-6"
        onSubmit={(event) => {
          event.preventDefault();
          onRun('create', async () => {
            await intelligenceApi.createSource({
              name: draft.name.trim(),
              url: draft.url.trim(),
              source_type: draft.sourceType,
              scope_type: draft.scopeType,
            });
            setDraft(EMPTY_DRAFT);
          });
        }}
      >
        <input required className="ui-input h-10 px-3 text-sm" value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} placeholder={t('intelligence.sourceName')} />
        <input required type="url" className="ui-input h-10 px-3 text-sm md:col-span-2" value={draft.url} onChange={(event) => setDraft((current) => ({ ...current, url: event.target.value }))} placeholder="https://..." />
        <select className="ui-input h-10 px-3 text-sm" value={draft.sourceType} onChange={(event) => setDraft((current) => ({ ...current, sourceType: event.target.value as Draft['sourceType'] }))}><option value="rss">RSS</option><option value="atom">Atom</option><option value="newsnow">NewsNow</option></select>
        <select className="ui-input h-10 px-3 text-sm" value={draft.scopeType} onChange={(event) => setDraft((current) => ({ ...current, scopeType: event.target.value as Draft['scopeType'] }))}><option value="market">{t('intelligence.market')}</option><option value="symbol">{t('intelligence.symbol')}</option><option value="sector">{t('intelligence.sector')}</option></select>
        <Button type="submit" isLoading={activeAction === 'create'} loadingText={t('intelligence.adding')}><Plus className="h-4 w-4" />{t('intelligence.add')}</Button>
      </form>
      <div className="divide-y divide-border/60">
        {sources.map((source) => (
          <div key={source.id} className="flex flex-col gap-3 py-3 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0"><p className="font-medium text-foreground">{source.name} <span className="ml-2 text-xs text-muted-text">{source.source_type.toUpperCase()} · {source.scope_type}</span></p><p className="mt-1 truncate text-xs text-secondary-text">{source.description || source.url}</p>{source.last_error ? <p className="mt-1 text-xs text-danger">{source.last_error}</p> : null}</div>
            <div className="flex gap-2"><Button size="sm" variant="outline" disabled={!source.enabled} isLoading={activeAction === `source-${source.id}`} loadingText={t('intelligence.refreshing')} onClick={() => onRun(`source-${source.id}`, () => intelligenceApi.fetchSource(source.id))}><RefreshCw className="h-3.5 w-3.5" />{t('intelligence.refresh')}</Button><Button size="sm" variant="outline" isLoading={activeAction === `toggle-${source.id}`} loadingText="..." onClick={() => onRun(`toggle-${source.id}`, () => intelligenceApi.setSourceEnabled(source.id, !source.enabled))}>{source.enabled ? t('intelligence.disable') : t('intelligence.enable')}</Button><Button size="sm" variant="danger-subtle" aria-label={t('intelligence.deleteSource', { name: source.name })} isLoading={activeAction === `delete-${source.id}`} loadingText="..." onClick={() => onRun(`delete-${source.id}`, () => intelligenceApi.deleteSource(source.id))}><Trash2 className="h-3.5 w-3.5" /></Button></div>
          </div>
        ))}
      </div>
    </Card>
  );
}
