import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import { renderShareImageHtml } from '../../../utils/shareImage';
import { ReportShareMenu } from '../ReportShareMenu';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getShareImage: vi.fn(),
    getShareImageHtml: vi.fn(),
  },
}));
vi.mock('../../../utils/shareImage', () => ({
  renderShareImageHtml: vi.fn(),
}));

const mockedGetShareImage = vi.mocked(historyApi.getShareImage);
const mockedGetShareImageHtml = vi.mocked(historyApi.getShareImageHtml);
const mockedRenderShareImageHtml = vi.mocked(renderShareImageHtml);

const defaultExportPdf = vi.fn().mockResolvedValue(undefined);

const renderShareMenu = ({
  recordId,
  reportTitle,
  reportLanguage = 'zh',
  onExportPdf = defaultExportPdf,
}: {
  recordId?: number;
  reportTitle: string;
  reportLanguage?: 'zh' | 'en';
  onExportPdf?: () => Promise<void>;
}) => render(
  <ReportShareMenu
    recordId={recordId}
    reportTitle={reportTitle}
    reportLanguage={reportLanguage}
    onExportPdf={onExportPdf}
  />,
);

const openShareMenu = () => {
  fireEvent.click(screen.getByRole('button', { name: '分享' }));
  return screen.getByRole('menu');
};

const chooseShareImage = () => {
  const menu = openShareMenu();
  fireEvent.click(within(menu).getByRole('menuitem', { name: /^分享图片/ }));
};

describe('ReportShareMenu', () => {
  beforeEach(() => {
    mockedGetShareImage.mockReset();
    mockedGetShareImageHtml.mockReset();
    mockedRenderShareImageHtml.mockReset();
    mockedGetShareImageHtml.mockResolvedValue('<html>poster</html>');
    mockedRenderShareImageHtml.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
    defaultExportPdf.mockReset();
    defaultExportPdf.mockResolvedValue(undefined);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:share-image');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    Object.defineProperty(window, 'dsaDesktop', { configurable: true, value: undefined });
    Object.defineProperty(window, 'showSaveFilePicker', { configurable: true, value: undefined });
    Object.defineProperty(navigator, 'share', { configurable: true, value: undefined });
    Object.defineProperty(navigator, 'canShare', { configurable: true, value: undefined });
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    Object.defineProperty(globalThis, 'ClipboardItem', { configurable: true, value: undefined });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.body.style.overflow = '';
  });

  it('keeps PDF export available while disabling only image sharing for unsaved reports', () => {
    renderShareMenu({ reportTitle: '即时报告' });

    const trigger = screen.getByRole('button', { name: '分享' });
    expect(trigger).toBeEnabled();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    const menu = openShareMenu();
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(within(menu).getByRole('menuitem', { name: /^分享图片/ }))
      .toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('menuitem', { name: '导出 PDF' }))
      .not.toHaveAttribute('aria-disabled');
  });

  it('opens an image preview from the share menu without downloading automatically', async () => {
    renderShareMenu({ recordId: 17, reportTitle: '中钨高新-000657' });

    const menu = openShareMenu();
    expect(mockedGetShareImageHtml).not.toHaveBeenCalled();
    fireEvent.click(within(menu).getByRole('menuitem', { name: '分享图片' }));

    const dialog = await screen.findByRole('dialog', { name: '分享图片预览' });
    expect(mockedGetShareImageHtml).toHaveBeenCalledWith(17);
    expect(mockedRenderShareImageHtml).toHaveBeenCalledWith('<html>poster</html>');
    expect(mockedGetShareImage).not.toHaveBeenCalled();
    expect(within(dialog).getByRole('img')).toHaveAttribute('src', 'blob:share-image');
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
    expect(within(dialog).getByRole('button', { name: '保存到本地' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '系统分享' })).not.toBeInTheDocument();
  });

  it('saves the preview PNG with a safe local filename', async () => {
    let anchorWasConnected = false;
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(function handleDownloadClick(this: HTMLAnchorElement) {
      anchorWasConnected = document.body.contains(this);
    });
    renderShareMenu({ recordId: 18, reportTitle: 'A股/市场:复盘' });

    chooseShareImage();
    const saveButton = await screen.findByRole('button', { name: '保存到本地' });
    fireEvent.click(saveButton);

    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    const anchor = vi.mocked(HTMLAnchorElement.prototype.click).mock.instances[0] as HTMLAnchorElement;
    expect(anchor.href).toBe('blob:share-image');
    expect(anchor.download).toBe('A股-市场-复盘-18.png');
    expect(anchorWasConnected).toBe(true);
  });

  it('writes the PNG directly through the native file picker when available', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn().mockResolvedValue(undefined);
    const showSaveFilePicker = vi.fn().mockResolvedValue({
      createWritable: vi.fn().mockResolvedValue({ write, close }),
    });
    Object.defineProperty(window, 'showSaveFilePicker', {
      configurable: true,
      value: showSaveFilePicker,
    });

    renderShareMenu({ recordId: 181, reportTitle: '原生保存' });

    chooseShareImage();
    fireEvent.click(await screen.findByRole('button', { name: '保存到本地' }));

    await waitFor(() => expect(write).toHaveBeenCalledTimes(1));
    expect(showSaveFilePicker).toHaveBeenCalledWith(expect.objectContaining({
      suggestedName: '原生保存-181.png',
    }));
    expect(write.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect(close).toHaveBeenCalledTimes(1);
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '已保存' })).toBeInTheDocument();
  });

  it('copies the PNG blob to the system clipboard', async () => {
    class MockClipboardItem {
      readonly data: Record<string, Blob>;

      constructor(data: Record<string, Blob>) {
        this.data = data;
      }
    }
    const clipboardWrite = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis, 'ClipboardItem', {
      configurable: true,
      value: MockClipboardItem,
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { write: clipboardWrite },
    });

    renderShareMenu({ recordId: 182, reportTitle: '剪切板报告' });

    chooseShareImage();
    fireEvent.click(await screen.findByRole('button', { name: '复制图片' }));

    await waitFor(() => expect(clipboardWrite).toHaveBeenCalledTimes(1));
    const clipboardItem = clipboardWrite.mock.calls[0][0][0] as MockClipboardItem;
    expect(clipboardItem.data['image/png']).toBeInstanceOf(Blob);
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument();
  });

  it('shares from the preview when native file sharing is available', async () => {
    const nativeShare = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'share', { configurable: true, value: nativeShare });
    Object.defineProperty(navigator, 'canShare', { configurable: true, value: () => true });

    renderShareMenu({ recordId: 19, reportTitle: 'A股市场复盘' });

    chooseShareImage();
    const systemShareButton = await screen.findByRole('button', { name: '系统分享' });
    expect(nativeShare).not.toHaveBeenCalled();

    fireEvent.click(systemShareButton);
    await waitFor(() => expect(nativeShare).toHaveBeenCalledTimes(1));
    const sharePayload = nativeShare.mock.calls[0][0];
    expect(sharePayload.title).toBe('A股市场复盘');
    expect(sharePayload.files[0].name).toBe('A股市场复盘-19.png');
    expect(mockedGetShareImageHtml).toHaveBeenCalledTimes(1);
  });

  it('shows a retryable error state when both renderers fail', async () => {
    mockedRenderShareImageHtml.mockRejectedValue(new Error('browser renderer unavailable'));
    mockedGetShareImage.mockRejectedValue(new Error('server renderer unavailable'));

    renderShareMenu({ recordId: 20, reportTitle: '中钨高新' });

    chooseShareImage();
    await waitFor(() => expect(mockedGetShareImage).toHaveBeenCalledWith(20));
    const retryMenu = openShareMenu();
    expect(within(retryMenu).getByRole('menuitem', { name: '重试' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('falls back to the server renderer and still opens the preview', async () => {
    mockedRenderShareImageHtml.mockRejectedValue(new Error('canvas unavailable'));
    mockedGetShareImage.mockResolvedValue(new Blob(['server-png'], { type: 'image/png' }));

    renderShareMenu({ recordId: 21, reportTitle: '服务端回退' });

    chooseShareImage();
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    expect(mockedGetShareImage).toHaveBeenCalledWith(21);
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  it('uses the desktop renderer and opens the same preview', async () => {
    const renderShareImage = vi.fn().mockResolvedValue(
      new TextEncoder().encode('png').buffer,
    );
    Object.defineProperty(window, 'dsaDesktop', {
      configurable: true,
      value: { version: '3.30.0', renderShareImage },
    });

    renderShareMenu({ recordId: 22, reportTitle: '桌面端报告' });

    chooseShareImage();
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    expect(renderShareImage).toHaveBeenCalledWith(22);
    expect(mockedGetShareImageHtml).not.toHaveBeenCalled();
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  it('releases the preview URL and reopens the cached image without regenerating', async () => {
    renderShareMenu({ recordId: 23, reportTitle: '缓存报告' });

    chooseShareImage();
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭图片预览' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:share-image');

    chooseShareImage();
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    expect(mockedGetShareImageHtml).toHaveBeenCalledTimes(1);
    expect(mockedRenderShareImageHtml).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
  });

  it('closes the preview with Escape', async () => {
    renderShareMenu({ recordId: 24, reportTitle: '键盘关闭' });

    chooseShareImage();
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('exports PDF without starting image generation and keeps the image action available', async () => {
    let finishExport: (() => void) | undefined;
    const exportPdf = vi.fn(() => new Promise<void>((resolve) => {
      finishExport = resolve;
    }));
    renderShareMenu({ recordId: 241, reportTitle: 'PDF 报告', onExportPdf: exportPdf });

    const menu = openShareMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: '导出 PDF' }));

    expect(exportPdf).toHaveBeenCalledTimes(1);
    expect(mockedGetShareImageHtml).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '分享' })).toHaveAttribute('aria-busy', 'true');

    const exportingMenu = openShareMenu();
    expect(within(exportingMenu).getByRole('menuitem', { name: '导出中...' }))
      .toHaveAttribute('aria-disabled', 'true');
    expect(within(exportingMenu).getByRole('menuitem', { name: '分享图片' }))
      .not.toHaveAttribute('aria-disabled');

    finishExport?.();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '分享' })).not.toHaveAttribute('aria-busy');
    });
  });

  it('shows a retryable PDF error without blocking image sharing', async () => {
    const exportPdf = vi.fn()
      .mockRejectedValueOnce(new Error('PDF renderer unavailable'))
      .mockResolvedValueOnce(undefined);
    renderShareMenu({ recordId: 242, reportTitle: 'PDF 重试', onExportPdf: exportPdf });

    fireEvent.click(within(openShareMenu()).getByRole('menuitem', { name: '导出 PDF' }));

    await waitFor(() => expect(exportPdf).toHaveBeenCalledTimes(1));
    const retryMenu = openShareMenu();
    expect(within(retryMenu).getByRole('menuitem', { name: '导出失败，重试' })).toBeInTheDocument();
    expect(within(retryMenu).getByRole('menuitem', { name: '分享图片' }))
      .not.toHaveAttribute('aria-disabled');

    fireEvent.click(within(retryMenu).getByRole('menuitem', { name: '导出失败，重试' }));
    await waitFor(() => expect(exportPdf).toHaveBeenCalledTimes(2));
  });

  it('supports menu keyboard navigation, Escape focus return, and outside-click close', async () => {
    renderShareMenu({ recordId: 243, reportTitle: '键盘菜单' });
    const trigger = screen.getByRole('button', { name: '分享' });

    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    const menu = screen.getByRole('menu');
    const imageItem = within(menu).getByRole('menuitem', { name: '分享图片' });
    const pdfItem = within(menu).getByRole('menuitem', { name: '导出 PDF' });
    await waitFor(() => expect(imageItem).toHaveFocus());

    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    expect(pdfItem).toHaveFocus();
    fireEvent.keyDown(menu, { key: 'Home' });
    expect(imageItem).toHaveFocus();
    fireEvent.keyDown(menu, { key: 'End' });
    expect(pdfItem).toHaveFocus();
    fireEvent.keyDown(menu, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    openShareMenu();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('does not restore a stale preview after switching away and back', async () => {
    const { rerender } = render(
      <ReportShareMenu
        recordId={25}
        reportTitle="报告A"
        reportLanguage="zh"
        onExportPdf={defaultExportPdf}
      />,
    );

    chooseShareImage();
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();

    rerender(
      <ReportShareMenu
        recordId={26}
        reportTitle="报告B"
        reportLanguage="zh"
        onExportPdf={defaultExportPdf}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    rerender(
      <ReportShareMenu
        recordId={25}
        reportTitle="报告A"
        reportLanguage="zh"
        onExportPdf={defaultExportPdf}
      />,
    );

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
