import type React from 'react';
import { useId } from 'react';
import { cn } from '../../utils/cn';

interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: string;
  containerClassName?: string;
}

/**
 * 定制化的大尺寸勾选框组件
 */
export const Checkbox: React.FC<CheckboxProps> = ({
  label,
  id,
  className = '',
  containerClassName = '',
  ...props
}) => {
  const generatedId = useId();
  const checkboxId = id ?? generatedId;

  return (
    <div className={cn('flex items-center gap-3', containerClassName)}>
      <input
        id={checkboxId}
        type="checkbox"
        className={cn(
          'ui-checkbox transition-all',
          'disabled:cursor-not-allowed disabled:opacity-50',
          className
        )}
        {...props}
      />
      {label && (
        <label
          htmlFor={checkboxId}
          className="cursor-pointer select-none text-sm font-medium text-foreground"
        >
          {label}
        </label>
      )}
    </div>
  );
};
