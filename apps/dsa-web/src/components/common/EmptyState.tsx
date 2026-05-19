import type React from 'react';
import { cn } from '../../utils/cn';

interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title,
  description,
  icon,
  action,
  className = '',
}) => {
  return (
    <div className={cn(
      'rounded-2xl border border-dashed border-border/50 bg-card/40 px-8 py-12 text-center',
      'transition-colors duration-200',
      className
    )}>
      {icon ? (
        <div className="mb-5 flex justify-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-primary/20 bg-primary/8 text-primary/70">
            {icon}
          </div>
        </div>
      ) : null}
      <h3 className="text-[15px] font-semibold tracking-tight text-foreground/90">{title}</h3>
      {description ? (
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-text">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-6 flex justify-center">{action}</div> : null}
    </div>
  );
};
