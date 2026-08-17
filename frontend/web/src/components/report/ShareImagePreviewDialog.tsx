import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ClipboardCopy, Download, Loader2, Share2, TriangleAlert, X } from 'lucide-react';
import type { ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { Button } from '../common/Button';

interface ShareImagePreviewDialogProps {
  blob: Blob;
  filename: string;
  reportTitle: string;
  reportLanguage: ReportLanguage;
  onClose: () => void;
}

interface WritableFileTarget {
  write: (data: Blob) => Promise<void>;
  close: () => Promise<void>;
}

interface SaveFileHandle {
  createWritable: () => Promise<WritableFileTarget>;
}

type WindowWithFilePicker = Window & {
  showSaveFilePicker?: (options: {
    suggestedName: string;
    types: Array<{
      description: string;
      accept: Record<string, string[]>;
    }>;
  }) => Promise<SaveFileHandle>;
};

type CopyState = 'idle' | 'copying' | 'copied' | 'error';
type SaveState = 'idle' | 'saving' | 'saved' | 'error';

const isAbortError = (error: unknown): boolean => (
  error instanceof DOMException && error.name === 'AbortError'
);

const triggerBlobDownload = (imageUrl: string, filename: string): void => {
  const anchor = document.createElement('a');
  anchor.href = imageUrl;
  anchor.download = filename;
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    window.setTimeout(() => anchor.remove(), 0);
  }
};

const supportsFileShare = (file: File | null): boolean => {
  if (!file || typeof navigator.share !== 'function' || typeof navigator.canShare !== 'function') {
    return false;
  }
  try {
    return navigator.canShare({ files: [file] });
  } catch {
    return false;
  }
};

export const ShareImagePreviewDialog: React.FC<ShareImagePreviewDialogProps> = ({
  blob,
  filename,
  reportTitle,
  reportLanguage,
  onClose,
}) => {
  const text = getReportText(normalizeReportLanguage(reportLanguage));
  const [isSharing, setIsSharing] = useState(false);
  const [copyState, setCopyState] = useState<CopyState>('idle');
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const imageUrl = useMemo(() => URL.createObjectURL(blob), [blob]);
  const shareFile = useMemo(
    () => (typeof File === 'undefined' ? null : new File([blob], filename, { type: 'image/png' })),
    [blob, filename],
  );
  const canShare = supportsFileShare(shareFile);
  const canCopyImage = typeof ClipboardItem !== 'undefined'
    && typeof navigator.clipboard?.write === 'function';

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      URL.revokeObjectURL(imageUrl);
    };
  }, [imageUrl, onClose]);

  const handleSave = useCallback(async () => {
    if (saveState === 'saving') return;
    setSaveState('saving');
    const showSaveFilePicker = (window as WindowWithFilePicker).showSaveFilePicker;
    if (showSaveFilePicker) {
      try {
        const fileHandle = await showSaveFilePicker({
          suggestedName: filename,
          types: [{
            description: 'PNG image',
            accept: { 'image/png': ['.png'] },
          }],
        });
        const writable = await fileHandle.createWritable();
        await writable.write(blob);
        await writable.close();
        setSaveState('saved');
        return;
      } catch (error) {
        if (isAbortError(error)) {
          setSaveState('idle');
          return;
        }
        console.warn('Native file save failed; falling back to browser download:', error);
      }
    }

    try {
      triggerBlobDownload(imageUrl, filename);
      setSaveState('idle');
    } catch (error) {
      console.error('Share image download failed:', error);
      setSaveState('error');
    }
  }, [blob, filename, imageUrl, saveState]);

  const handleCopy = useCallback(async () => {
    if (!canCopyImage || copyState === 'copying') return;
    setCopyState('copying');
    try {
      await navigator.clipboard.write([
        new ClipboardItem({ 'image/png': blob }),
      ]);
      setCopyState('copied');
    } catch (error) {
      console.error('Copy share image to clipboard failed:', error);
      setCopyState('error');
    }
  }, [blob, canCopyImage, copyState]);

  const handleSystemShare = useCallback(async () => {
    if (!shareFile || !canShare || isSharing) return;
    setIsSharing(true);
    try {
      await navigator.share({
        files: [shareFile],
        title: reportTitle,
      });
    } catch (error) {
      if (!isAbortError(error)) {
        console.error('Native file sharing failed:', error);
      }
    } finally {
      setIsSharing(false);
    }
  }, [canShare, isSharing, reportTitle, shareFile]);

  return createPortal(
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 p-3 backdrop-blur-sm sm:p-6"
      role="presentation"
      onClick={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-image-preview-title"
        className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border/70 bg-elevated shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between border-b border-border/60 px-4 py-3 sm:px-5">
          <h2 id="share-image-preview-title" className="truncate text-base font-semibold text-foreground">
            {text.shareImagePreview}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={text.closeShareImagePreview}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-auto bg-background/70 p-3 sm:p-5">
          <img
            src={imageUrl}
            alt={`${reportTitle} ${text.shareImagePreview}`}
            className="mx-auto block h-auto w-full max-w-[720px] bg-white shadow-lg"
          />
        </div>

        <footer className="grid shrink-0 grid-cols-2 gap-2 border-t border-border/60 px-4 py-3 sm:flex sm:flex-wrap sm:justify-end sm:px-5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleCopy()}
            disabled={!canCopyImage || copyState === 'copying'}
            title={canCopyImage ? undefined : text.copyShareImageUnavailable}
          >
            {copyState === 'copying' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            {copyState === 'copied' ? <Check className="h-4 w-4 text-success" aria-hidden="true" /> : null}
            {copyState === 'error' ? <TriangleAlert className="h-4 w-4 text-danger" aria-hidden="true" /> : null}
            {copyState === 'idle' ? <ClipboardCopy className="h-4 w-4" aria-hidden="true" /> : null}
            {copyState === 'copying'
              ? text.copyingShareImage
              : copyState === 'copied'
                ? text.shareImageCopied
                : copyState === 'error'
                  ? text.copyShareImageFailed
                  : text.copyShareImage}
          </Button>
          {canShare ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleSystemShare()}
              disabled={isSharing}
            >
              {isSharing ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Share2 className="h-4 w-4" aria-hidden="true" />
              )}
              {text.systemShareImage}
            </Button>
          ) : null}
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={() => void handleSave()}
            disabled={saveState === 'saving'}
            className={canShare ? 'col-span-2 sm:col-span-1' : ''}
            autoFocus
          >
            {saveState === 'saving' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            {saveState === 'saved' ? <Check className="h-4 w-4" aria-hidden="true" /> : null}
            {saveState === 'error' ? <TriangleAlert className="h-4 w-4" aria-hidden="true" /> : null}
            {saveState === 'idle' ? <Download className="h-4 w-4" aria-hidden="true" /> : null}
            {saveState === 'saving'
              ? text.savingShareImage
              : saveState === 'saved'
                ? text.shareImageSaved
                : saveState === 'error'
                  ? text.saveShareImageFailed
                  : text.saveShareImage}
          </Button>
        </footer>
      </section>
    </div>,
    document.body,
  );
};
