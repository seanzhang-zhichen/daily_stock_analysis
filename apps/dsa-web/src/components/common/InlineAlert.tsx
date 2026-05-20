import type React from 'react';
import { cn } from '../../utils/cn';

type InlineAlertVariant = 'info' | 'success' | 'warning' | 'danger';

interface InlineAlertProps {
  title?: string;
  message: React.ReactNode;
  variant?: InlineAlertVariant;
  action?: React.ReactNode;
  className?: string;
}

const variantStyles: Record<InlineAlertVariant, string> = {
  info: 'ui-alert-info',
  success: 'ui-alert-success',
  warning: 'ui-alert-warning',
  danger: 'ui-alert-danger',
};

export const InlineAlert: React.FC<InlineAlertProps> = ({
  title,
  message,
  variant = 'info',
  action,
  className = '',
}) => {
  return (
    <div
      role="alert"
      className={cn('ui-alert', variantStyles[variant], className)}
    >
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          {title ? <p className="text-sm font-semibold">{title}</p> : null}
          <div className={cn('text-sm', title ? 'mt-1 opacity-90' : 'opacity-90')}>{message}</div>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
};
