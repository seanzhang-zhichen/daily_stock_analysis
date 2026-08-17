import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import { renderShareImageHtml } from '../../../utils/shareImage';
import { ShareImageButton } from '../ShareImageButton';

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

describe('ShareImageButton', () => {
  beforeEach(() => {
    mockedGetShareImage.mockReset();
    mockedGetShareImageHtml.mockReset();
    mockedRenderShareImageHtml.mockReset();
    mockedGetShareImageHtml.mockResolvedValue('<html>poster</html>');
    mockedRenderShareImageHtml.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
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

  it('keeps a disabled entry visible until the report has a history ID', () => {
    render(<ShareImageButton reportTitle="即时报告" reportLanguage="zh" />);

    expect(screen.getByRole('button', { name: '报告保存后可分享' })).toBeDisabled();
    expect(screen.getByText('分享')).toBeInTheDocument();
  });

  it('opens a preview after one click without downloading automatically', async () => {
    render(
      <ShareImageButton recordId={17} reportTitle="中钨高新-000657" reportLanguage="zh" />,
    );

    fireEvent.click(screen.getByRole('button', { name: '分享' }));

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
    render(
      <ShareImageButton recordId={18} reportTitle="A股/市场:复盘" reportLanguage="zh" />,
    );

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
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

    render(<ShareImageButton recordId={181} reportTitle="原生保存" reportLanguage="zh" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
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

    render(<ShareImageButton recordId={182} reportTitle="剪切板报告" reportLanguage="zh" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
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

    render(
      <ShareImageButton recordId={19} reportTitle="A股市场复盘" reportLanguage="zh" />,
    );

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
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

    render(<ShareImageButton recordId={20} reportTitle="中钨高新" reportLanguage="zh" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(await screen.findByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mockedGetShareImage).toHaveBeenCalledWith(20);
  });

  it('falls back to the server renderer and still opens the preview', async () => {
    mockedRenderShareImageHtml.mockRejectedValue(new Error('canvas unavailable'));
    mockedGetShareImage.mockResolvedValue(new Blob(['server-png'], { type: 'image/png' }));

    render(<ShareImageButton recordId={21} reportTitle="服务端回退" reportLanguage="zh" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
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

    render(<ShareImageButton recordId={22} reportTitle="桌面端报告" reportLanguage="zh" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    expect(renderShareImage).toHaveBeenCalledWith(22);
    expect(mockedGetShareImageHtml).not.toHaveBeenCalled();
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  it('releases the preview URL and reopens the cached image without regenerating', async () => {
    render(<ShareImageButton recordId={23} reportTitle="缓存报告" reportLanguage="zh" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭图片预览' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:share-image');

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    expect(mockedGetShareImageHtml).toHaveBeenCalledTimes(1);
    expect(mockedRenderShareImageHtml).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
  });

  it('closes the preview with Escape', async () => {
    render(<ShareImageButton recordId={24} reportTitle="键盘关闭" reportLanguage="zh" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('does not restore a stale preview after switching away and back', async () => {
    const { rerender } = render(
      <ShareImageButton recordId={25} reportTitle="报告A" reportLanguage="zh" />,
    );

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(await screen.findByRole('dialog', { name: '分享图片预览' })).toBeInTheDocument();

    rerender(<ShareImageButton recordId={26} reportTitle="报告B" reportLanguage="zh" />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    rerender(<ShareImageButton recordId={25} reportTitle="报告A" reportLanguage="zh" />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
