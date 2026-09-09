import { useEffect, useState } from 'react';
import { LineChart, Trash2 } from 'lucide-react';
import { historyApi } from '../../api/history';
import type { HistoryTrendResponse } from '../../types/analysis';
import { Drawer, Button, ConfirmDialog } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface Props {
  open: boolean;
  stockCode: string;
  stockName?: string;
  onClose: () => void;
  onDeleted: () => void;
}

export function HistoryTrendDrawer({ open, stockCode, stockName, onClose, onDeleted }: Props) {
  const { t } = useUiLanguage();
  const [data, setData] = useState<HistoryTrendResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!open || !stockCode) return;
    let active = true;
    setLoading(true); setError(null);
    historyApi.getTrendByCode(stockCode).then((result) => {
      if (active) setData(result);
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : t('trend.loadFailed'));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, stockCode, t]);

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await historyApi.deleteByCode(stockCode);
      setConfirming(false); onClose(); onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('trend.loadFailed'));
    } finally { setDeleting(false); }
  };

  const items = data?.items ?? [];
  return <>
    <Drawer isOpen={open} onClose={onClose} title={t('trend.title', { name: stockName || stockCode })} side="right">
      <div className="space-y-5">
        <div className="flex items-center justify-between gap-3 rounded-lg border border-subtle bg-surface-muted/40 p-3">
          <div className="flex items-center gap-2 text-sm text-secondary-text"><LineChart className="h-4 w-4 text-primary" />{t('trend.total', { count: items.length })}</div>
          <Button type="button" variant="danger-subtle" size="xsm" onClick={() => setConfirming(true)} disabled={!items.length || deleting}>
            <Trash2 className="h-3.5 w-3.5" />{t('trend.clear')}
          </Button>
        </div>
        {loading ? <p className="text-sm text-secondary-text">{t('trend.loading')}</p> : null}
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
        {!loading && !error && !items.length ? <p className="text-sm text-secondary-text">{t('trend.empty')}</p> : null}
        {items.length > 0 ? <div className="relative space-y-4 border-l border-subtle pl-5">
          {items.map((item) => <article key={item.id} className="relative rounded-lg border border-subtle bg-surface p-3">
            <span className="absolute -left-[1.65rem] top-4 h-2.5 w-2.5 rounded-full bg-primary ring-4 ring-surface" />
            <div className="flex items-start justify-between gap-3"><time className="text-xs text-muted-text">{item.createdAt ? new Date(item.createdAt).toLocaleString() : '-'}</time><span className="shrink-0 text-sm font-semibold text-primary">{item.sentimentScore ?? '-'} / 100</span></div>
            <p className="mt-2 text-sm font-medium text-foreground">{item.trendPrediction || t('trend.noPrediction')}</p>
            <p className="mt-1 text-xs text-secondary-text">{item.operationAdvice || t('trend.noAdvice')}</p>
            {item.analysisSummary ? <p className="mt-2 line-clamp-3 text-sm leading-6 text-secondary-text">{item.analysisSummary}</p> : null}
          </article>)}
        </div> : null}
      </div>
    </Drawer>
    <ConfirmDialog isOpen={confirming} onCancel={() => setConfirming(false)} onConfirm={() => void handleDelete()} title={t('trend.clearTitle')} message={t('trend.clearMessage', { name: stockName || stockCode })} confirmText={deleting ? t('trend.clearing') : t('trend.clearConfirm')} isDanger />
  </>;
}
