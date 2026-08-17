import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Drawer } from '../Drawer';

describe('Drawer', () => {
  it('renders at the document root so shell stacking contexts cannot cover it', () => {
    const onClose = vi.fn();

    render(
      <div data-testid="stacking-context">
        <Drawer isOpen onClose={onClose} title="Details">
          Drawer content
        </Drawer>
      </div>,
    );

    const presentation = screen.getByRole('dialog').closest('[role="presentation"]');
    expect(presentation?.parentElement).toBe(document.body);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
