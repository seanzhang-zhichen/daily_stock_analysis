const INVALID_FILENAME_CHARS = /[<>:"/\\|?*]/;
const A4_PAGE_WIDTH_MM = 210;
const A4_PAGE_HEIGHT_MM = 297;
const PDF_MARGIN_MM = 10;
const CSS_PIXELS_PER_MM = 96 / 25.4;
const PDF_VIEWPORT_WIDTH = Math.floor(
  (A4_PAGE_WIDTH_MM - PDF_MARGIN_MM * 2) * CSS_PIXELS_PER_MM,
);
const SVG_PAINT_PROPERTIES = ['stroke', 'fill', 'stop-color', 'color'] as const;
const PDF_CHART_RASTER_SCALE = 2;
const PDF_CAPTURE_SCALE = 2;
const PDF_PAGEBREAK_SAFETY_PX = 2;
const PDF_PAGEBREAK_AVOID_SELECTORS = [
  '.pdf-export-surface .report-overview .ui-card',
  '.pdf-export-surface .report-strategy',
  '.pdf-export-surface .report-price-history',
  '.pdf-export-surface .report-news-item',
  '.pdf-export-surface .stock-profile-section',
];

type SvgPaintProperty = (typeof SVG_PAINT_PROPERTIES)[number];

const materializeSvgCssVariables = (sourceRoot: Element, cloneRoot: Element): void => {
  const sourceNodes = sourceRoot.querySelectorAll<SVGElement>('svg, svg *');
  const cloneNodes = cloneRoot.querySelectorAll<SVGElement>('svg, svg *');

  sourceNodes.forEach((sourceNode, index) => {
    const cloneNode = cloneNodes[index];
    if (!cloneNode || cloneNode.localName !== sourceNode.localName) {
      return;
    }

    const authoredProperties = SVG_PAINT_PROPERTIES.filter((property: SvgPaintProperty) => {
      const authoredAttribute = sourceNode.getAttribute(property);
      const authoredStyle = sourceNode.style.getPropertyValue(property);
      return [authoredAttribute, authoredStyle].some((value) => (
        typeof value === 'string'
        && value.includes('var(')
        && !value.trim().startsWith('url(')
      ));
    });
    if (authoredProperties.length === 0) {
      return;
    }

    const computedStyle = window.getComputedStyle(sourceNode);
    authoredProperties.forEach((property: SvgPaintProperty) => {
      const authoredAttribute = sourceNode.getAttribute(property);
      const authoredStyle = sourceNode.style.getPropertyValue(property);
      const resolvedValue = computedStyle.getPropertyValue(property).trim();
      if (!resolvedValue || resolvedValue.includes('var(')) {
        return;
      }
      if (authoredAttribute?.includes('var(') && !authoredAttribute.trim().startsWith('url(')) {
        cloneNode.setAttribute(property, resolvedValue);
      }
      if (authoredStyle.includes('var(') && !authoredStyle.trim().startsWith('url(')) {
        cloneNode.style.setProperty(property, resolvedValue);
      }
    });
  });
};

const waitForImage = (image: HTMLImageElement, source: string): Promise<void> => new Promise((resolve, reject) => {
  image.onload = () => resolve();
  image.onerror = () => reject(new Error('Unable to rasterize SVG chart'));
  image.src = source;
  if (image.complete && image.naturalWidth > 0) {
    resolve();
  }
});

const waitForClonedImages = async (container: HTMLElement): Promise<void> => {
  const images = Array.from(container.querySelectorAll<HTMLImageElement>('img'));

  await Promise.all(images.map((image) => {
    const decode = () => (
      typeof image.decode === 'function'
        ? image.decode().catch(() => undefined)
        : Promise.resolve()
    );

    if (!image.src || image.complete) {
      return decode();
    }

    return new Promise<void>((resolve) => {
      let settled = false;
      const settle = () => {
        if (settled) {
          return;
        }
        settled = true;
        image.removeEventListener('load', settle);
        image.removeEventListener('error', settle);
        void decode().then(() => resolve());
      };

      image.addEventListener('load', settle, { once: true });
      image.addEventListener('error', settle, { once: true });
      if (image.complete) {
        settle();
      }
    });
  }));
};

const isPageBreakPadding = (element: Element | null): element is HTMLElement => {
  if (!(element instanceof HTMLElement)
    || element.className
    || element.childElementCount > 0
    || element.textContent?.trim()
    || element.style.display !== 'block') {
    return false;
  }

  const height = Number.parseFloat(element.style.height);
  return Number.isFinite(height) && height > 0;
};

const stabilizePageBreakOffsets = (container: HTMLElement): void => {
  const protectedBlocks = container.querySelectorAll<HTMLElement>(
    PDF_PAGEBREAK_AVOID_SELECTORS.join(', '),
  );

  protectedBlocks.forEach((block) => {
    const padding = block.previousElementSibling;
    if (!isPageBreakPadding(padding)) {
      return;
    }

    const desiredTop = block.getBoundingClientRect().top;
    const currentMarginTop = Number.parseFloat(window.getComputedStyle(block).marginTop) || 0;
    padding.remove();

    let appliedMargin = currentMarginTop + desiredTop - block.getBoundingClientRect().top;
    block.style.setProperty('margin-top', `${appliedMargin}px`, 'important');

    const residualOffset = desiredTop - block.getBoundingClientRect().top;
    if (Math.abs(residualOffset) > 0.25) {
      appliedMargin += residualOffset;
      block.style.setProperty('margin-top', `${appliedMargin}px`, 'important');
    }
  });
};

const prepareHtml2CanvasClone = (clonedDocument: Document): void => {
  const container = clonedDocument.querySelector<HTMLElement>('.html2pdf__container');
  if (!container) {
    return;
  }

  const canvasWidth = Math.max(container.scrollWidth, Math.ceil(container.getBoundingClientRect().width))
    * PDF_CAPTURE_SCALE;
  const pageRatio = (
    (A4_PAGE_HEIGHT_MM - PDF_MARGIN_MM * 2)
    / (A4_PAGE_WIDTH_MM - PDF_MARGIN_MM * 2)
  );
  const pageHeight = Math.floor(canvasWidth * pageRatio) / PDF_CAPTURE_SCALE;
  const containerTop = container.getBoundingClientRect().top;
  const protectedBlocks = container.querySelectorAll<HTMLElement>(
    PDF_PAGEBREAK_AVOID_SELECTORS.join(', '),
  );

  protectedBlocks.forEach((block) => {
    const rect = block.getBoundingClientRect();
    const top = rect.top - containerTop;
    const bottom = rect.bottom - containerTop;
    const startPage = Math.floor(top / pageHeight);
    const endPage = Math.floor(Math.max(bottom - 0.5, top) / pageHeight);
    if (endPage === startPage || rect.height + PDF_PAGEBREAK_SAFETY_PX > pageHeight) {
      return;
    }

    const shift = (startPage + 1) * pageHeight - top + PDF_PAGEBREAK_SAFETY_PX;
    const marginTop = Number.parseFloat(clonedDocument.defaultView?.getComputedStyle(block).marginTop || '') || 0;
    block.style.setProperty('margin-top', `${marginTop + shift}px`, 'important');
  });
};

const rasterizeSvgChart = async (sourceSvg: SVGSVGElement): Promise<{
  dataUrl: string;
  width: number;
  height: number;
}> => {
  const rect = sourceSvg.getBoundingClientRect();
  const width = Math.max(
    Math.round(rect.width || sourceSvg.clientWidth || Number.parseFloat(sourceSvg.getAttribute('width') || '0')),
    1,
  );
  const height = Math.max(
    Math.round(rect.height || sourceSvg.clientHeight || Number.parseFloat(sourceSvg.getAttribute('height') || '0')),
    1,
  );
  const serializedSvg = sourceSvg.cloneNode(true) as SVGSVGElement;
  materializeSvgCssVariables(sourceSvg, serializedSvg);
  serializedSvg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  serializedSvg.setAttribute('width', String(width));
  serializedSvg.setAttribute('height', String(height));
  serializedSvg.style.width = `${width}px`;
  serializedSvg.style.height = `${height}px`;

  const image = new Image();
  image.decoding = 'sync';
  const imageSource = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
    new XMLSerializer().serializeToString(serializedSvg),
  )}`;
  await waitForImage(image, imageSource);

  const canvas = document.createElement('canvas');
  canvas.width = width * PDF_CHART_RASTER_SCALE;
  canvas.height = height * PDF_CHART_RASTER_SCALE;
  const context = canvas.getContext('2d');
  if (!context) {
    throw new Error('Unable to create chart raster canvas');
  }
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return {
    dataUrl: canvas.toDataURL('image/png'),
    width,
    height,
  };
};

const rasterizeRechartsCharts = async (sourceRoot: HTMLElement, cloneRoot: HTMLElement): Promise<void> => {
  const sourceCharts = Array.from(sourceRoot.querySelectorAll<SVGSVGElement>('.recharts-surface'));
  const cloneCharts = Array.from(cloneRoot.querySelectorAll<SVGSVGElement>('.recharts-surface'));

  await Promise.all(sourceCharts.map(async (sourceChart, index) => {
    const cloneChart = cloneCharts[index];
    if (!cloneChart) {
      return;
    }
    try {
      const raster = await rasterizeSvgChart(sourceChart);
      const image = cloneRoot.ownerDocument.createElement('img');
      image.className = 'pdf-chart-raster';
      image.alt = '';
      image.decoding = 'sync';
      image.width = raster.width;
      image.height = raster.height;
      image.src = raster.dataUrl;
      image.style.display = 'block';
      image.style.maxWidth = '100%';
      image.style.width = '100%';
      image.style.height = '100%';
      image.setAttribute('data-pdf-rasterized-chart', 'true');
      const responsiveContainer = cloneChart.closest<HTMLElement>('.recharts-responsive-container');
      if (responsiveContainer) {
        responsiveContainer.replaceChildren(image);
      } else {
        cloneChart.replaceWith(image);
      }
    } catch {
      // Keep the materialized SVG as a fallback when a browser cannot decode it.
    }
  }));
};

const safeFilenamePart = (value: string): string => (
  Array.from(value.trim(), (char) => (
    char.charCodeAt(0) < 32 || INVALID_FILENAME_CHARS.test(char) ? '-' : char
  ))
    .join('')
    .replace(/[.\s]+$/g, '')
    .slice(0, 80) || 'report'
);

export const buildReportPdfFilename = (
  reportTitle: string,
  stockCode: string,
  language: 'zh' | 'en',
): string => {
  const safeTitle = safeFilenamePart(reportTitle);
  const safeCode = safeFilenamePart(stockCode);
  const suffix = language === 'en' ? 'Analysis-Report' : '分析报告';
  const parts = safeTitle === safeCode ? [safeCode, suffix] : [safeTitle, safeCode, suffix];
  return `${parts.join('-')}.pdf`;
};

export const exportReportToPdf = async (element: HTMLElement, filename: string): Promise<void> => {
  const { default: html2pdf } = await import('html2pdf.js');
  await document.fonts?.ready;

  const exportElement = element.cloneNode(true) as HTMLElement;
  materializeSvgCssVariables(element, exportElement);
  await rasterizeRechartsCharts(element, exportElement);
  exportElement.classList.add('pdf-export-surface');
  exportElement.style.width = '100%';
  exportElement.style.minWidth = '0';
  exportElement.style.maxWidth = '100%';
  exportElement.style.boxSizing = 'border-box';
  const options = {
    margin: [PDF_MARGIN_MM, PDF_MARGIN_MM, PDF_MARGIN_MM, PDF_MARGIN_MM] as [number, number, number, number],
    filename,
    image: { type: 'jpeg' as const, quality: 0.98 },
    enableLinks: true,
    html2canvas: {
      scale: PDF_CAPTURE_SCALE,
      useCORS: true,
      backgroundColor: '#ffffff',
      logging: false,
      scrollX: 0,
      scrollY: 0,
      windowWidth: PDF_VIEWPORT_WIDTH,
      onclone: prepareHtml2CanvasClone,
    },
    jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' as const },
    pagebreak: {
      mode: ['css', 'legacy'],
      avoid: PDF_PAGEBREAK_AVOID_SELECTORS,
    },
  };

  const worker = html2pdf().set(options).from(exportElement);
  await worker.toContainer();
  await worker.get('container', (container) => {
    if (container instanceof HTMLElement) {
      stabilizePageBreakOffsets(container);
      return waitForClonedImages(container);
    }
    return undefined;
  });
  await worker.save();
};
