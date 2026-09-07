import React from 'react';
import type { MarketStructureContext, ReportLanguage } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';

interface MarketStructureCardProps {
  context?: MarketStructureContext;
  language?: ReportLanguage;
}

const text = {
  zh: {
    eyebrow: '市场结构',
    title: '题材与个股位置',
    active: '活跃题材',
    industries: '领涨行业',
    concepts: '领涨概念',
    primary: '主要关联题材',
    role: '个股角色',
    phase: '题材阶段',
    boards: '关联板块',
    partial: '数据不完整，仅作辅助参考',
    unknown: '暂无足够市场结构数据',
  },
  en: {
    eyebrow: 'Market structure',
    title: 'Theme and stock position',
    active: 'Active themes',
    industries: 'Leading industries',
    concepts: 'Leading concepts',
    primary: 'Primary theme',
    role: 'Stock role',
    phase: 'Theme phase',
    boards: 'Related boards',
    partial: 'Incomplete data; use as supporting evidence only',
    unknown: 'Insufficient market-structure data',
  },
} as const;

const names = (items: Array<{ name?: string; changePct?: number }> | undefined): string[] =>
  (Array.isArray(items) ? items : [])
    .filter((item) => item?.name)
    .slice(0, 5)
    .map((item) => {
      const change = typeof item.changePct === 'number' && Number.isFinite(item.changePct)
        ? ` (${item.changePct > 0 ? '+' : ''}${item.changePct.toFixed(2)}%)`
        : '';
      return `${item.name}${change}`;
    });

export const MarketStructureCard: React.FC<MarketStructureCardProps> = ({ context, language = 'zh' }) => {
  if (!context || context.market === 'hk' || context.market === 'us' || context.status === 'not_supported') {
    return null;
  }
  const labels = text[language === 'en' ? 'en' : 'zh'];
  const marketTheme = context.marketThemeContext;
  const position = context.stockMarketPosition;
  const status = context.status || 'unknown';
  const active = names(marketTheme?.activeThemes);
  const industries = names(marketTheme?.leadingIndustries);
  const concepts = names(marketTheme?.leadingConcepts);
  const primary = position?.primaryTheme?.name;
  const boards = (position?.relatedBoards || []).map((item) => item.name).filter(Boolean).slice(0, 4) as string[];

  if (status === 'unknown' && !primary && !active.length && !industries.length && !concepts.length) {
    return (
      <Card variant="bordered" padding="md" className="text-left">
        <DashboardPanelHeader eyebrow={labels.eyebrow} title={labels.title} className="mb-2" />
        <p className="text-sm text-muted-text">{labels.unknown}</p>
      </Card>
    );
  }

  const row = (label: string, values: string[]) => values.length ? (
    <div className="grid grid-cols-[7rem_1fr] gap-3 border-b border-subtle py-2 last:border-0">
      <span className="text-xs text-muted-text">{label}</span>
      <span className="text-sm text-foreground">{values.join('、')}</span>
    </div>
  ) : null;

  return (
    <Card variant="bordered" padding="md" className="text-left">
      <DashboardPanelHeader eyebrow={labels.eyebrow} title={labels.title} className="mb-2" />
      {row(labels.active, active)}
      {row(labels.industries, industries)}
      {row(labels.concepts, concepts)}
      {row(labels.boards, boards)}
      {primary && (
        <div className="grid grid-cols-[7rem_1fr] gap-3 border-b border-subtle py-2">
          <span className="text-xs text-muted-text">{labels.primary}</span>
          <span className="text-sm font-medium text-foreground">{primary}</span>
        </div>
      )}
      {(position?.stockRole || position?.themePhase) && (
        <div className="grid grid-cols-2 gap-3 pt-2 text-xs text-muted-text">
          {position.stockRole && <span>{labels.role}: <strong className="text-foreground">{position.stockRole}</strong></span>}
          {position.themePhase && <span>{labels.phase}: <strong className="text-foreground">{position.themePhase}</strong></span>}
        </div>
      )}
      {status === 'partial' && <p className="mt-2 text-xs text-muted-text">{labels.partial}</p>}
    </Card>
  );
};

export default MarketStructureCard;
