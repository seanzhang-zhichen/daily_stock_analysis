import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReportStockProfile } from '../ReportStockProfile';

describe('ReportStockProfile', () => {
  it('renders deep research content as separated reading sections', () => {
    const report = [
      '# Executive Summary',
      '公司是半导体封装测试企业。',
      '',
      '# Key Findings',
      '核心看点包括先进封装和客户导入。',
      '',
      '## Detailed Analysis',
      '主营业务保持增长。',
    ].join('\n');

    const { container } = render(<ReportStockProfile profile={{ researchReport: report }} />);

    expect(screen.getByText('Executive Summary')).toBeInTheDocument();
    expect(screen.getByText('Key Findings')).toBeInTheDocument();
    expect(screen.getByText('Detailed Analysis')).toBeInTheDocument();
    expect(container.querySelectorAll('.stock-profile-section')).toHaveLength(3);
  });

  it('does not render when the research report is empty', () => {
    const { container } = render(<ReportStockProfile profile={{ researchReport: '   ' }} />);

    expect(container).toBeEmptyDOMElement();
  });
});
