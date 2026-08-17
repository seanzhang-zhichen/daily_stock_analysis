import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Share2, TriangleAlert } from 'lucide-react';
import { historyApi } from '../../api/history';
import type { ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { renderShareImageHtml } from '../../utils/shareImage';
import { Button } from '../common/Button';
import { Tooltip } from '../common/Tooltip';
import { ShareImagePreviewDialog } from './ShareImagePreviewDialog';

type DesktopWindow = Window & {
  dsaDesktop?: {
    renderShareImage?: (recordId: number) => Promise<ArrayBuffer>;
  };
};

type ShareState = 'idle' | 'loading' | 'error';

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

const ShareImageButtonForRecord: React.FC<ShareImageButtonProps> = ({
  recordId,
  reportTitle,
  reportLanguage = 'zh',
  className = '',
}) => {
  const desktopRuntime = typeof window !== 'undefined' ? (window as DesktopWindow).dsaDesktop : undefined;
  const renderDesktopShareImage = desktopRuntime?.renderShareImage;
  const activeRecordId = recordId;
  const text = getReportText(normalizeReportLanguage(reportLanguage));
  const [state, setState] = useState<ShareState>('idle');
  const loadTokenRef = useRef(0);
  const cachedImageRef = useRef<Blob | null>(null);
  const [previewBlob, setPreviewBlob] = useState<Blob | null>(null);

  useEffect(() => {
    return () => {
      loadTokenRef.current += 1;
    };
  }, []);

  const handleShare = useCallback(async () => {
    if (activeRecordId === undefined || state === 'loading') return;

    let blob = cachedImageRef.current;

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
      cachedImageRef.current = blob;
    }

    setState('idle');
    setPreviewBlob(blob);
  }, [activeRecordId, renderDesktopShareImage, state]);

  const closePreview = useCallback(() => {
    setPreviewBlob(null);
  }, []);

  const unavailable = activeRecordId === undefined;
  const tooltipText = unavailable
    ? text.shareImageRequiresHistory
    : state === 'loading'
      ? text.generatingShareImage
      : state === 'error'
        ? text.shareImageFailed
        : text.generateShareImage;
  const buttonText = unavailable ? text.generateShareImage : tooltipText;
  const filename = activeRecordId === undefined
    ? ''
    : `${safeFilenamePart(reportTitle)}-${activeRecordId}.png`;

  return (
    <>
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
            {state === 'error' ? <TriangleAlert className="h-4 w-4 text-danger" aria-hidden="true" /> : null}
            {state === 'idle' ? <Share2 className="h-4 w-4" aria-hidden="true" /> : null}
            <span>{buttonText}</span>
          </Button>
        </span>
      </Tooltip>
      {previewBlob ? (
        <ShareImagePreviewDialog
          blob={previewBlob}
          filename={filename}
          reportTitle={reportTitle}
          reportLanguage={reportLanguage}
          onClose={closePreview}
        />
      ) : null}
    </>
  );
};

export const ShareImageButton: React.FC<ShareImageButtonProps> = (props) => (
  <ShareImageButtonForRecord key={props.recordId ?? 'unsaved'} {...props} />
);
