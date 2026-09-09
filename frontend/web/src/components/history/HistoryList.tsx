import type React from 'react';
import { useRef, useCallback, useDeferredValue, useEffect, useId, useMemo, useState } from 'react';
import type { HistoryItem } from '../../types/analysis';
import { Badge, Button, ScrollArea } from '../common';
import { DashboardPanelHeader, DashboardStateBlock } from '../dashboard';
import { HistoryListItem } from './HistoryListItem';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface HistoryListProps {
  items: HistoryItem[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  selectedId?: number;  // 当前选中的历史记录 ID
  selectedIds: Set<number>;
  isDeleting?: boolean;
  onItemClick: (recordId: number) => void;  // 点击记录的回调
  onLoadMore: () => void;
  onToggleItemSelection: (recordId: number) => void;
  onToggleSelectAll: (recordIds: number[]) => void;
  onDeleteSelected: () => void;
  className?: string;
}

/**
 * 历史记录列表组件 (升级版)
 * 使用新设计系统组件实现，支持批量选择和滚动加载
 */
export const HistoryList: React.FC<HistoryListProps> = ({
  items,
  isLoading,
  isLoadingMore,
  hasMore,
  selectedId,
  selectedIds,
  isDeleting = false,
  onItemClick,
  onLoadMore,
  onToggleItemSelection,
  onToggleSelectAll,
  onDeleteSelected,
  className = '',
}) => {
  const { t } = useUiLanguage();
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const loadMoreTriggerRef = useRef<HTMLDivElement>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const selectAllId = useId();
  const searchId = useId();
  const [searchText, setSearchText] = useState('');
  const deferredSearchText = useDeferredValue(searchText);

  const filteredItems = useMemo(() => {
    const q = deferredSearchText.trim().toLowerCase();
    if (!q) return items;

    return items.filter((item) => (
      item.stockCode.toLowerCase().includes(q) ||
      (item.stockName?.toLowerCase().includes(q) ?? false)
    ));
  }, [deferredSearchText, items]);

  const selectedCount = useMemo(
    () => filteredItems.filter((item) => selectedIds.has(item.id)).length,
    [filteredItems, selectedIds],
  );
  const allVisibleSelected = filteredItems.length > 0 && selectedCount === filteredItems.length;
  const someVisibleSelected = selectedCount > 0 && !allVisibleSelected;
  const visibleIds = useMemo(() => filteredItems.map((item) => item.id), [filteredItems]);
  const visibleCountLabel = deferredSearchText.trim()
    ? `${filteredItems.length}/${items.length}`
    : items.length > 99
      ? '99+'
      : items.length.toString();

  // 使用 IntersectionObserver 检测滚动到底部
  const handleObserver = useCallback(
    (entries: IntersectionObserverEntry[]) => {
      const target = entries[0];
      if (target.isIntersecting && hasMore && !isLoading && !isLoadingMore) {
        const container = scrollContainerRef.current;
        if (container && container.scrollHeight > container.clientHeight) {
          onLoadMore();
        }
      }
    },
    [hasMore, isLoading, isLoadingMore, onLoadMore]
  );

  useEffect(() => {
    const trigger = loadMoreTriggerRef.current;
    const container = scrollContainerRef.current;
    if (!trigger || !container) return;

    const observer = new IntersectionObserver(handleObserver, {
      root: container,
      rootMargin: '20px',
      threshold: 0.1,
    });

    observer.observe(trigger);
    return () => observer.disconnect();
  }, [handleObserver]);

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someVisibleSelected;
    }
  }, [someVisibleSelected]);

  return (
    <aside className={`ui-card ui-card-bordered ui-card-padding-none overflow-hidden flex flex-col ${className}`}>
      <ScrollArea
        viewportRef={scrollContainerRef}
        viewportClassName="p-3"
        testId="home-history-list-scroll"
      >
        <div className="sticky top-0 z-10 -mx-3 -mt-3 mb-3 space-y-2.5 border-b border-subtle bg-surface/95 px-3 pb-3 pt-3 backdrop-blur supports-[backdrop-filter]:bg-surface/85">
          <DashboardPanelHeader
            className="mb-0"
            title={t('history.title')}
            titleClassName="text-sm font-medium"
            leading={(
              <svg className="h-4 w-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
            headingClassName="items-center"
            actions={
              selectedCount > 0 ? (
                <Badge variant="info" size="sm" className="animate-in fade-in zoom-in duration-200">
                  {t('history.selected', { count: selectedCount })}
                </Badge>
              ) : items.length > 0 ? (
                <Badge variant="default" size="sm" className="shadow-none">
                  {t('history.count', { count: visibleCountLabel })}
                </Badge>
              ) : undefined
            }
          />

          {items.length > 0 && (
            <div className="space-y-2 rounded-2xl border border-subtle bg-surface-muted/45 p-1.5">
              <div className="relative">
                <svg
                  className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-text"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  id={searchId}
                  type="search"
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  placeholder={t('history.searchPlaceholder')}
                  aria-label={t('history.searchAria')}
                  className="ui-input min-h-8 w-full rounded-xl bg-surface py-1.5 pl-8 pr-3 text-[11px] shadow-none"
                />
              </div>

              <div className="-mx-1.5 flex items-center gap-2">
                <label
                  className="flex min-h-7 flex-1 cursor-pointer items-center gap-2 rounded-lg"
                  htmlFor={selectAllId}
                >
                  <input
                    id={selectAllId}
                    ref={selectAllRef}
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={() => onToggleSelectAll(visibleIds)}
                    disabled={isDeleting}
                  aria-label={t('history.selectAll')}
                    className="ui-checkbox h-3.5 w-3.5 disabled:opacity-50"
                  />
                  <span className="select-none text-[11px] text-muted-text">{t('history.selectAll')}</span>
                </label>
                <Button
                  variant="danger-subtle"
                  size="xsm"
                  onClick={onDeleteSelected}
                  disabled={selectedCount === 0 || isDeleting}
                  isLoading={isDeleting}
                  className="disabled:!border-transparent disabled:!bg-transparent disabled:!text-muted-text"
                >
                  {isDeleting ? t('history.deleting') : t('history.delete')}
                </Button>
              </div>
            </div>
          )}
        </div>

        {isLoading ? (
          <DashboardStateBlock
            loading
            compact
            title={t('history.loading')}
          />
        ) : items.length === 0 ? (
          <DashboardStateBlock
            title={t('history.emptyTitle')}
            description={t('history.emptyDescription')}
            icon={(
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
          />
        ) : filteredItems.length === 0 ? (
          <DashboardStateBlock
            compact
            title={t('history.noMatch')}
            description={t('history.noMatchDescription', { query: searchText.trim() })}
          />
        ) : (
          <div className="space-y-2">
            {filteredItems.map((item) => (
              <HistoryListItem
                key={item.id}
                item={item}
                isViewing={selectedId === item.id}
                isChecked={selectedIds.has(item.id)}
                isDeleting={isDeleting}
                onToggleChecked={onToggleItemSelection}
                onClick={onItemClick}
              />
            ))}

            <div ref={loadMoreTriggerRef} className="h-4" />
            
            {isLoadingMore && (
              <div className="flex justify-center py-4">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
              </div>
            )}

            {!hasMore && items.length > 0 && (
              <div className="text-center py-5">
                <div className="h-px bg-subtle w-full mb-3" />
                <span className="text-[10px] text-secondary-text uppercase tracking-[0.2em]">{t('history.end')}</span>
              </div>
            )}
          </div>
        )}
      </ScrollArea>
    </aside>
  );
};
