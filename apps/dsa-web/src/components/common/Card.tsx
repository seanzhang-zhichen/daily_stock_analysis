import type React from 'react';
import { cn } from '../../utils/cn';

interface CardProps {
  title?: string;
  subtitle?: string;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  variant?: 'default' | 'bordered' | 'gradient';
  hoverable?: boolean;
  padding?: 'none' | 'sm' | 'md' | 'lg';
}

/**
 * Card component with terminal-inspired variants and optional hover styling.
 */
export const Card: React.FC<CardProps> = ({
  title,
  subtitle,
  children,
  className = '',
  style,
  variant = 'default',
  hoverable = false,
  padding = 'md',
}) => {
  const paddingStyles = {
    none: 'ui-card-padding-none',
    sm: 'ui-card-padding-sm',
    md: 'ui-card-padding-md',
    lg: 'ui-card-padding-lg',
  };

  const variantStyles = {
    default: 'ui-card',
    bordered: 'ui-card ui-card-bordered',
    gradient: 'ui-card ui-card-emphasis',
  };

  const hoverStyles = hoverable ? 'ui-card-hoverable' : '';

  return (
    <div
      style={style}
      className={cn(variantStyles[variant], hoverStyles, paddingStyles[padding], className)}
    >
      {(title || subtitle) && (
        <div className="mb-3">
          {subtitle ? <span className="ui-eyebrow">{subtitle}</span> : null}
          {title ? <h3 className="mt-1 text-lg font-semibold text-foreground">{title}</h3> : null}
        </div>
      )}
      {children}
    </div>
  );
};
