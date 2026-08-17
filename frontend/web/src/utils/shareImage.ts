import { toBlob } from 'html-to-image';

const SHARE_IMAGE_WIDTH = 1080;
const SHARE_IMAGE_MAX_HEIGHT = 20000;
const SHARE_IMAGE_LOAD_TIMEOUT_MS = 10000;

const waitForIframeLoad = (iframe: HTMLIFrameElement, html: string): Promise<void> => (
  new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      reject(new Error('Share image HTML did not finish loading'));
    }, SHARE_IMAGE_LOAD_TIMEOUT_MS);

    iframe.addEventListener('load', () => {
      window.clearTimeout(timeoutId);
      resolve();
    }, { once: true });
    iframe.srcdoc = html;
  })
);

const waitForImages = async (documentNode: Document): Promise<void> => {
  const images = Array.from(documentNode.images);
  await Promise.all(images.map(async (image) => {
    if (image.complete) return;
    try {
      await image.decode();
    } catch {
      // The renderer can still capture the poster when an optional image fails.
    }
  }));
};

export const renderShareImageHtml = async (html: string): Promise<Blob> => {
  const iframe = document.createElement('iframe');
  iframe.setAttribute('sandbox', 'allow-same-origin');
  iframe.setAttribute('aria-hidden', 'true');
  Object.assign(iframe.style, {
    position: 'fixed',
    left: '-12000px',
    top: '0',
    width: `${SHARE_IMAGE_WIDTH}px`,
    height: '1px',
    border: '0',
    pointerEvents: 'none',
  });
  document.body.appendChild(iframe);

  try {
    await waitForIframeLoad(iframe, html);
    const iframeDocument = iframe.contentDocument;
    const poster = iframeDocument?.querySelector<HTMLElement>('.poster');
    if (!iframeDocument || !poster) {
      throw new Error('Share image HTML does not contain a poster');
    }

    await iframeDocument.fonts?.ready;
    await waitForImages(iframeDocument);

    const height = Math.ceil(Math.max(
      poster.scrollHeight,
      poster.getBoundingClientRect().height,
    ));
    if (height < 1 || height > SHARE_IMAGE_MAX_HEIGHT) {
      throw new Error(`Share image has invalid dimensions: ${SHARE_IMAGE_WIDTH}x${height}`);
    }
    iframe.style.height = `${height}px`;

    const blob = await toBlob(poster, {
      width: SHARE_IMAGE_WIDTH,
      height,
      canvasWidth: SHARE_IMAGE_WIDTH,
      canvasHeight: height,
      pixelRatio: 1,
      backgroundColor: '#eef4fd',
      cacheBust: true,
      skipAutoScale: true,
    });
    if (!blob || blob.size === 0) {
      throw new Error('Browser share image renderer returned an empty image');
    }
    return blob;
  } finally {
    iframe.remove();
  }
};
