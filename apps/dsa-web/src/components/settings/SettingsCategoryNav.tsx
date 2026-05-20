import type React from 'react';
import { Badge } from '../common';
import { getCategoryDescriptionZh, getCategoryTitleZh } from '../../utils/systemConfigI18n';
import type { SystemConfigCategorySchema, SystemConfigItem } from '../../types/systemConfig';
import { cn } from '../../utils/cn';

interface SettingsCategoryNavProps {
  categories: SystemConfigCategorySchema[];
  itemsByCategory: Record<string, SystemConfigItem[]>;
  activeCategory: string;
  onSelect: (category: string) => void;
}

export const SettingsCategoryNav: React.FC<SettingsCategoryNavProps> = ({
  categories,
  itemsByCategory,
  activeCategory,
  onSelect,
}) => {
  return (
    <div className="h-full rounded-[1.5rem] border border-border/60 bg-card/94 p-4 shadow-card backdrop-blur-sm">
      <div className="mb-4">
        <p className="text-primary text-xs font-semibold uppercase tracking-[0.3em]">配置分类</p>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-text">按模块整理系统设置与认证能力。</p>
      </div>

      <div className="space-y-2.5">
        {categories.map((category) => {
          const isActive = category.category === activeCategory;
          const count = (itemsByCategory[category.category] || []).length;
          const title = getCategoryTitleZh(category.category, category.title);
          const description = getCategoryDescriptionZh(category.category, category.description);

          return (
            <button
              key={category.category}
              type="button"
              className={cn(
                'w-full rounded-[1.1rem] border px-3 py-3 text-left transition-[background-color,border-color,box-shadow,transform] duration-200',
                isActive
                  ? 'border-primary/36 bg-primary/12 text-foreground shadow-[0_0_0_1px_hsl(var(--primary)/0.18)_inset]'
                  : 'border-border/60 bg-card/94 hover:border-primary/32 hover:bg-primary/5',
              )}
              onClick={() => onSelect(category.category)}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className={cn('text-sm font-semibold tracking-tight', isActive ? 'text-foreground' : 'text-secondary-text')}>
                    {title}
                  </p>
                  {description ? (
                    <p className={cn('mt-1 line-clamp-2 text-xs leading-5', isActive ? 'text-secondary-text' : 'text-muted-text')}>{description}</p>
                  ) : null}
                </div>
                <Badge
                  variant={isActive ? 'info' : 'default'}
                  size="sm"
                  className={isActive ? 'border-primary/36 bg-primary/10 text-primary' : 'border-border/60 bg-card/97 text-muted-text'}
                >
                  {count}
                </Badge>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
};
