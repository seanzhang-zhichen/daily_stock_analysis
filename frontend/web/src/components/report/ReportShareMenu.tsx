import type React from 'react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import {
  ChevronDown,
  FileDown,
  Image as ImageIcon,
  Loader2,
  Share2,
  TriangleAlert,
} from 'lucide-react';
import { historyApi } from '../../api/history';
import type { ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { renderShareImageHtml } from '../../utils/shareImage';
import { Button } from '../common/Button';
import { ShareImagePreviewDialog } from './ShareImagePreviewDialog';

type DesktopWindow = Window & {
  dsaDesktop?: {
    renderShareImage?: (recordId: number) => Promise<ArrayBuffer>;
  };
};

type ShareImageState = 'idle' | 'loading' | 'error';
type PdfExportState = 'idle' | 'exporting' | 'error';

interface ReportShareMenuProps {
  recordId?: number;
  reportTitle: string;
  reportLanguage?: ReportLanguage;
  onExportPdf: () => Promise<void>;
  className?: string;
}

const safeFilenamePart = (value: string): string => {
  const normalized = value.trim().replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, '-');
  return normalized.slice(0, 72) || 'report';
};

const ReportShareMenuForReport: React.FC<ReportShareMenuProps> = ({
  recordId,
  reportTitle,
  reportLanguage = 'zh',
  onExportPdf,
  className = '',
}) => {
  const desktopRuntime = typeof window !== 'undefined' ? (window as DesktopWindow).dsaDesktop : undefined;
  const renderDesktopShareImage = desktopRuntime?.renderShareImage;
  const language = normalizeReportLanguage(reportLanguage);
  const text = getReportText(language);
  const menuId = useId();
  const [menuOpen, setMenuOpen] = useState(false);
  const [shareImageState, setShareImageState] = useState<ShareImageState>('idle');
  const [pdfExportState, setPdfExportState] = useState<PdfExportState>('idle');
  const [previewBlob, setPreviewBlob] = useState<Blob | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const menuItemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const initialFocusRef = useRef<'first' | 'last' | null>(null);
  const loadTokenRef = useRef(0);
  const cachedImageRef = useRef<Blob | null>(null);

  const shareImageUnavailable = recordId === undefined;
  const busy = shareImageState === 'loading' || pdfExportState === 'exporting';

  useEffect(() => {
    return () => {
      loadTokenRef.current += 1;
    };
  }, []);

  useEffect(() => {
    if (!menuOpen) return undefined;

    const handlePointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen || initialFocusRef.current === null) return undefined;

    const frame = window.requestAnimationFrame(() => {
      const enabledItems = menuItemRefs.current.filter(
        (item): item is HTMLButtonElement => Boolean(item),
      );
      const target = initialFocusRef.current === 'last'
        ? enabledItems.at(-1)
        : enabledItems[0];
      initialFocusRef.current = null;
      target?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [menuOpen]);

  const closeMenu = useCallback((restoreFocus = false) => {
    initialFocusRef.current = null;
    setMenuOpen(false);
    if (restoreFocus) {
      containerRef.current
        ?.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]')
        ?.focus();
    }
  }, []);

  const handleShareImage = useCallback(async () => {
    if (recordId === undefined || shareImageState === 'loading') return;

    closeMenu(true);
    let blob = cachedImageRef.current;

    if (!blob) {
      const loadToken = loadTokenRef.current + 1;
      loadTokenRef.current = loadToken;
      setShareImageState('loading');
      try {
        if (renderDesktopShareImage) {
          const pngBytes = await renderDesktopShareImage(recordId);
          blob = new Blob([pngBytes], { type: 'image/png' });
        } else {
          try {
            const html = await historyApi.getShareImageHtml(recordId);
            blob = await renderShareImageHtml(html);
          } catch (browserRenderError) {
            console.warn(
              'Browser share image rendering failed; falling back to the server renderer:',
              browserRenderError,
            );
            blob = await historyApi.getShareImage(recordId);
          }
        }
      } catch (error) {
        if (loadTokenRef.current !== loadToken) return;
        console.error('Generate share image failed:', error);
        setShareImageState('error');
        return;
      }
      if (loadTokenRef.current !== loadToken) return;
      cachedImageRef.current = blob;
    }

    setShareImageState('idle');
    setPreviewBlob(blob);
  }, [closeMenu, recordId, renderDesktopShareImage, shareImageState]);

  const handleExportPdf = useCallback(async () => {
    if (pdfExportState === 'exporting') return;

    closeMenu(true);
    setPdfExportState('exporting');
    try {
      await onExportPdf();
      setPdfExportState('idle');
    } catch (error) {
      console.error('Report PDF export failed:', error);
      setPdfExportState('error');
    }
  }, [closeMenu, onExportPdf, pdfExportState]);

  const closePreview = useCallback(() => {
    setPreviewBlob(null);
  }, []);

  const handleTriggerKeyDown = useCallback((event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;

    event.preventDefault();
    initialFocusRef.current = event.key === 'ArrowUp' ? 'last' : 'first';
    setMenuOpen(true);
  }, []);

  const handleMenuKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const enabledItems = menuItemRefs.current.filter(
      (item): item is HTMLButtonElement => Boolean(item),
    );
    if (enabledItems.length === 0) return;

    const currentIndex = enabledItems.findIndex((item) => item === document.activeElement);
    switch (event.key) {
      case 'Escape':
        event.preventDefault();
        closeMenu(true);
        break;
      case 'ArrowDown':
        event.preventDefault();
        enabledItems[(currentIndex + 1 + enabledItems.length) % enabledItems.length]?.focus();
        break;
      case 'ArrowUp':
        event.preventDefault();
        enabledItems[
          currentIndex < 0
            ? enabledItems.length - 1
            : (currentIndex - 1 + enabledItems.length) % enabledItems.length
        ]?.focus();
        break;
      case 'Home':
        event.preventDefault();
        enabledItems[0]?.focus();
        break;
      case 'End':
        event.preventDefault();
        enabledItems.at(-1)?.focus();
        break;
      case 'Tab':
        closeMenu();
        break;
      default:
        break;
    }
  }, [closeMenu]);

  const shareImageLabel = shareImageState === 'loading'
    ? text.generatingShareImage
    : shareImageState === 'error'
      ? text.shareImageFailed
      : text.generateShareImage;
  const pdfExportLabel = pdfExportState === 'exporting'
    ? text.exportingPdf
    : pdfExportState === 'error'
      ? text.exportPdfFailed
      : text.exportPdf;
  const filename = recordId === undefined
    ? ''
    : `${safeFilenamePart(reportTitle)}-${recordId}.png`;
  const asyncStatus = [
    shareImageState === 'loading' ? text.generatingShareImage : null,
    shareImageState === 'error' ? text.shareImageFailed : null,
    pdfExportState === 'exporting' ? text.exportingPdf : null,
    pdfExportState === 'error' ? text.exportPdfFailed : null,
  ].filter(Boolean).join(' ');

  const toggleMenu = () => {
    if (menuOpen) {
      closeMenu();
      return;
    }
    initialFocusRef.current = 'first';
    setMenuOpen(true);
  };

  return (
    <>
      <div
        ref={containerRef}
        data-pdf-hide
        className={`relative inline-flex shrink-0 ${className}`}
      >
        <Button
          id={`${menuId}-trigger`}
          variant="outline"
          size="sm"
          onClick={toggleMenu}
          onKeyDown={handleTriggerKeyDown}
          className="shrink-0 whitespace-nowrap"
          aria-busy={busy || undefined}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-controls={menuOpen ? menuId : undefined}
          aria-label={text.shareReport}
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : shareImageState === 'error' || pdfExportState === 'error' ? (
            <TriangleAlert className="h-4 w-4 text-danger" aria-hidden="true" />
          ) : (
            <Share2 className="h-4 w-4" aria-hidden="true" />
          )}
          <span>{text.shareReport}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-text" aria-hidden="true" />
        </Button>

        {menuOpen ? (
          <div
            id={menuId}
            role="menu"
            aria-labelledby={`${menuId}-trigger`}
            onKeyDown={handleMenuKeyDown}
            className="ui-menu absolute right-0 top-full z-[120] mt-2 w-[min(14rem,calc(100vw-1.5rem))] text-left"
          >
            <button
              ref={(node) => {
                menuItemRefs.current[0] = node;
              }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              aria-disabled={shareImageUnavailable || shareImageState === 'loading' || undefined}
              aria-busy={shareImageState === 'loading' || undefined}
              onClick={() => void handleShareImage()}
              className="ui-menu-item gap-3 text-left"
            >
              <span className="flex min-w-0 items-center gap-2.5">
                {shareImageState === 'loading' ? (
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
                ) : shareImageState === 'error' ? (
                  <TriangleAlert className="h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
                ) : (
                  <ImageIcon className="h-4 w-4 shrink-0" aria-hidden="true" />
                )}
                <span className="min-w-0">
                  <span className="block font-medium text-foreground">{shareImageLabel}</span>
                  {shareImageUnavailable ? (
                    <span className="mt-0.5 block text-xs text-muted-text">
                      {text.shareImageRequiresHistory}
                    </span>
                  ) : null}
                </span>
              </span>
            </button>
            <button
              ref={(node) => {
                menuItemRefs.current[1] = node;
              }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              aria-disabled={pdfExportState === 'exporting' || undefined}
              aria-busy={pdfExportState === 'exporting' || undefined}
              onClick={() => void handleExportPdf()}
              className="ui-menu-item gap-3 text-left"
            >
              <span className="flex min-w-0 items-center gap-2.5">
                {pdfExportState === 'exporting' ? (
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
                ) : pdfExportState === 'error' ? (
                  <TriangleAlert className="h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
                ) : (
                  <FileDown className="h-4 w-4 shrink-0" aria-hidden="true" />
                )}
                <span className="font-medium text-foreground">{pdfExportLabel}</span>
              </span>
            </button>
          </div>
        ) : null}
        <span role="status" aria-live="polite" className="sr-only">
          {asyncStatus}
        </span>
      </div>

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

export const ReportShareMenu: React.FC<ReportShareMenuProps> = (props) => (
  <ReportShareMenuForReport
    key={`${props.recordId ?? 'unsaved'}:${props.reportTitle}`}
    {...props}
  />
);
