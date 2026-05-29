import type React from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ReportLanguage, StockProfile } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportStockProfileProps {
  profile?: StockProfile;
  language?: ReportLanguage;
}

const splitReportSections = (report: string): string[] => {
  const sections: string[][] = [];
  let current: string[] = [];
  let inFence = false;

  report.split(/\r?\n/).forEach((line) => {
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
    }

    if (!inFence && /^#{1,2}\s+\S/.test(line) && current.some((item) => item.trim())) {
      sections.push(current);
      current = [];
    }

    current.push(line);
  });

  if (current.some((line) => line.trim())) {
    sections.push(current);
  }

  return sections.map((section) => section.join('\n').trim()).filter(Boolean);
};

export const ReportStockProfile: React.FC<ReportStockProfileProps> = ({
  profile,
  language = 'zh',
}) => {
  const report = (profile?.researchReport || '').trim();
  if (!report) {
    return null;
  }

  const reportLanguage = normalizeReportLanguage(language);
  const labels = reportLanguage === 'en'
    ? {
        eyebrow: 'DEEP RESEARCH',
        title: 'Stock Profile',
        description: 'A concise company and fundamentals overview generated through the Deep Research workflow.',
      }
    : {
        eyebrow: '深度研究',
        title: '',
        description: '',
      };

  const sections = splitReportSections(report);

  return (
    <Card variant="bordered" padding="lg" className="stock-profile-card overflow-hidden text-left">
      <DashboardPanelHeader
        eyebrow={labels.eyebrow}
        title={labels.title}
        className="mb-2"
      />
      {labels.description && <p className="mb-4 text-xs leading-5 text-muted-text">{labels.description}</p>}

      <div className="stock-profile-body">
        {sections.map((section, index) => (
          <section
            key={`${index}-${section.length}`}
            className="stock-profile-section stock-profile-prose ui-prose prose prose-invert prose-sm max-w-none
              prose-headings:text-foreground prose-headings:font-semibold
              prose-h1:text-lg prose-h2:text-base prose-h3:text-sm
              prose-p:leading-relaxed
              prose-strong:text-foreground prose-strong:font-semibold
              prose-ul:my-2 prose-ol:my-2
              prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none
              prose-pre:border prose-table:border-collapse
              prose-a:no-underline hover:prose-a:underline prose-blockquote:text-secondary-text
              break-words"
          >
            <Markdown remarkPlugins={[remarkGfm]}>{section}</Markdown>
          </section>
        ))}
      </div>
    </Card>
  );
};

export default ReportStockProfile;
