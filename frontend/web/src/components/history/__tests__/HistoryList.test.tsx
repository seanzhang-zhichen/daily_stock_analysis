import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { HistoryList } from '../HistoryList';
import type { HistoryItem } from '../../../types/analysis';

const baseProps = {
  isLoading: false,
  isLoadingMore: false,
  hasMore: false,
  selectedIds: new Set<number>(),
  onItemClick: vi.fn(),
  onLoadMore: vi.fn(),
  onToggleItemSelection: vi.fn(),
  onToggleSelectAll: vi.fn(),
  onDeleteSelected: vi.fn(),
};

const items: HistoryItem[] = [
  {
    id: 1,
    queryId: 'q-1',
    stockCode: '600519',
    stockName: '贵州茅台',
    sentimentScore: 82,
    operationAdvice: '买入',
    createdAt: '2026-03-15T08:00:00Z',
  },
];

const longChineseNameItem: HistoryItem = {
  id: 2,
  queryId: 'q-2',
  stockCode: '600519',
  stockName: '贵州茅台股票股份有限公司',
  sentimentScore: 75,
  operationAdvice: '持有',
  createdAt: '2026-03-16T08:00:00Z',
};

describe('HistoryList', () => {
  it('shows the empty state copy when no history exists', () => {
    const { container } = render(<HistoryList {...baseProps} items={[]} />);

    expect(screen.getByText('暂无历史分析记录')).toBeInTheDocument();
    expect(screen.getByText('完成首次分析后，这里会保留最近结果。')).toBeInTheDocument();
    expect(screen.getByText('历史分析')).toBeInTheDocument();
    expect(container.querySelector('.ui-card')).toBeTruthy();
  });

  it('renders selected count and forwards item interactions', () => {
    const onItemClick = vi.fn();
    const onToggleItemSelection = vi.fn();

    render(
      <HistoryList
        {...baseProps}
        items={items}
        selectedIds={new Set([1])}
        selectedId={1}
        onItemClick={onItemClick}
        onToggleItemSelection={onToggleItemSelection}
      />,
    );

    expect(screen.getByText('已选 1')).toBeInTheDocument();
    expect(screen.getByText('买入 82')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /贵州茅台/i })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: /贵州茅台/i }));
    expect(onItemClick).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    expect(onToggleItemSelection).toHaveBeenCalledWith(1);
  });

  it('toggles select-all when clicking the label text', () => {
    const onToggleSelectAll = vi.fn();

    render(
      <HistoryList
        {...baseProps}
        items={items}
        onToggleSelectAll={onToggleSelectAll}
      />,
    );

    fireEvent.click(screen.getByText('全选当前'));

    expect(onToggleSelectAll).toHaveBeenCalledWith([1]);
  });

  it('toggles select-all only for the filtered history items', () => {
    const onToggleSelectAll = vi.fn();

    render(
      <HistoryList
        {...baseProps}
        items={[
          ...items,
          {
            id: 2,
            queryId: 'q-2',
            stockCode: '300342',
            stockName: '天银机电',
            sentimentScore: 42,
            operationAdvice: '观望',
            createdAt: '2026-03-15T09:00:00Z',
          },
        ]}
        onToggleSelectAll={onToggleSelectAll}
      />,
    );

    fireEvent.change(screen.getByRole('searchbox', { name: '按股票代码或名称筛选历史记录' }), {
      target: { value: '天银' },
    });
    fireEvent.click(screen.getByText('全选当前'));

    expect(onToggleSelectAll).toHaveBeenCalledWith([2]);
  });

  it('disables delete when nothing is selected', () => {
    render(<HistoryList {...baseProps} items={items} />);

    expect(screen.getByRole('button', { name: '删除' })).toBeDisabled();
  });

  it('truncates long stock names with trailing dot', () => {
    render(
      <HistoryList
        {...baseProps}
        items={[longChineseNameItem]}
      />,
    );

    // '贵州茅台股票股份有限公司' (12 Chinese chars) should be truncated to '贵州茅台股票股份.' (8 chars + dot)
    // The full name exists in a hidden span, visible on hover
    expect(screen.getByText('贵州茅台股票股份.')).toBeInTheDocument();
    const fullNameHidden = screen.queryByText('贵州茅台股票股份有限公司');
    expect(fullNameHidden).toBeInTheDocument();
    expect(fullNameHidden).toHaveClass('hidden');
  });

  it('generates unique select-all ids across multiple instances', () => {
    const { container } = render(
      <>
        <HistoryList {...baseProps} items={items} />
        <HistoryList {...baseProps} items={items} />
      </>,
    );

    const labels = container.querySelectorAll('label[for]');
    const ids = Array.from(labels).map((label) => label.getAttribute('for'));

    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
