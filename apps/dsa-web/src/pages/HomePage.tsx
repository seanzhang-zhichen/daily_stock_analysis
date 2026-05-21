import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BarChart3, Check, SlidersHorizontal } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { analysisApi } from '../api/analysis';
import { agentApi, type SkillInfo } from '../api/agent';
import { systemConfigApi } from '../api/systemConfig';
import { ApiErrorAlert, ConfirmDialog, Button, EmptyState, InlineAlert } from '../components/common';
import { DashboardStateBlock } from '../components/dashboard';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { HistoryList } from '../components/history';
import { ReportMarkdown, ReportSummary } from '../components/report';
import { TaskPanel } from '../components/tasks';
import { useAuth, useDashboardLifecycle, useHomeDashboardState } from '../hooks';
import { useStockIndex } from '../hooks/useStockIndex';
import type { SetupStatusResponse } from '../types/systemConfig';
import { formatDateTime, formatReportType } from '../utils/format';
import { getReportText, normalizeReportLanguage } from '../utils/reportLanguage';
import { searchStocks } from '../utils/searchStocks';

const EMPTY_QUICK_STOCKS = [
  { code: '600519', name: '贵州茅台', hint: 'A 股龙头' },
  { code: 'AAPL', name: 'Apple', hint: '美股科技' },
  { code: 'hk00700', name: '腾讯控股', hint: '港股互联网' },
] as const;

type MarketReviewNotice = {
  variant: 'success' | 'warning' | 'danger';
  title: string;
  message: string;
} | null;

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const { userMode } = useAuth();
  const canReadSetupStatus = !(userMode?.userModeEnabled) || Boolean(userMode?.user?.isAdmin);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isSubmittingMarketReview, setIsSubmittingMarketReview] = useState(false);
  const [marketReviewNotice, setMarketReviewNotice] = useState<MarketReviewNotice>(null);
  const [marketReviewError, setMarketReviewError] = useState<ParsedApiError | null>(null);
  const [marketReviewReport, setMarketReviewReport] = useState<string | null>(null);
  const [marketReviewReportCopied, setMarketReviewReportCopied] = useState(false);
  const [analysisSkills, setAnalysisSkills] = useState<SkillInfo[]>([]);
  const [defaultStrategyId, setDefaultStrategyId] = useState('');
  const [selectedStrategyId, setSelectedStrategyId] = useState('');
  const [strategyMenuOpen, setStrategyMenuOpen] = useState(false);
  const marketReviewPollTimer = useRef<number | null>(null);
  const dashboardScrollRef = useRef<HTMLElement | null>(null);
  const strategyMenuRef = useRef<HTMLDivElement | null>(null);
  const strategyButtonRef = useRef<HTMLButtonElement | null>(null);
  const strategyItemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const strategyInitialFocusIndexRef = useRef<number | null>(null);

  const stopMarketReviewPolling = useCallback(() => {
    if (marketReviewPollTimer.current !== null) {
      window.clearInterval(marketReviewPollTimer.current);
      marketReviewPollTimer.current = null;
    }
  }, []);

  const scrollMarketReviewFeedbackIntoView = useCallback(() => {
    const scrollContainer = dashboardScrollRef.current;
    if (!scrollContainer) {
      return;
    }

    if (typeof scrollContainer.scrollTo === 'function') {
      scrollContainer.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    scrollContainer.scrollTop = 0;
  }, []);

  useEffect(() => stopMarketReviewPolling, [stopMarketReviewPolling]);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(null);

  const {
    query,
    inputError,
    duplicateError,
    error,
    isAnalyzing,
    historyItems,
    selectedHistoryIds,
    isDeletingHistory,
    isLoadingHistory,
    isLoadingMore,
    hasMore,
    selectedReport,
    isLoadingReport,
    activeTasks,
    markdownDrawerOpen,
    setQuery,
    clearError,
    loadInitialHistory,
    refreshHistory,
    loadMoreHistory,
    selectHistoryItem,
    toggleHistorySelection,
    toggleSelectAllVisible,
    deleteSelectedHistory,
    submitAnalysis,
    notify,
    setNotify,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    removeTask,
    openMarkdownDrawer,
    closeMarkdownDrawer,
    selectedIds,
  } = useHomeDashboardState();
  const { index: stockIndex } = useStockIndex();

  useEffect(() => {
    document.title = '每日选股分析 - DSA';
  }, []);

  useEffect(() => {
    if (!canReadSetupStatus) {
      setSetupStatus(null);
      return;
    }

    let active = true;
    systemConfigApi.getSetupStatus()
      .then((status) => {
        if (active) {
          setSetupStatus(status);
        }
      })
      .catch(() => {
        if (active) {
          setSetupStatus(null);
        }
      });

    return () => {
      active = false;
    };
  }, [canReadSetupStatus]);

  useEffect(() => {
    let active = true;
    agentApi.getSkills()
      .then((response) => {
        if (active) {
          const nextDefaultStrategyId = response.skills.some((skill) => skill.id === response.default_skill_id)
            ? response.default_skill_id
            : response.skills[0]?.id || '';
          setAnalysisSkills(response.skills);
          setDefaultStrategyId(nextDefaultStrategyId);
          setSelectedStrategyId((current) => (
            current && response.skills.some((skill) => skill.id === current)
              ? current
              : nextDefaultStrategyId
          ));
        }
      })
      .catch(() => {
        if (active) {
          setAnalysisSkills([]);
          setDefaultStrategyId('');
          setSelectedStrategyId('');
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!strategyMenuOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (target instanceof Node && strategyMenuRef.current?.contains(target)) {
        return;
      }
      setStrategyMenuOpen(false);
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [strategyMenuOpen]);

  useEffect(() => {
    const fallbackStrategyId = analysisSkills.some((skill) => skill.id === defaultStrategyId)
      ? defaultStrategyId
      : analysisSkills[0]?.id || '';

    if (!selectedStrategyId) {
      if (fallbackStrategyId) {
        setSelectedStrategyId(fallbackStrategyId);
      }
      return;
    }

    if (!analysisSkills.some((skill) => skill.id === selectedStrategyId)) {
      setSelectedStrategyId(fallbackStrategyId);
    }
  }, [analysisSkills, defaultStrategyId, selectedStrategyId]);

  const reportLanguage = normalizeReportLanguage(selectedReport?.meta.reportLanguage);
  const reportText = getReportText(reportLanguage);
  const isMarketReviewHistoryReport = selectedReport?.meta.reportType === 'market_review';
  const selectedStrategy = useMemo(
    () => analysisSkills.find((skill) => skill.id === selectedStrategyId),
    [analysisSkills, selectedStrategyId],
  );
  const selectedAnalysisSkills = useMemo(
    () => (selectedStrategyId ? [selectedStrategyId] : undefined),
    [selectedStrategyId],
  );
  const strategyOptions = useMemo(
    () => analysisSkills.map((skill) => ({
        id: skill.id,
        name: skill.name,
        description: skill.description,
      })),
    [analysisSkills],
  );
  const closeStrategyMenu = useCallback((restoreFocus = false) => {
    setStrategyMenuOpen(false);
    if (restoreFocus) {
      strategyButtonRef.current?.focus();
    }
  }, []);
  const selectStrategy = useCallback((strategyId: string) => {
    setSelectedStrategyId(strategyId);
    setStrategyMenuOpen(false);
  }, []);
  const focusStrategyItem = useCallback((index: number) => {
    const itemCount = strategyOptions.length;
    if (itemCount === 0) {
      return;
    }
    const nextIndex = (index + itemCount) % itemCount;
    strategyItemRefs.current[nextIndex]?.focus();
  }, [strategyOptions.length]);
  const getSelectedStrategyIndex = useCallback(() => {
    const selectedIndex = strategyOptions.findIndex((option) => option.id === selectedStrategyId);
    return selectedIndex >= 0 ? selectedIndex : 0;
  }, [selectedStrategyId, strategyOptions]);
  useEffect(() => {
    strategyItemRefs.current = strategyItemRefs.current.slice(0, strategyOptions.length);
  }, [strategyOptions.length]);
  useEffect(() => {
    if (!strategyMenuOpen) {
      return undefined;
    }

    const targetIndex = strategyInitialFocusIndexRef.current ?? getSelectedStrategyIndex();
    strategyInitialFocusIndexRef.current = null;
    const timeout = window.setTimeout(() => focusStrategyItem(targetIndex), 0);
    return () => window.clearTimeout(timeout);
  }, [focusStrategyItem, getSelectedStrategyIndex, strategyMenuOpen]);
  const handleStrategyButtonKeyDown = useCallback((event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') {
      return;
    }

    event.preventDefault();
    const targetIndex = event.key === 'ArrowUp' ? strategyOptions.length - 1 : 0;
    if (strategyMenuOpen) {
      focusStrategyItem(targetIndex);
      return;
    }
    strategyInitialFocusIndexRef.current = targetIndex;
    setStrategyMenuOpen(true);
  }, [focusStrategyItem, strategyMenuOpen, strategyOptions.length]);
  const handleStrategyMenuKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const itemCount = strategyOptions.length;
    if (itemCount === 0) {
      return;
    }

    const currentIndex = strategyItemRefs.current.findIndex((item) => item === document.activeElement);
    switch (event.key) {
      case 'Escape':
        event.preventDefault();
        closeStrategyMenu(true);
        break;
      case 'ArrowDown':
        event.preventDefault();
        focusStrategyItem(currentIndex >= 0 ? currentIndex + 1 : 0);
        break;
      case 'ArrowUp':
        event.preventDefault();
        focusStrategyItem(currentIndex >= 0 ? currentIndex - 1 : itemCount - 1);
        break;
      case 'Home':
        event.preventDefault();
        focusStrategyItem(0);
        break;
      case 'End':
        event.preventDefault();
        focusStrategyItem(itemCount - 1);
        break;
      case 'Tab':
        setStrategyMenuOpen(false);
        break;
      default:
        break;
    }
  }, [closeStrategyMenu, focusStrategyItem, strategyOptions.length]);
  const resolveExactStockInput = useCallback((value: string) => {
    const trimmedValue = value.trim();
    if (!trimmedValue || stockIndex.length === 0) {
      return null;
    }

    try {
      const exactMatches = searchStocks(trimmedValue, stockIndex, { limit: 20 })
        .filter((suggestion) => suggestion.matchType === 'exact');
      return exactMatches.length === 1 ? exactMatches[0] : null;
    } catch (err) {
      console.error('Failed to resolve stock input from index.', err);
      return null;
    }
  }, [stockIndex]);
  const setupNeedsAction = setupStatus ? !setupStatus.isComplete : false;
  const setupMissingLabels = useMemo(() => {
    if (!setupStatus) {
      return '';
    }
    const requiredNeedsAction = setupStatus.checks
      .filter((check) => check.required && check.status === 'needs_action')
      .map((check) => check.title);
    return requiredNeedsAction.slice(0, 3).join('、');
  }, [setupStatus]);

  useDashboardLifecycle({
    loadInitialHistory,
    refreshHistory,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    removeTask,
  });

  const handleHistoryItemClick = useCallback((recordId: number) => {
    void selectHistoryItem(recordId);
    setSidebarOpen(false);
  }, [selectHistoryItem]);

  const handleSubmitAnalysis = useCallback(
    (
      stockCode?: string,
      stockName?: string,
      selectionSource?: 'manual' | 'autocomplete' | 'import' | 'image',
    ) => {
      const resolvedStock = stockCode ? null : resolveExactStockInput(query);
      void submitAnalysis({
        stockCode: stockCode ?? resolvedStock?.canonicalCode,
        stockName: stockName ?? resolvedStock?.nameZh,
        originalQuery: query,
        selectionSource: selectionSource ?? 'manual',
        skills: selectedAnalysisSkills,
      });
    },
    [query, resolveExactStockInput, selectedAnalysisSkills, submitAnalysis],
  );

  const handleAskFollowUp = useCallback(() => {
    if (selectedReport?.meta.id === undefined || selectedReport.meta.reportType === 'market_review') {
      return;
    }

    const code = selectedReport.meta.stockCode;
    const name = selectedReport.meta.stockName;
    const rid = selectedReport.meta.id;
    navigate(`/chat?stock=${encodeURIComponent(code)}&name=${encodeURIComponent(name)}&recordId=${rid}`);
  }, [navigate, selectedReport]);

  const handleReanalyze = useCallback(() => {
    if (!selectedReport || selectedReport.meta.reportType === 'market_review') {
      return;
    }

    void submitAnalysis({
      stockCode: selectedReport.meta.stockCode,
      stockName: selectedReport.meta.stockName,
      originalQuery: selectedReport.meta.stockCode,
      selectionSource: 'manual',
      forceRefresh: true,
      skills: selectedAnalysisSkills,
    });
  }, [selectedAnalysisSkills, selectedReport, submitAnalysis]);

  const pollMarketReviewStatus = useCallback(
    async (taskId: string) => {
      stopMarketReviewPolling();

      const maxAttempts = 120;
      const intervalMs = 2000;
      let attempts = 0;

      const poll = async (): Promise<boolean> => {
        if (attempts >= maxAttempts) {
          stopMarketReviewPolling();
          setMarketReviewReport(null);
          setMarketReviewNotice({
            variant: 'danger',
            title: '大盘复盘已超时',
            message: '任务长时间未返回最终结果，请在任务列表/历史中查看。',
          });
          scrollMarketReviewFeedbackIntoView();
          return false;
        }

        attempts += 1;

        try {
          const status = await analysisApi.getStatus(taskId);
          if (status.status === 'pending' || status.status === 'processing') {
            setMarketReviewReport(null);
            const progress = typeof status.progress === 'number'
              ? `${status.progress}%`
              : '进行中';
            setMarketReviewNotice({
              variant: 'warning',
              title: '大盘复盘进行中',
              message: `任务状态：${status.status}（${progress}）`,
            });
            return true;
          }

          if (status.status === 'completed') {
            stopMarketReviewPolling();
            const marketReviewText = typeof status.marketReviewReport === 'string'
              ? status.marketReviewReport
              : '';
            setMarketReviewReport(marketReviewText ? marketReviewText.trim() : null);
            setMarketReviewNotice({
              variant: 'success',
              title: '大盘复盘已完成',
              message: marketReviewText ? '大盘复盘任务已完成，结果如下：' : '大盘复盘任务已完成，结果已生成并按配置推送。',
            });
            setMarketReviewError(null);
            scrollMarketReviewFeedbackIntoView();
            return false;
          }

          if (status.status === 'failed') {
            stopMarketReviewPolling();
            setMarketReviewReport(null);
            setMarketReviewError(
              getParsedApiError({
                response: {
                  status: 500,
                  data: {
                    error: 'market_review_failed',
                    message: status.error || '大盘复盘执行失败。',
                  },
                },
              }),
            );
            setMarketReviewNotice(null);
            scrollMarketReviewFeedbackIntoView();
            return false;
          }

          stopMarketReviewPolling();
          setMarketReviewReport(null);
          setMarketReviewNotice({
            variant: 'danger',
            title: '大盘复盘状态异常',
            message: `收到未知任务状态：${status.status}`,
          });
          scrollMarketReviewFeedbackIntoView();
          return false;
        } catch (err: unknown) {
          const parsed = getParsedApiError(err);
          if (attempts >= maxAttempts) {
            stopMarketReviewPolling();
            setMarketReviewReport(null);
            setMarketReviewError(parsed);
            setMarketReviewNotice(null);
            scrollMarketReviewFeedbackIntoView();
            return false;
          }
          return true;
        }

        return true;
      };

      if (await poll()) {
        marketReviewPollTimer.current = window.setInterval(() => {
          void poll().then((shouldContinue) => {
            if (!shouldContinue) {
              stopMarketReviewPolling();
            }
          });
        }, intervalMs);
      }
    },
    [scrollMarketReviewFeedbackIntoView, stopMarketReviewPolling],
  );

  const handleTriggerMarketReview = useCallback(async () => {
    setIsSubmittingMarketReview(true);
    setMarketReviewNotice(null);
    setMarketReviewError(null);
    setMarketReviewReport(null);
    scrollMarketReviewFeedbackIntoView();
    try {
      const result = await analysisApi.triggerMarketReview({ sendNotification: notify });
      setMarketReviewNotice({
        variant: 'success',
        title: '大盘复盘已提交',
        message: result.message,
      });
      scrollMarketReviewFeedbackIntoView();

      if (result.taskId) {
        await pollMarketReviewStatus(result.taskId);
      }
    } catch (err: unknown) {
      setMarketReviewError(getParsedApiError(err));
      setMarketReviewNotice(null);
      scrollMarketReviewFeedbackIntoView();
    } finally {
      setIsSubmittingMarketReview(false);
    }
  }, [notify, pollMarketReviewStatus, scrollMarketReviewFeedbackIntoView]);

  const handleCopyMarketReviewReport = useCallback(() => {
    if (!marketReviewReport) {
      return;
    }

    void navigator.clipboard.writeText(marketReviewReport).then(
      () => {
        setMarketReviewReportCopied(true);
        setTimeout(() => setMarketReviewReportCopied(false), 2000);
      },
      (err) => {
        console.error('复制失败:', err);
      },
    );
  }, [marketReviewReport]);

  const handleDeleteSelectedHistory = useCallback(() => {
    void deleteSelectedHistory();
    setShowDeleteConfirm(false);
  }, [deleteSelectedHistory]);

  const handleQuickStockSelect = useCallback((stockCode: string) => {
    setQuery(stockCode);
  }, [setQuery]);

  const sidebarContent = useMemo(
    () => (
      <div className="flex min-h-0 h-full flex-col gap-3 overflow-hidden">
        <TaskPanel tasks={activeTasks} />
        <HistoryList
          items={historyItems}
          isLoading={isLoadingHistory}
          isLoadingMore={isLoadingMore}
          hasMore={hasMore}
          selectedId={selectedReport?.meta.id}
          selectedIds={selectedIds}
          isDeleting={isDeletingHistory}
          onItemClick={handleHistoryItemClick}
          onLoadMore={() => void loadMoreHistory()}
          onToggleItemSelection={toggleHistorySelection}
          onToggleSelectAll={toggleSelectAllVisible}
          onDeleteSelected={() => setShowDeleteConfirm(true)}
          className="flex-1 overflow-hidden"
        />
      </div>
    ),
    [
      activeTasks,
      hasMore,
      historyItems,
      isDeletingHistory,
      isLoadingHistory,
      isLoadingMore,
      handleHistoryItemClick,
      loadMoreHistory,
      selectedIds,
      selectedReport?.meta.id,
      toggleHistorySelection,
      toggleSelectAllVisible,
    ],
  );

  return (
    <div
      data-testid="home-dashboard"
      className="flex h-[calc(100vh-5rem)] w-full flex-col overflow-hidden md:flex-row sm:h-[calc(100vh-5.5rem)] lg:h-[calc(100vh-2rem)]"
    >
      <div className="workspace-page-layout flex-1 flex flex-col min-h-0 min-w-0 !max-w-7xl !p-0 mx-auto w-full">
        <header className="relative z-30 flex min-w-0 flex-shrink-0 items-center overflow-visible px-3 py-3 md:px-4 md:py-4">
          <div className="ui-card ui-card-bordered ui-card-padding-sm !overflow-visible flex min-w-0 flex-1 flex-col gap-2.5 md:flex-row md:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-2.5">
              <button
                onClick={() => setSidebarOpen(true)}
                className="md:hidden -ml-1 flex-shrink-0 rounded-lg p-1.5 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
                aria-label="历史记录"
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
              <div className="relative min-w-0 flex-1">
                <StockAutocomplete
                  value={query}
                  onChange={setQuery}
                  onSubmit={(stockCode, stockName, selectionSource) => {
                    handleSubmitAnalysis(stockCode, stockName, selectionSource);
                  }}
                  placeholder="输入股票代码或名称，如 600519、贵州茅台、AAPL"
                  disabled={isAnalyzing}
                  className={inputError ? 'border-danger/50' : undefined}
                />
              </div>
              {analysisSkills.length > 0 ? (
                <div ref={strategyMenuRef} className="relative flex-shrink-0">
                  <button
                    ref={strategyButtonRef}
                    id="strategy-menu-button"
                    type="button"
                    aria-haspopup="menu"
                    aria-expanded={strategyMenuOpen}
                    aria-controls={strategyMenuOpen ? 'strategy-menu' : undefined}
                    onClick={() => setStrategyMenuOpen((open) => !open)}
                    onKeyDown={handleStrategyButtonKeyDown}
                    disabled={isAnalyzing}
                    className="ui-button ui-button-secondary flex h-10 max-w-[8.5rem] items-center gap-1.5 rounded-xl px-3 text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-60 sm:max-w-[11rem]"
                  >
                    <SlidersHorizontal className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
                    <span className="truncate">{selectedStrategy?.name || '选择策略'}</span>
                  </button>
                  {strategyMenuOpen ? (
                    <div
                      id="strategy-menu"
                      role="menu"
                      aria-labelledby="strategy-menu-button"
                      onKeyDown={handleStrategyMenuKeyDown}
                      className="ui-menu ui-menu-scrollable absolute right-0 top-11 z-[120] max-h-80 w-[min(18rem,calc(100vw-1.5rem))] text-sm text-foreground"
                    >
                      {strategyOptions.map((option, index) => {
                        const selected = selectedStrategyId === option.id;
                        return (
                          <button
                            key={option.id}
                            ref={(node) => {
                              strategyItemRefs.current[index] = node;
                            }}
                            type="button"
                            role="menuitemradio"
                            aria-checked={selected}
                            tabIndex={-1}
                            onClick={() => selectStrategy(option.id)}
                            className={`ui-menu-item ui-menu-item-radio text-left ${selected ? 'ui-menu-item-active' : ''}`}
                          >
                            <Check className={`ui-menu-item-check-slot ${selected ? 'opacity-100' : 'opacity-0'}`} aria-hidden="true" />
                            <span className="ui-menu-item-content">
                              <span className="block font-medium">{option.name}</span>
                              <span className="mt-0.5 line-clamp-2 block text-xs leading-5 text-muted-text">{option.description}</span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div className="flex min-w-0 flex-shrink-0 items-center gap-2.5">
              <label className="flex h-10 flex-shrink-0 cursor-pointer items-center gap-1.5 rounded-xl border border-subtle bg-surface/60 px-3 text-xs text-secondary-text select-none transition-colors hover:border-subtle-hover hover:text-foreground">
                <input
                  type="checkbox"
                  checked={notify}
                  onChange={(e) => setNotify(e.target.checked)}
                  className="ui-checkbox h-3.5 w-3.5"
                />
                推送通知
              </label>
              <Button
                type="button"
                variant="secondary"
                size="md"
                isLoading={isSubmittingMarketReview}
                loadingText="提交中"
                onClick={() => void handleTriggerMarketReview()}
                className="h-10 flex-1 whitespace-nowrap md:flex-none"
              >
                <BarChart3 className="h-4 w-4" aria-hidden="true" />
                大盘复盘
              </Button>
              <Button
                type="button"
                onClick={() => handleSubmitAnalysis()}
                disabled={!query || isAnalyzing}
                isLoading={isAnalyzing}
                loadingText="提交中"
                className="h-10 flex-1 whitespace-nowrap shadow-lg shadow-primary/20 md:flex-none"
              >
                分析
              </Button>
            </div>
          </div>
        </header>

        {inputError || duplicateError ? (
          <div className="px-3 pb-2 md:px-4">
            {inputError ? (
              <InlineAlert
                variant="danger"
                title="输入有误"
                message={inputError}
                className="rounded-xl px-3 py-2 text-xs shadow-none"
              />
            ) : null}
            {!inputError && duplicateError ? (
              <InlineAlert
                variant="warning"
                title="任务已存在"
                message={duplicateError}
                className="rounded-xl px-3 py-2 text-xs shadow-none"
              />
            ) : null}
          </div>
        ) : null}

        {setupNeedsAction ? (
          <div className="px-3 pb-2 md:px-4">
            <InlineAlert
              variant="warning"
              title="基础配置未完成"
              message={
                setupMissingLabels
                  ? `还缺少 ${setupMissingLabels}，完成后即可开始最小可用分析。`
                  : '还缺少基础配置，完成后即可开始最小可用分析。'
              }
              action={(
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => navigate('/settings')}
                >
                  去配置
                </Button>
              )}
              className="rounded-xl px-3 py-2 text-xs shadow-none"
            />
          </div>
        ) : null}

        <div className="flex-1 flex min-h-0 overflow-hidden">
          <div className="hidden min-h-0 w-64 shrink-0 flex-col overflow-hidden pl-4 pb-4 md:flex lg:w-72">
            {sidebarContent}
          </div>

          {sidebarOpen ? (
            <div className="fixed inset-0 z-40 md:hidden" onClick={() => setSidebarOpen(false)}>
              <div className="ui-drawer-backdrop absolute inset-0" />
              <div
                className="ui-drawer-panel ui-drawer-panel-left absolute bottom-0 left-0 top-0 flex w-72 flex-col overflow-hidden p-3"
                onClick={(event) => event.stopPropagation()}
              >
                {sidebarContent}
              </div>
            </div>
          ) : null}

          <section
            ref={dashboardScrollRef}
            data-testid="home-dashboard-scroll"
            className="flex-1 min-w-0 min-h-0 overflow-x-auto overflow-y-auto px-3 pb-4 md:px-6 touch-pan-y"
          >
            {marketReviewNotice ? (
              <div className="mb-3">
                <InlineAlert
                  variant={marketReviewNotice.variant}
                  title={marketReviewNotice.title}
                  message={marketReviewNotice.message}
                  className="rounded-xl px-3 py-2 text-xs shadow-none"
                />
              </div>
            ) : null}

            {marketReviewError ? (
              <div className="mb-3">
                <ApiErrorAlert
                  error={marketReviewError}
                  className="mb-1"
                  onDismiss={() => setMarketReviewError(null)}
                />
              </div>
            ) : null}

            {marketReviewReport ? (
              <div className="mb-3 rounded-xl border border-subtle bg-surface/70 px-3 py-3 text-xs text-secondary-text shadow-sm">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="font-semibold text-foreground">大盘复盘报告</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="xsm"
                    className="h-7"
                    disabled={marketReviewReportCopied}
                    onClick={() => void handleCopyMarketReviewReport()}
                  >
                    {marketReviewReportCopied ? '已复制' : '复制'}
                  </Button>
                </div>
                <pre
                  data-testid="market-review-report"
                  className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-background px-3 py-2 leading-relaxed"
                >
                  {marketReviewReport}
                </pre>
              </div>
            ) : null}

            {error ? (
              <ApiErrorAlert
                error={error}
                className="mb-3"
                onDismiss={clearError}
              />
            ) : null}
            {isLoadingReport ? (
              <div className="flex h-full flex-col items-center justify-center">
                <DashboardStateBlock title="加载报告中..." loading />
              </div>
            ) : selectedReport ? (
              <div className="max-w-4xl space-y-4 pb-8">
                <div
                  data-testid="home-report-toolbar"
                  className="ui-card ui-card-bordered ui-card-padding-sm flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="ui-eyebrow">Analysis Report</p>
                    <h2 className="mt-1 truncate text-lg font-semibold tracking-tight text-foreground">
                      {selectedReport.meta.stockName || selectedReport.meta.stockCode}
                    </h2>
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-text">
                      <span className="font-mono">{selectedReport.meta.stockCode}</span>
                      <span>{formatReportType(selectedReport.meta.reportType)}</span>
                      <span>{formatDateTime(selectedReport.meta.createdAt)}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={isAnalyzing || selectedReport.meta.id === undefined || isMarketReviewHistoryReport}
                      onClick={handleReanalyze}
                    >
                      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                      {reportText.reanalyze}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={selectedReport.meta.id === undefined || isMarketReviewHistoryReport}
                      onClick={handleAskFollowUp}
                    >
                      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                      </svg>
                      追问 AI
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={selectedReport.meta.id === undefined}
                      onClick={openMarkdownDrawer}
                    >
                      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      {reportText.fullReport}
                    </Button>
                  </div>
                </div>
                <ReportSummary data={selectedReport} isHistory />
              </div>
            ) : (
              <div className="flex h-full items-center justify-center py-8">
                <EmptyState
                  title="从一只股票开始分析"
                  description="输入股票代码或名称即可生成报告；也可以先选择示例股票，再按顶部「分析」。历史报告会沉淀在左侧辅助栏。"
                  className="max-w-2xl border-dashed"
                  icon={(
                    <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                    </svg>
                  )}
                  action={(
                    <div className="w-full space-y-4">
                      <div className="grid gap-2 sm:grid-cols-3">
                        {EMPTY_QUICK_STOCKS.map((stock) => (
                          <button
                            key={stock.code}
                            type="button"
                            disabled={isAnalyzing}
                            onClick={() => handleQuickStockSelect(stock.code)}
                            className="rounded-xl border border-subtle bg-surface/80 px-3 py-2.5 text-left transition-colors hover:border-primary/30 hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            <span className="block text-sm font-semibold text-foreground">{stock.name}</span>
                            <span className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-text">
                              <span className="font-mono">{stock.code}</span>
                              <span>{stock.hint}</span>
                            </span>
                          </button>
                        ))}
                      </div>
                      <div className="flex flex-wrap items-center justify-center gap-2">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          isLoading={isSubmittingMarketReview}
                          loadingText="提交中"
                          onClick={() => void handleTriggerMarketReview()}
                        >
                          <BarChart3 className="h-4 w-4" aria-hidden="true" />
                          先看大盘复盘
                        </Button>
                      </div>
                    </div>
                  )}
                />
              </div>
            )}
          </section>
        </div>
      </div>

      {markdownDrawerOpen && selectedReport?.meta.id ? (
        <ReportMarkdown
          recordId={selectedReport.meta.id}
          stockName={selectedReport.meta.stockName || ''}
          stockCode={selectedReport.meta.stockCode}
          reportLanguage={reportLanguage}
          onClose={closeMarkdownDrawer}
        />
      ) : null}

      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title="删除历史记录"
        message={
          selectedHistoryIds.length === 1
            ? '确认删除这条历史记录吗？删除后将不可恢复。'
            : `确认删除选中的 ${selectedHistoryIds.length} 条历史记录吗？删除后将不可恢复。`
        }
        confirmText={isDeletingHistory ? '删除中...' : '确认删除'}
        cancelText="取消"
        isDanger={true}
        onConfirm={handleDeleteSelectedHistory}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
};

export default HomePage;
