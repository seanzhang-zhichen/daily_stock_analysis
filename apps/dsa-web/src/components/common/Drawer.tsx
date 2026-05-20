import type React from 'react';
import { useEffect, useCallback } from 'react';
import { cn } from '../../utils/cn';

let activeDrawerCount = 0;

interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  width?: string;
  zIndex?: number;
  side?: 'left' | 'right';
  backdropClassName?: string;
}

/**
 * Side drawer component with terminal-inspired styling.
 */
export const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  children,
  width = 'max-w-2xl',
  zIndex = 50,
  side = 'right',
  backdropClassName,
}) => {
  // Close the drawer when Escape is pressed.
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    },
    [onClose]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      activeDrawerCount++;
      if (activeDrawerCount === 1) {
        document.body.style.overflow = 'hidden';
      }

      return () => {
        document.removeEventListener('keydown', handleKeyDown);
        activeDrawerCount--;
        if (activeDrawerCount === 0) {
          document.body.style.overflow = '';
        }
      };
    }
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  const titleId = title ? `drawer-title-${side}` : undefined;
  const sidePositionClass = side === 'left' ? 'left-0 justify-start' : 'right-0 justify-end';

  return (
    <div className="fixed inset-0 overflow-hidden" style={{ zIndex }} role="presentation">
      {/* Backdrop */}
      <div
        className={cn(
          'ui-drawer-backdrop',
          backdropClassName,
        )}
        onClick={onClose}
      />

      <div className={cn('absolute inset-y-0 flex w-full', sidePositionClass, width)}>
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          className={cn(
            'ui-drawer-panel',
            side === 'right' ? 'ui-drawer-panel-right' : 'ui-drawer-panel-left',
            side === 'left' ? 'animate-slide-in-left' : 'animate-slide-in-right'
          )}
        >
          <div className="ui-drawer-header">
            {title ? (
              <div>
                <span className="ui-eyebrow">DETAIL VIEW</span>
                <h2 id={titleId} className="ui-drawer-title">{title}</h2>
              </div>
            ) : <div />}
            <button
              type="button"
              onClick={onClose}
              className="ui-icon-button"
              aria-label="关闭抽屉"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="ui-drawer-body">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
};
