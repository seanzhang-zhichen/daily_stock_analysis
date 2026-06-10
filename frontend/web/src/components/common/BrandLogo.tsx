import type React from 'react';
import { APP_NAME, APP_TAGLINE } from '../../utils/brand';
import { cn } from '../../utils/cn';

type BrandLogoProps = {
  className?: string;
  markClassName?: string;
  textClassName?: string;
  taglineClassName?: string;
  showText?: boolean;
  size?: 'sm' | 'md' | 'lg';
};

const sizeClassNames = {
  sm: 'h-9 w-9',
  md: 'h-10 w-10',
  lg: 'h-14 w-14',
};

export const BrandLogo: React.FC<BrandLogoProps> = ({
  className,
  markClassName,
  textClassName,
  taglineClassName,
  showText = false,
  size = 'md',
}) => (
  <span className={cn('inline-flex items-center gap-3', className)}>
    <span
      className={cn(
        'relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-xl bg-primary-gradient text-white shadow-[0_8px_24px_hsl(var(--primary)/0.35)]',
        sizeClassNames[size],
        markClassName
      )}
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 40 40"
        className="h-[72%] w-[72%]"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M9 27.5H31"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          opacity="0.55"
        />
        <path
          d="M10 25.5L16.2 19.3L21.1 22.9L30 12"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="30" cy="12" r="3" fill="currentColor" />
      </svg>
    </span>
    {showText ? (
      <span className="min-w-0">
        <span className={cn('block truncate text-lg font-bold text-current', textClassName)}>
          {APP_NAME}
        </span>
        <span className={cn('block truncate text-[10px] font-medium text-current/60', taglineClassName)}>
          {APP_TAGLINE}
        </span>
      </span>
    ) : null}
  </span>
);
