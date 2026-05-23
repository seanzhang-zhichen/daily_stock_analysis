import type React from 'react';
import { createPortal } from 'react-dom';
import { Button } from './Button';

interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  isDanger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Generic confirmation dialog component.
 * Style is consistent with ChatPage.
 */
export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  isOpen,
  title,
  message,
  confirmText = '确定',
  cancelText = '取消',
  isDanger = false,
  onConfirm,
  onCancel,
}) => {
  if (!isOpen) return null;

  const dialog = (
    <div
      className="ui-dialog-backdrop"
      onClick={onCancel}
    >
      <div
        className="ui-dialog-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="ui-dialog-title">{title}</h3>
        <p className="ui-dialog-message">
          {message}
        </p>
        <div className="ui-dialog-actions">
          <Button type="button" variant="outline" size="sm" onClick={onCancel}>
            {cancelText}
          </Button>
          <Button
            type="button"
            variant={isDanger ? 'danger' : 'primary'}
            size="sm"
            onClick={onConfirm}
          >
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
};
