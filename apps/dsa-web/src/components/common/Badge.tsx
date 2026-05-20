import React from 'react';
import { cn } from '../../utils/cn';

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'history';

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
  variant?: BadgeVariant;
  size?: 'sm' | 'md';
  glow?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

const variantStyles: Record<BadgeVariant, string> = {
  default: 'border-[hsl(var(--color-border)/0.7)] bg-[hsl(var(--color-surface-raised)/0.75)] text-secondary-text',
  success: 'border-[hsl(var(--color-success)/0.22)] bg-[hsl(var(--color-success)/0.08)] text-success',
  warning: 'border-[hsl(var(--color-warning)/0.24)] bg-[hsl(var(--color-warning)/0.1)] text-warning',
  danger: 'border-[hsl(var(--color-danger)/0.22)] bg-[hsl(var(--color-danger)/0.08)] text-danger',
  info: 'border-[hsl(var(--color-info)/0.24)] bg-[hsl(var(--color-info)/0.08)] text-primary',
  history: 'border-[hsl(var(--chart-2)/0.22)] bg-[hsl(var(--chart-2)/0.08)] text-[hsl(var(--chart-2))]',
};

const glowStyles: Record<BadgeVariant, string> = {
  default: '',
  success: 'shadow-[0_8px_18px_hsl(var(--color-success)/0.14)]',
  warning: 'shadow-[0_8px_18px_hsl(var(--color-warning)/0.14)]',
  danger: 'shadow-[0_8px_18px_hsl(var(--color-danger)/0.14)]',
  info: 'shadow-[0_8px_18px_hsl(var(--color-info)/0.14)]',
  history: 'shadow-[0_8px_18px_hsl(var(--chart-2)/0.14)]',
};

/**
 * Badge component with multiple variants and optional glow styling.
 */
export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'default',
  size = 'sm',
  glow = false,
  className = '',
  style,
  ...rest
}) => {
  const sizeStyles = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm';

  return (
    <span
      {...rest}
      style={style}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border font-medium backdrop-blur-sm',
        sizeStyles,
        variantStyles[variant],
        glow && glowStyles[variant],
        className,
      )}
    >
      {children}
    </span>
  );
};
