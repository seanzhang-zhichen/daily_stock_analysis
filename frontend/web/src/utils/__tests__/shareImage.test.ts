import { toBlob } from 'html-to-image';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderShareImageHtml } from '../shareImage';

vi.mock('html-to-image', () => ({
  toBlob: vi.fn(),
}));

const mockedToBlob = vi.mocked(toBlob);

describe('renderShareImageHtml', () => {
  beforeEach(() => {
    mockedToBlob.mockReset();
    mockedToBlob.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
  });

  afterEach(() => {
    document.querySelectorAll('iframe').forEach((iframe) => iframe.remove());
  });

  it('renders the isolated poster at its measured height and removes the iframe', async () => {
    const rendering = renderShareImageHtml('<html><body><main class="poster">report</main></body></html>');
    const iframe = document.querySelector('iframe');
    expect(iframe).not.toBeNull();
    expect(iframe?.getAttribute('sandbox')).toBe('allow-same-origin');

    const iframeDocument = iframe?.contentDocument;
    expect(iframeDocument).not.toBeNull();
    if (!iframe || !iframeDocument) throw new Error('Test iframe was not created');
    iframeDocument.body.innerHTML = '<main class="poster">report</main>';
    const poster = iframeDocument.querySelector<HTMLElement>('.poster');
    if (!poster) throw new Error('Test poster was not created');
    Object.defineProperty(poster, 'scrollHeight', { configurable: true, value: 1440 });
    vi.spyOn(poster, 'getBoundingClientRect').mockReturnValue({
      width: 1080,
      height: 1439.5,
      top: 0,
      right: 1080,
      bottom: 1439.5,
      left: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    iframe.dispatchEvent(new Event('load'));

    await expect(rendering).resolves.toBeInstanceOf(Blob);
    const [renderedNode, renderOptions] = mockedToBlob.mock.calls[0];
    expect(renderedNode).toBe(poster);
    expect(renderOptions).toEqual(expect.objectContaining({
      width: 1080,
      height: 1440,
      canvasWidth: 1080,
      canvasHeight: 1440,
      pixelRatio: 1,
    }));
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('rejects invalid poster HTML and still removes the iframe', async () => {
    const rendering = renderShareImageHtml('<html><body>missing poster</body></html>');
    const iframe = document.querySelector('iframe');
    expect(iframe?.contentDocument).not.toBeNull();
    iframe?.dispatchEvent(new Event('load'));

    await expect(rendering).rejects.toThrow('does not contain a poster');
    expect(mockedToBlob).not.toHaveBeenCalled();
    expect(document.querySelector('iframe')).toBeNull();
  });
});
