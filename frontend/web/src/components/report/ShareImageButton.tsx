import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Loader2, Share2, TriangleAlert } from 'lucide-react';
import { historyApi } from '../../api/history';
import type { ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { renderShareImageHtml } from '../../utils/shareImage';
import { Button } from '../common/Button';
import { Tooltip } from '../common/Tooltip';

type DesktopWindow = Window & {
  dsaDesktop?: {
    renderShareImage?: (recordId: number) => Promise<ArrayBuffer>;
  };
};

type ShareState = 'idle' | 'loading' | 'ready' | 'success' | 'error';

interface ShareImageButtonProps {
  recordId?: number;
  reportTitle: string;
  reportLanguage?: ReportLanguage;
  className?: string;
}

const safeFilenamePart = (value: string): string => {
  const normalized = value.trim().replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, '-');
  return normalized.slice(0, 72) || 'report';
};

const downloadBlob = (blob: Blob, filename: string): void => {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
};

export const ShareImageButton: React.FC<ShareImageButtonProps> = ({
  recordId,
  reportTitle,
  reportLanguage = 'zh',
  className = '',
}) => {
  const desktopRuntime = typeof window !== 'undefined' ? (window as DesktopWindow).dsaDesktop : undefined;
  const renderDesktopShareImage = desktopRuntime?.renderShareImage;
  const activeRecordId = recordId;
  const text = getReportText(normalizeReportLanguage(reportLanguage));
  const [stateSnapshot, setStateSnapshot] = useState<{
    recordId?: number;
    state: ShareState;
  }>(() => ({
    recordId: activeRecordId,
    state: 'idle',
  }));
  const resetTimerRef = useRef<number | null>(null);
  const loadTokenRef = useRef(0);
  const cachedImageRef = useRef<{ recordId: number; blob: Blob } | null>(null);
  const state = stateSnapshot.recordId === activeRecordId ? stateSnapshot.state : 'idle';
  const setState = useCallback((nextState: ShareState) => {
    setStateSnapshot({ recordId: activeRecordId, state: nextState });
  }, [activeRecordId]);
  const clearResetTimer = useCallback(() => {
    if (resetTimerRef.current !== null) {
      window.clearTimeout(resetTimerRef.current);
      resetTimerRef.current = null;
    }
  }, []);

  const scheduleReset = useCallback(() => {
    clearResetTimer();
    const scheduledRecordId = activeRecordId;
    resetTimerRef.current = window.setTimeout(() => {
      setStateSnapshot((current) => (
        current.recordId === scheduledRecordId
          ? { recordId: scheduledRecordId, state: 'idle' }
          : current
      ));
    }, 2200);
  }, [activeRecordId, clearResetTimer]);

  useEffect(() => {
    clearResetTimer();
    loadTokenRef.current += 1;
    cachedImageRef.current = null;

    return () => {
      clearResetTimer();
      loadTokenRef.current += 1;
    };
  }, [activeRecordId, clearResetTimer]);

  const handleShare = useCallback(async () => {
    if (activeRecordId === undefined || state === 'loading') return;
    clearResetTimer();

    let blob = cachedImageRef.current?.recordId === activeRecordId
      ? cachedImageRef.current.blob
      : null;
    let generatedNow = false;

    if (!blob) {
      const loadToken = loadTokenRef.current + 1;
      loadTokenRef.current = loadToken;
      setState('loading');
      try {
        if (renderDesktopShareImage) {
          const pngBytes = await renderDesktopShareImage(activeRecordId);
          blob = new Blob([pngBytes], { type: 'image/png' });
        } else {
          try {
            const html = await historyApi.getShareImageHtml(activeRecordId);
            blob = await renderShareImageHtml(html);
          } catch (browserRenderError) {
            console.warn(
              'Browser share image rendering failed; falling back to the server renderer:',
              browserRenderError,
            );
            blob = await historyApi.getShareImage(activeRecordId);
          }
        }
      } catch (error) {
        if (loadTokenRef.current !== loadToken) return;
        console.error('Generate share image failed:', error);
        setState('error');
        return;
      }
      if (loadTokenRef.current !== loadToken) return;
      cachedImageRef.current = { recordId: activeRecordId, blob };
      generatedNow = true;
    }

    const filename = `${safeFilenamePart(reportTitle)}-${activeRecordId}.png`;
    const file = new File([blob], filename, { type: 'image/png' });
    const canShareFile = typeof navigator.share === 'function'
      && typeof navigator.canShare === 'function'
      && navigator.canShare({ files: [file] });

    // A file cannot be shared before it exists, while navigator.share() must run
    // inside a transient user-activation event. Prepare on the first click and
    // let the next click invoke native sharing synchronously.
    if (generatedNow && canShareFile) {
      setState('ready');
      return;
    }

    setState('loading');
    try {
      if (canShareFile) {
        try {
          await navigator.share({
            files: [file],
            title: reportTitle,
          });
        } catch (error) {
          if (error instanceof DOMException && error.name === 'AbortError') {
            setState('ready');
            return;
          }
          console.warn('Native file sharing failed; falling back to download:', error);
          downloadBlob(blob, filename);
        }
      } else {
        downloadBlob(blob, filename);
      }

      setState('success');
      scheduleReset();
    } catch (error) {
      console.error('Generate share image failed:', error);
      setState('error');
    }
  }, [activeRecordId, clearResetTimer, renderDesktopShareImage, reportTitle, scheduleReset, setState, state]);

  const unavailable = activeRecordId === undefined;
  const tooltipText = unavailable
    ? text.shareImageRequiresHistory
    : state === 'loading'
      ? text.generatingShareImage
      : state === 'ready'
        ? text.shareImageReadyToShare
      : state === 'success'
        ? text.shareImageReady
        : state === 'error'
          ? text.shareImageFailed
          : text.generateShareImage;
  const buttonText = unavailable ? text.generateShareImage : tooltipText;

  return (
    <Tooltip content={tooltipText}>
      <span className="inline-flex shrink-0">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void handleShare()}
          disabled={unavailable || state === 'loading'}
          className={`shrink-0 whitespace-nowrap ${className}`}
          aria-label={tooltipText}
        >
          {state === 'loading' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
          {state === 'success' ? <Check className="h-4 w-4 text-success" aria-hidden="true" /> : null}
          {state === 'error' ? <TriangleAlert className="h-4 w-4 text-danger" aria-hidden="true" /> : null}
          {state === 'idle' || state === 'ready' ? <Share2 className="h-4 w-4" aria-hidden="true" /> : null}
          <span>{buttonText}</span>
        </Button>
      </span>
    </Tooltip>
  );
};
