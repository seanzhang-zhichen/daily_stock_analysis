import React from 'react';
import { cn } from '../../utils/cn';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'gradient' | 'danger' | 'danger-subtle' | 'settings-primary' | 'settings-secondary';
  size?: 'xsm' | 'sm' | 'md' | 'lg' | 'xl';
  isLoading?: boolean;
  /** Custom loading text. */
  loadingText?: string;
  glow?: boolean;
}

const BUTTON_SIZE_STYLES = {
  xsm: 'ui-button-size-xsm',
  sm: 'ui-button-size-sm',
  md: 'ui-button-size-md',
  lg: 'ui-button-size-lg',
  xl: 'ui-button-size-xl',
} as const;

const BUTTON_VARIANT_STYLES = {
  primary: 'ui-button-primary',
  secondary: 'ui-button-secondary',
  'settings-primary': 'ui-button-primary',
  'settings-secondary': 'ui-button-secondary',
  outline: 'ui-button-outline',
  ghost: 'ui-button-ghost',
  gradient: 'ui-button-primary',
  danger: 'ui-button-danger',
  'danger-subtle': 'ui-button-danger-subtle',
} as const;

/**
 * Button component with multiple variants and terminal-inspired styling.
 */
export const Button: React.FC<ButtonProps> = ({
  children,
  variant = 'primary',
  size = 'md',
  isLoading = false,
  loadingText = '处理中...',
  glow = false,
  className = '',
  disabled,
  type = 'button',
  ...props
}) => {
  const glowStyles = glow ? 'ui-button-emphasis' : '';

  return (
    <button
      type={type}
      aria-busy={isLoading || undefined}
      data-variant={variant}
      className={cn(
        'ui-button',
        BUTTON_SIZE_STYLES[size],
        BUTTON_VARIANT_STYLES[variant],
        glowStyles,
        className,
      )}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading ? (
        <span className="flex items-center justify-center gap-2">
          <svg
            className="h-4 w-4 animate-spin text-current"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
          {loadingText}
        </span>
      ) : (
        children
      )}
    </button>
  );
};
