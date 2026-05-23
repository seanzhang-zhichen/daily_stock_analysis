import type React from 'react';
import type { ReportLanguage, ReportStrategy as ReportStrategyType } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportStrategyProps {
  strategy?: ReportStrategyType;
  language?: ReportLanguage;
}

interface StrategyItemProps {
  label: string;
  value?: string;
  toneClassName: string;
  barClassName: string;
}

const StrategyItem: React.FC<StrategyItemProps> = ({
  label,
  value,
  toneClassName,
  barClassName,
}) => (
  <div className="relative overflow-hidden rounded-xl border border-subtle bg-surface-muted/60 p-3">
    <div className="flex flex-col">
      <span className="mb-0.5 text-xs font-medium uppercase tracking-[0.14em] text-muted-text">{label}</span>
      <span className={`text-lg font-bold font-mono ${value ? toneClassName : 'text-muted-text'}`}>
        {value || '—'}
      </span>
    </div>
    <div
      className={`absolute bottom-0 left-0 right-0 h-0.5 ${barClassName}`}
    />
  </div>
);

/**
 * 策略点位区组件 - 终端风格
 */
export const ReportStrategy: React.FC<ReportStrategyProps> = ({ strategy, language = 'zh' }) => {
  if (!strategy) {
    return null;
  }

  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);

  const strategyItems = [
    {
      label: text.idealBuy,
      value: strategy.idealBuy,
      toneClassName: 'text-success',
      barClassName: 'bg-success',
    },
    {
      label: text.secondaryBuy,
      value: strategy.secondaryBuy,
      toneClassName: 'text-primary',
      barClassName: 'bg-primary',
    },
    {
      label: text.stopLoss,
      value: strategy.stopLoss,
      toneClassName: 'text-danger',
      barClassName: 'bg-danger',
    },
    {
      label: text.takeProfit,
      value: strategy.takeProfit,
      toneClassName: 'text-warning',
      barClassName: 'bg-warning',
    },
  ];

  return (
    <Card variant="bordered" padding="md">
      <DashboardPanelHeader
        eyebrow={text.strategyPoints}
        title={text.sniperLevels}
        className="mb-3"
      />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {strategyItems.map((item) => (
          <StrategyItem key={item.label} {...item} />
        ))}
      </div>
    </Card>
  );
};
