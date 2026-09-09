import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SourceManager } from '../SourceManager';

const { createSource, fetchSource, setSourceEnabled, deleteSource } = vi.hoisted(() => ({
  createSource: vi.fn().mockResolvedValue(undefined),
  fetchSource: vi.fn().mockResolvedValue(undefined),
  setSourceEnabled: vi.fn().mockResolvedValue(undefined),
  deleteSource: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../../api/intelligence', () => ({
  intelligenceApi: { createSource, fetchSource, setSourceEnabled, deleteSource },
}));

const sources = [{
  id: 7,
  name: 'Test RSS',
  source_type: 'rss',
  url: 'https://example.test/feed.xml',
  enabled: true,
  scope_type: 'market',
}];

describe('SourceManager', () => {
  it('submits a trimmed custom source payload', async () => {
    const onRun = vi.fn(async (_key: string, operation: () => Promise<void>) => operation());
    const { container } = render(<SourceManager sources={sources} activeAction={null} onRun={onRun} />);
    const inputs = container.querySelectorAll('input');

    fireEvent.change(inputs[0], { target: { value: '  Custom source  ' } });
    fireEvent.change(inputs[1], { target: { value: ' https://example.test/rss ' } });
    fireEvent.submit(inputs[0].closest('form')!);

    await waitFor(() => expect(createSource).toHaveBeenCalledWith({
      name: 'Custom source',
      url: 'https://example.test/rss',
      source_type: 'rss',
      scope_type: 'market',
    }));
  });

  it('runs refresh, toggle, and delete actions for the selected source', async () => {
    const onRun = vi.fn(async (_key: string, operation: () => Promise<void>) => operation());
    render(<SourceManager sources={sources} activeAction={null} onRun={onRun} />);
    const buttons = screen.getAllByRole('button');

    fireEvent.click(buttons[1]);
    fireEvent.click(buttons[2]);
    fireEvent.click(screen.getByLabelText(/Test RSS/));

    await waitFor(() => {
      expect(fetchSource).toHaveBeenCalledWith(7);
      expect(setSourceEnabled).toHaveBeenCalledWith(7, false);
      expect(deleteSource).toHaveBeenCalledWith(7);
    });
  });
});
