import { beforeEach, describe, expect, it, vi } from 'vitest';
import { buildReportPdfFilename, exportReportToPdf } from '../reportPdf';

const pdfMocks = vi.hoisted(() => {
  const worker = {
    set: vi.fn(),
    from: vi.fn(),
    toContainer: vi.fn(),
    get: vi.fn(),
    save: vi.fn(),
  };
  worker.set.mockReturnValue(worker);
  worker.from.mockReturnValue(worker);
  worker.toContainer.mockReturnValue(worker);
  worker.get.mockImplementation((_key: string, callback?: (value: unknown) => unknown) => (
    Promise.resolve(callback?.(document.createElement('div')))
  ));
  worker.save.mockResolvedValue(undefined);
  return {
    factory: vi.fn(() => worker),
    worker,
  };
});

vi.mock('html2pdf.js', () => ({
  default: pdfMocks.factory,
}));

describe('reportPdf', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pdfMocks.worker.set.mockReturnValue(pdfMocks.worker);
    pdfMocks.worker.from.mockReturnValue(pdfMocks.worker);
    pdfMocks.worker.toContainer.mockReturnValue(pdfMocks.worker);
    pdfMocks.worker.get.mockImplementation((_key: string, callback?: (value: unknown) => unknown) => (
      Promise.resolve(callback?.(document.createElement('div')))
    ));
    pdfMocks.worker.save.mockResolvedValue(undefined);
  });

  it('builds filesystem-safe localized filenames', () => {
    expect(buildReportPdfFilename('贵州/茅台', '600519', 'zh'))
      .toBe('贵州-茅台-600519-分析报告.pdf');
    expect(buildReportPdfFilename('AAPL', 'AAPL', 'en'))
      .toBe('AAPL-Analysis-Report.pdf');
  });

  it('clones a wide desktop report into the A4 content width', async () => {
    const element = document.createElement('div');
    element.dataset.pdfReport = '';
    element.innerHTML = '<div class="ui-card">Report</div><div data-pdf-hide>Actions</div>';
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
      width: 1088,
      height: 1200,
      top: 0,
      right: 1088,
      bottom: 1200,
      left: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });

    await exportReportToPdf(element, 'report.pdf');

    expect(pdfMocks.factory).toHaveBeenCalledTimes(1);
    expect(pdfMocks.worker.set).toHaveBeenCalledWith(expect.objectContaining({
      filename: 'report.pdf',
      html2canvas: expect.objectContaining({ windowWidth: 718 }),
      jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
      pagebreak: {
        mode: ['css', 'legacy'],
        avoid: [
          '.pdf-export-surface .report-overview .ui-card',
          '.pdf-export-surface .report-strategy',
          '.pdf-export-surface .report-price-history',
          '.pdf-export-surface .report-news-item',
          '.pdf-export-surface .stock-profile-section',
        ],
      },
    }));
    const exportedElement = pdfMocks.worker.from.mock.calls[0]?.[0] as HTMLElement;
    expect(exportedElement).not.toBe(element);
    expect(exportedElement.classList).toContain('pdf-export-surface');
    expect(exportedElement.style.width).toBe('100%');
    expect(exportedElement.style.minWidth).toBe('0px');
    expect(exportedElement.style.maxWidth).toBe('100%');
    expect(exportedElement.style.boxSizing).toBe('border-box');
    expect(element.style.width).toBe('');
    expect(pdfMocks.worker.toContainer).toHaveBeenCalledTimes(1);
    expect(pdfMocks.worker.get).toHaveBeenCalledWith('container', expect.any(Function));
    expect(pdfMocks.worker.save).toHaveBeenCalledTimes(1);
  });

  it('uses the A4 viewport width without forcing a desktop-width clone on narrow screens', async () => {
    const element = document.createElement('div');
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
      width: 360,
      height: 1200,
      top: 0,
      right: 360,
      bottom: 1200,
      left: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });

    await exportReportToPdf(element, 'mobile-report.pdf');

    expect(pdfMocks.worker.set).toHaveBeenCalledWith(expect.objectContaining({
      html2canvas: expect.objectContaining({ windowWidth: 718 }),
    }));
    const exportedElement = pdfMocks.worker.from.mock.calls[0]?.[0] as HTMLElement;
    expect(exportedElement.style.width).toBe('100%');
    expect(exportedElement.style.minWidth).toBe('0px');
  });

  it('realigns protected blocks against the final html2canvas page height', async () => {
    const element = document.createElement('div');
    await exportReportToPdf(element, 'paginated-report.pdf');

    const options = pdfMocks.worker.set.mock.calls[0]?.[0] as {
      html2canvas?: { onclone?: (clonedDocument: Document) => void };
    };
    const onclone = options.html2canvas?.onclone;
    expect(onclone).toBeTypeOf('function');

    const container = document.createElement('div');
    container.className = 'html2pdf__container';
    container.innerHTML = `
      <div class="pdf-export-surface">
        <section class="report-price-history" style="margin-top: 6px">Price history</section>
        <article class="report-news-item" style="margin-top: 4px">News</article>
      </div>
    `;
    const protectedBlock = container.querySelector<HTMLElement>('.report-price-history');
    const containedBlock = container.querySelector<HTMLElement>('.report-news-item');
    if (!protectedBlock || !containedBlock || !onclone) {
      throw new Error('Pagination fixture did not render');
    }

    Object.defineProperty(container, 'scrollWidth', { configurable: true, value: 718 });
    vi.spyOn(container, 'getBoundingClientRect').mockReturnValue({
      width: 718,
      height: 1600,
      top: 24,
      right: 718,
      bottom: 1624,
      left: 0,
      x: 0,
      y: 24,
      toJSON: () => ({}),
    });
    vi.spyOn(protectedBlock, 'getBoundingClientRect').mockReturnValue({
      width: 680,
      height: 120,
      top: 1024,
      right: 680,
      bottom: 1144,
      left: 0,
      x: 0,
      y: 1024,
      toJSON: () => ({}),
    });
    vi.spyOn(containedBlock, 'getBoundingClientRect').mockReturnValue({
      width: 680,
      height: 100,
      top: 200,
      right: 680,
      bottom: 300,
      left: 0,
      x: 0,
      y: 200,
      toJSON: () => ({}),
    });

    document.body.append(container);
    try {
      onclone(document);
      expect(protectedBlock.style.getPropertyValue('margin-top')).toBe('54.5px');
      expect(protectedBlock.style.getPropertyPriority('margin-top')).toBe('important');
      expect(containedBlock.style.getPropertyValue('margin-top')).toBe('4px');
    } finally {
      container.remove();
    }
  });

  it('materializes SVG CSS variables without breaking gradient references', async () => {
    const element = document.createElement('div');
    element.innerHTML = `
      <svg>
        <defs>
          <linearGradient id="gradient">
            <stop data-kind="stop" stop-color="hsl(var(--color-primary))" />
          </linearGradient>
        </defs>
        <path data-kind="attribute" stroke="hsl(var(--color-primary))" fill="url(#gradient)" />
        <path data-kind="style" style="stroke: hsl(var(--color-warning))" />
      </svg>
    `;
    const stop = element.querySelector('[data-kind="stop"]');
    const attributePath = element.querySelector('[data-kind="attribute"]');
    const stylePath = element.querySelector('[data-kind="style"]');
    if (!(stop instanceof SVGElement)
      || !(attributePath instanceof SVGElement)
      || !(stylePath instanceof SVGElement)) {
      throw new Error('SVG fixture did not render');
    }

    const computedValues = new Map<Element, Record<string, string>>([
      [stop, { 'stop-color': 'rgb(8, 145, 178)' }],
      [attributePath, { stroke: 'rgb(8, 145, 178)' }],
      [stylePath, { stroke: 'rgb(217, 119, 6)' }],
    ]);
    const getComputedStyleSpy = vi.spyOn(window, 'getComputedStyle').mockImplementation((node) => (
      {
        getPropertyValue: (property: string) => computedValues.get(node)?.[property] ?? '',
      } as CSSStyleDeclaration
    ));

    try {
      await exportReportToPdf(element, 'svg-report.pdf');
      expect(getComputedStyleSpy).toHaveBeenCalled();
    } finally {
      getComputedStyleSpy.mockRestore();
    }

    const exportedElement = pdfMocks.worker.from.mock.calls[0]?.[0] as HTMLElement;
    const exportedStop = exportedElement.querySelector('[data-kind="stop"]');
    const exportedAttributePath = exportedElement.querySelector('[data-kind="attribute"]');
    const exportedStylePath = exportedElement.querySelector('[data-kind="style"]');
    expect(exportedStop?.getAttribute('stop-color')).toBe('rgb(8, 145, 178)');
    expect(exportedAttributePath?.getAttribute('stroke')).toBe('rgb(8, 145, 178)');
    expect(exportedAttributePath?.getAttribute('fill')).toBe('url(#gradient)');
    expect(exportedStylePath instanceof SVGElement
      ? exportedStylePath.style.getPropertyValue('stroke')
      : '').toBe('rgb(217, 119, 6)');
    expect(attributePath.getAttribute('stroke')).toBe('hsl(var(--color-primary))');
    expect(stop.getAttribute('stop-color')).toBe('hsl(var(--color-primary))');
  });

});
