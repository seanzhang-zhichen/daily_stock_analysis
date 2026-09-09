import { Download, RefreshCw } from 'lucide-react';
import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { Button, StatusDot } from '../common';

type RawDesktopUpdateState = Record<string, unknown>;
type DesktopUpdateState = {
  status: string;
  updateMode: string;
  currentVersion: string;
  latestVersion: string;
  releaseUrl: string;
  tagName: string;
  message: string;
  downloadPercent: number | null;
};
type DesktopApi = {
  version?: unknown;
  getUpdateState?: () => Promise<RawDesktopUpdateState>;
  checkForUpdates?: () => Promise<RawDesktopUpdateState>;
  installDownloadedUpdate?: () => Promise<boolean>;
  openReleasePage?: (url?: string) => Promise<boolean>;
  onUpdateStateChange?: (listener: (state: RawDesktopUpdateState) => void) => (() => void) | void;
};

function runtime(): DesktopApi | undefined {
  return (window as Window & { dsaDesktop?: DesktopApi }).dsaDesktop;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function normalize(raw: RawDesktopUpdateState | null | undefined): DesktopUpdateState | null {
  if (!raw || typeof raw !== 'object') return null;
  const percent = Number(raw.downloadPercent);
  return {
    status: text(raw.status) || 'idle', updateMode: text(raw.updateMode) || 'manual',
    currentVersion: text(raw.currentVersion), latestVersion: text(raw.latestVersion), releaseUrl: text(raw.releaseUrl),
    tagName: text(raw.tagName), message: text(raw.message),
    downloadPercent: Number.isFinite(percent) ? percent : null,
  };
}

function isBusy(status: string) {
  return status === 'checking' || status === 'downloading' || status === 'installing';
}

function badgeTone(status: string): 'success' | 'warning' | 'danger' | 'info' | null {
  if (status === 'update-downloaded') return 'success';
  if (status === 'update-available') return 'warning';
  if (status === 'error') return 'danger';
  return isBusy(status) ? 'info' : null;
}

export const DesktopUpdateIndicator: React.FC = () => {
  const { t } = useUiLanguage();
  const navigate = useNavigate();
  const api = runtime();
  const enabled = Boolean(api?.getUpdateState && api?.checkForUpdates && api?.openReleasePage);
  const [state, setState] = useState<DesktopUpdateState | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const status = state?.status || 'idle';
  const busy = isBusy(status);

  useEffect(() => {
    if (!enabled || !api) return undefined;
    let active = true;
    void api.getUpdateState?.().then((next) => {
      if (active) setState(normalize(next));
    }).catch((error: unknown) => {
      if (active) setState({ status: 'error', updateMode: 'manual', currentVersion: '', latestVersion: '', releaseUrl: '', tagName: '', message: error instanceof Error ? error.message : t('desktopUpdate.errorMessage'), downloadPercent: null });
    });
    const unsubscribe = api.onUpdateStateChange?.((next) => { if (active) setState(normalize(next)); });
    return () => { active = false; if (typeof unsubscribe === 'function') unsubscribe(); };
  }, [api, enabled, t]);

  useEffect(() => {
    if (!open) return undefined;
    const closeWhenOutside = (event: MouseEvent) => { if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false); };
    const closeWhenEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', closeWhenOutside);
    document.addEventListener('keydown', closeWhenEscape);
    return () => { document.removeEventListener('mousedown', closeWhenOutside); document.removeEventListener('keydown', closeWhenEscape); };
  }, [open]);

  if (!enabled || !api) return null;

  const currentVersion = state?.currentVersion || text(api.version);
  const latestVersion = state?.latestVersion || state?.tagName;
  const notice = state?.message || (status === 'update-available'
    ? t('desktopUpdate.availableMessage', { current: currentVersion || t('desktopUpdate.latest'), latest: latestVersion || t('desktopUpdate.latest'), message: t('desktopUpdate.releaseMessage') })
    : status === 'update-downloaded' ? t('desktopUpdate.downloadedMessage')
      : status === 'downloading' ? t('desktopUpdate.downloadingMessage', { percent: state?.downloadPercent == null ? '' : ` (${state.downloadPercent}%)` })
        : status === 'installing' ? t('desktopUpdate.installingMessage')
          : status === 'up-to-date' ? t('desktopUpdate.latestMessage')
            : status === 'checking' ? t('desktopUpdate.checkingMessage')
              : status === 'error' ? t('desktopUpdate.errorMessage') : t('desktopUpdate.idle', { version: currentVersion || t('desktopUpdate.latest') }));
  const title = status === 'update-available' ? t('desktopUpdate.available') : status === 'update-downloaded' ? t('desktopUpdate.downloaded') : status === 'downloading' ? t('desktopUpdate.downloading') : status === 'installing' ? t('desktopUpdate.installing') : status === 'up-to-date' ? t('desktopUpdate.latest') : status === 'error' ? t('desktopUpdate.error') : t('desktopUpdate.entry');
  const tone = badgeTone(status);

  const check = async () => {
    if (busy) return;
    setState((current) => ({ ...(current || normalize({})!), status: 'checking', message: t('desktopUpdate.checkingMessage') }));
    try { setState(normalize(await api.checkForUpdates?.())); }
    catch (error: unknown) { setState((current) => ({ ...(current || normalize({})!), status: 'error', message: error instanceof Error ? error.message : t('desktopUpdate.errorMessage') })); }
  };

  return <div className="relative" ref={rootRef}>
    <button type="button" className={cn('relative inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border/70 bg-card/85 text-secondary-text shadow-soft-card backdrop-blur-md transition-colors hover:bg-hover hover:text-foreground', open ? 'border-border text-foreground' : '')} aria-label={t('desktopUpdate.entry')} aria-expanded={open} aria-haspopup="dialog" title={notice} onClick={() => setOpen((value) => !value)}>
      {busy ? <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Download className="h-4 w-4" aria-hidden="true" />}
      {tone ? <StatusDot tone={tone} pulse={status === 'update-downloaded' || status === 'update-available'} className="absolute right-1.5 top-1.5 h-2 w-2" data-testid="desktop-update-badge" /> : null}
    </button>
    {open ? <div role="dialog" aria-label={t('desktopUpdate.entry')} className="absolute right-0 z-50 mt-2 w-[min(20rem,calc(100vw-1.5rem))] rounded-xl border border-border/70 bg-card/95 p-3 shadow-soft-card backdrop-blur-xl">
      <p className="text-sm font-semibold text-foreground">{title}</p><p className="mt-1 text-xs leading-5 text-secondary-text">{notice}</p>
      {currentVersion || latestVersion ? <p className="mt-1 text-[11px] text-muted-text">{currentVersion && latestVersion && currentVersion !== latestVersion ? t('desktopUpdate.versionRange', { current: currentVersion, latest: latestVersion }) : t('desktopUpdate.currentVersion', { version: currentVersion || latestVersion || '' })}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {!busy ? <Button variant="settings-secondary" size="sm" onClick={() => void check()} isLoading={status === 'checking'} loadingText={t('desktopUpdate.checking')}>{status === 'error' ? t('desktopUpdate.recheck') : t('desktopUpdate.check')}</Button> : null}
        {state?.releaseUrl && (status === 'update-available' || status === 'error') ? <Button variant="settings-primary" size="sm" onClick={() => void api.openReleasePage?.(state.releaseUrl)}>{t('desktopUpdate.download')}</Button> : null}
        {status === 'update-downloaded' ? <Button variant="settings-primary" size="sm" onClick={() => void api.installDownloadedUpdate?.()}>{t('desktopUpdate.install')}</Button> : null}
      </div>
      <button type="button" className="mt-3 text-xs text-secondary-text underline-offset-2 hover:text-foreground hover:underline" onClick={() => { setOpen(false); navigate('/settings?category=system#desktop-version-info'); }}>{t('desktopUpdate.openSettings')}</button>
    </div> : null}
  </div>;
};
