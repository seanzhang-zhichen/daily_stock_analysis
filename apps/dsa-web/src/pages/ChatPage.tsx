import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '../utils/cn';
import { agentApi } from '../api/agent';
import { ApiErrorAlert, Badge, Button, ChatWorkspaceLayout, ConfirmDialog, EmptyState, InlineAlert, ScrollArea, Tooltip } from '../components/common';
import { getParsedApiError } from '../api/error';
import type { SkillInfo } from '../api/agent';
import { DashboardStateBlock } from '../components/dashboard';
import {
  useAgentChatStore,
  type Message,
  type ProgressStep,
} from '../stores/agentChatStore';
import { downloadSession, formatSessionAsMarkdown } from '../utils/chatExport';
import type { ChatFollowUpContext } from '../utils/chatFollowUp';
import {
  buildFollowUpPrompt,
  parseFollowUpRecordId,
  resolveChatFollowUpContext,
  sanitizeFollowUpStockCode,
  sanitizeFollowUpStockName,
} from '../utils/chatFollowUp';
import { isNearBottom } from '../utils/chatScroll';
import { getReportText } from '../utils/reportLanguage';

// Quick question examples shown on empty state
const QUICK_QUESTIONS = [
  { label: '用缠论分析茅台', skill: 'chan_theory' },
  { label: '波浪理论看宁德时代', skill: 'wave_theory' },
  { label: '分析比亚迪趋势', skill: 'bull_trend' },
  { label: '箱体震荡技能看中芯国际', skill: 'box_oscillation' },
  { label: '分析腾讯 hk00700', skill: 'bull_trend' },
  { label: '用情绪周期分析东方财富', skill: 'emotion_cycle' },
];

const MAX_SELECTED_SKILLS = 3;

const getMessageSkillNames = (msg: Message): string[] => {
  if (msg.skillNames?.length) return msg.skillNames;
  if (msg.skillName) return [msg.skillName];
  if (msg.skills?.length) return msg.skills;
  if (msg.skill) return [msg.skill];
  return [];
};

const getMessageSkillLabel = (msg: Message): string => getMessageSkillNames(msg).join('、');

const ChatPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [input, setInput] = useState('');
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [showSkillDesc, setShowSkillDesc] = useState<string | null>(null);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [isFollowUpContextLoading, setIsFollowUpContextLoading] = useState(false);
  const [sendToast, setSendToast] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set());
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const copyResetTimerRef = useRef<Partial<Record<string, number>>>({});
  const messagesViewportRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isMountedRef = useRef(true);
  const sendToastTimerRef = useRef<number | null>(null);
  const followUpHydrationTokenRef = useRef(0);
  const followUpContextRef = useRef<ChatFollowUpContext | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const pendingScrollBehaviorRef = useRef<ScrollBehavior>('auto');

  // Get localized text (default to Chinese)
  const text = getReportText('zh');

  // Cleanup timers on unmount
  useEffect(() => {
    const timers = copyResetTimerRef.current;
    return () => {
      if (sendToastTimerRef.current !== null) {
        window.clearTimeout(sendToastTimerRef.current);
      }
      Object.values(timers).forEach((timerId) => {
        if (timerId !== undefined) {
          window.clearTimeout(timerId);
        }
      });
    };
  }, []);

  // Set page title
  useEffect(() => {
    document.title = '问股 - DSA';
  }, []);

  useEffect(() => () => {
    isMountedRef.current = false;
  }, []);

  const {
    messages,
    loading,
    progressSteps,
    sessionId,
    sessions,
    sessionsLoading,
    chatError,
    loadSessions,
    loadInitialSession,
    switchSession,
    startStream,
    clearCompletionBadge,
  } = useAgentChatStore();

  const syncScrollState = useCallback(() => {
    const viewport = messagesViewportRef.current;
    if (!viewport) return;
    const nearBottom = isNearBottom({
      scrollTop: viewport.scrollTop,
      clientHeight: viewport.clientHeight,
      scrollHeight: viewport.scrollHeight,
    });
    shouldStickToBottomRef.current = nearBottom;
    setShowJumpToBottom((prev) => (nearBottom ? false : prev));
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  const requestScrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    shouldStickToBottomRef.current = true;
    pendingScrollBehaviorRef.current = behavior;
    setShowJumpToBottom(false);
  }, []);

  const handleMessagesScroll = useCallback(() => {
    syncScrollState();
  }, [syncScrollState]);

  useEffect(() => {
    syncScrollState();
  }, [syncScrollState, sessionId]);

  useEffect(() => {
    const behavior = pendingScrollBehaviorRef.current;
    const shouldAutoScroll = shouldStickToBottomRef.current;
    if (!shouldAutoScroll) {
      if (messages.length > 0 || progressSteps.length > 0 || loading) {
        setShowJumpToBottom(true);
      }
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      scrollToBottom(behavior);
      pendingScrollBehaviorRef.current = loading ? 'auto' : 'smooth';
    });

    return () => window.cancelAnimationFrame(frame);
  }, [messages, progressSteps, loading, sessionId, scrollToBottom]);

  useEffect(() => {
    if (!loading) {
      pendingScrollBehaviorRef.current = 'smooth';
    }
  }, [loading]);

  useEffect(() => {
    clearCompletionBadge();
  }, [clearCompletionBadge]);

  useEffect(() => {
    loadInitialSession();
  }, [loadInitialSession]);

  useEffect(() => {
    agentApi.getSkills()
      .then((res) => {
        setSkills(res.skills);
        const defaultId =
          res.default_skill_id ||
          res.skills[0]?.id ||
          '';
        setSelectedSkillIds(defaultId ? [defaultId] : []);
      })
      .catch((error) => {
        console.error('Failed to load chat skills:', error);
      });
  }, []);

  const availableSkillIds = new Set(skills.map((skill) => skill.id));
  const quickQuestions = QUICK_QUESTIONS.filter((question) => availableSkillIds.size === 0 || availableSkillIds.has(question.skill));
  const selectedSkillIdSet = new Set(selectedSkillIds);
  const skillLimitReached = selectedSkillIds.length >= MAX_SELECTED_SKILLS;

  const getSkillNames = useCallback(
    (skillIds: string[]) => skillIds.map((id) => skills.find((s) => s.id === id)?.name || id),
    [skills],
  );

  const normalizeSelectedSkillIds = useCallback((skillIds: string[]) => {
    const normalized: string[] = [];
    for (const skillId of skillIds) {
      const cleaned = skillId.trim();
      if (cleaned && !normalized.includes(cleaned)) {
        normalized.push(cleaned);
      }
    }
    return normalized.slice(0, MAX_SELECTED_SKILLS);
  }, []);

  const toggleSkillSelection = useCallback((skillId: string) => {
    setSelectedSkillIds((prev) => {
      if (prev.includes(skillId)) {
        return prev.filter((id) => id !== skillId);
      }
      if (prev.length >= MAX_SELECTED_SKILLS) {
        return prev;
      }
      return [...prev, skillId];
    });
  }, []);

  const handleStartNewChat = useCallback(() => {
    followUpContextRef.current = null;
    requestScrollToBottom('auto');
    useAgentChatStore.getState().startNewChat();
    setSidebarOpen(false);
  }, [requestScrollToBottom]);

  const handleSwitchSession = useCallback((targetSessionId: string) => {
    requestScrollToBottom('auto');
    switchSession(targetSessionId);
    setSidebarOpen(false);
  }, [requestScrollToBottom, switchSession]);

  const confirmDelete = useCallback(() => {
    if (!deleteConfirmId) return;
    agentApi.deleteChatSession(deleteConfirmId)
      .then(() => {
        loadSessions();
        if (deleteConfirmId === sessionId) {
          handleStartNewChat();
        }
      })
      .catch((error) => {
        console.error('Failed to delete chat session:', error);
      });
    setDeleteConfirmId(null);
  }, [deleteConfirmId, sessionId, loadSessions, handleStartNewChat]);

  // Handle follow-up from report page: ?stock=600519&name=贵州茅台&recordId=xxx
  useEffect(() => {
    const stock = sanitizeFollowUpStockCode(searchParams.get('stock'));
    const name = sanitizeFollowUpStockName(searchParams.get('name'));
    const recordId = parseFollowUpRecordId(searchParams.get('recordId'));

    if (!stock) {
      setSearchParams({}, { replace: true });
      return;
    }

    const hydrationToken = ++followUpHydrationTokenRef.current;
    setInput(buildFollowUpPrompt(stock, name));
    followUpContextRef.current = {
      stock_code: stock,
      stock_name: name,
    };
    if (recordId !== undefined) {
      setIsFollowUpContextLoading(true);
    }
    void resolveChatFollowUpContext({
      stockCode: stock,
      stockName: name,
      recordId,
    }).then((context) => {
      if (!isMountedRef.current || followUpHydrationTokenRef.current !== hydrationToken) {
        return;
      }
      followUpContextRef.current = context;
    }).finally(() => {
      if (isMountedRef.current && followUpHydrationTokenRef.current === hydrationToken) {
        setIsFollowUpContextLoading(false);
      }
    });
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

  const handleSend = useCallback(
    async (overrideMessage?: string, overrideSkillIds?: string[]) => {
      const msgText = (overrideMessage ?? input).trim();
      if (!msgText || loading) return;
      const usedSkillIds = normalizeSelectedSkillIds(overrideSkillIds ?? selectedSkillIds);
      const usedSkillNames = usedSkillIds.length > 0 ? getSkillNames(usedSkillIds) : ['通用'];

      const payload = {
        message: msgText,
        session_id: sessionId,
        ...(usedSkillIds.length > 0 ? { skills: usedSkillIds } : {}),
        context: followUpContextRef.current ?? undefined,
      };
      followUpHydrationTokenRef.current += 1;
      followUpContextRef.current = null;
      setIsFollowUpContextLoading(false);

      setInput('');
      requestScrollToBottom('smooth');
      await startStream(payload, {
        skillNames: usedSkillNames,
        skillName: usedSkillNames.join('、'),
      });
    },
    [getSkillNames, input, loading, normalizeSelectedSkillIds, requestScrollToBottom, selectedSkillIds, sessionId, startStream],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleQuickQuestion = (q: (typeof QUICK_QUESTIONS)[0]) => {
    setSelectedSkillIds([q.skill]);
    handleSend(q.label, [q.skill]);
  };

  const showSendFeedback = useCallback((nextToast: { type: 'success' | 'error'; message: string }, durationMs: number) => {
    if (sendToastTimerRef.current !== null) {
      window.clearTimeout(sendToastTimerRef.current);
    }
    setSendToast(nextToast);
    sendToastTimerRef.current = window.setTimeout(() => {
      setSendToast(null);
      sendToastTimerRef.current = null;
    }, durationMs);
  }, []);

  const toggleThinking = (msgId: string) => {
    setExpandedThinking((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };

  const copyMessageToClipboard = async (msgId: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessages((prev) => new Set(prev).add(msgId));
      const existingTimer = copyResetTimerRef.current[msgId];
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
      }
      copyResetTimerRef.current[msgId] = window.setTimeout(() => {
        setCopiedMessages((prev) => {
          const next = new Set(prev);
          next.delete(msgId);
          return next;
        });
        delete copyResetTimerRef.current[msgId];
      }, 2000);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  };

  const downloadMessageAsMarkdown = useCallback((msg: Message) => {
    const skillLabel = getMessageSkillLabel(msg);
    const heading = msg.role === 'user' ? '# 用户消息' : `# AI 回复${skillLabel ? ` · ${skillLabel}` : ''}`;
    const content = [heading, '', msg.content].join('\n');
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${msg.role === 'user' ? 'user' : 'assistant'}-message-${msg.id}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }, []);

  const getCurrentStage = (steps: ProgressStep[]): string => {
    if (steps.length === 0) return '正在连接...';
    const last = steps[steps.length - 1];
    if (last.type === 'thinking') return last.message || 'AI 正在思考...';
    if (last.type === 'tool_start')
      return `${last.display_name || last.tool}...`;
    if (last.type === 'tool_done')
      return `${last.display_name || last.tool} 完成`;
    if (last.type === 'generating')
      return last.message || '正在生成最终分析...';
    return '处理中...';
  };

  const renderThinkingBlock = (msg: Message) => {
    if (!msg.thinkingSteps || msg.thinkingSteps.length === 0) return null;
    const isExpanded = expandedThinking.has(msg.id);
    const toolSteps = msg.thinkingSteps.filter((s) => s.type === 'tool_done');
    const totalDuration = toolSteps.reduce(
      (sum, s) => sum + (s.duration || 0),
      0,
    );
    const summary = `${toolSteps.length} 个工具调用 · ${totalDuration.toFixed(1)}s`;

    return (
      <button
        onClick={() => toggleThinking(msg.id)}
        className="flex items-center gap-2 text-xs text-muted-text hover:text-secondary-text transition-colors mb-2 w-full text-left"
      >
        <svg
          className={`w-3 h-3 transition-transform flex-shrink-0 ${isExpanded ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 5l7 7-7 7"
          />
        </svg>
        <span className="flex items-center gap-1.5">
          <span className="opacity-60">思考过程</span>
          <span className="text-muted-text/50">·</span>
          <span className="opacity-50">{summary}</span>
        </span>
      </button>
    );
  };

  const renderThinkingDetails = (steps: ProgressStep[]) => (
    <div className="mb-3 pl-5 border-l border-border/40 space-y-1.5 animate-fade-in">
      {steps.map((step, idx) => {
        let statusClass = 'ui-chat-progress-item-muted';
        let iconClass = 'ui-chat-progress-dot-muted';
        let text = '';
        if (step.type === 'thinking') {
          text = step.message || `第 ${step.step} 步：思考`;
          statusClass = 'ui-chat-progress-item-info';
          iconClass = 'ui-chat-progress-dot-info';
        } else if (step.type === 'tool_start') {
          text = `${step.display_name || step.tool}...`;
          statusClass = 'ui-chat-progress-item-info';
          iconClass = 'ui-chat-progress-dot-info';
        } else if (step.type === 'tool_done') {
          text = `${step.display_name || step.tool} (${step.duration}s)`;
          statusClass = step.success ? 'ui-chat-progress-item-success' : 'ui-chat-progress-item-danger';
          iconClass = step.success ? 'ui-chat-progress-dot-success' : 'ui-chat-progress-dot-danger';
        } else if (step.type === 'generating') {
          text = step.message || '生成分析';
          statusClass = 'ui-chat-progress-item-info';
          iconClass = 'ui-chat-progress-dot-info';
        }
        return (
          <div
            key={idx}
            className={cn('ui-chat-progress-item', statusClass)}
          >
            <span className={cn('ui-chat-progress-dot', iconClass)} />
            <span className="leading-relaxed">{text}</span>
          </div>
        );
      })}
    </div>
  );

  const sidebarContent = (
    <>
      <div className="flex items-center justify-between border-b border-border/70 bg-surface/40 p-3.5">
        <h2 className="ui-eyebrow flex items-center gap-2">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          历史对话
        </h2>
        <button
          onClick={handleStartNewChat}
          className="ui-icon-button h-8 w-8 rounded-lg"
          aria-label="开启新对话"
        >
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 4v16m8-8H4"
            />
          </svg>
        </button>
      </div>
      <ScrollArea testId="chat-session-list-scroll" viewportClassName="p-3">
        {sessionsLoading ? (
          <DashboardStateBlock
            loading
            compact
            title="加载对话中..."
            className="rounded-2xl border border-dashed border-border/50 bg-surface/30"
          />
        ) : sessions.length === 0 ? (
          <DashboardStateBlock
            compact
            title="暂无历史对话"
            description="开始提问后，这里会保留会话记录。"
            className="rounded-2xl border border-dashed border-border/50 bg-surface/30"
          />
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => (
              <div key={s.session_id} className="ui-chat-session-row">
                <button
                  type="button"
                  onClick={() => handleSwitchSession(s.session_id)}
                  className={cn(
                    'ui-chat-session-item',
                    s.session_id === sessionId && 'ui-chat-session-item-active',
                  )}
                  aria-label={`切换到对话 ${s.title}`}
                  aria-current={s.session_id === sessionId ? 'page' : undefined}
                >
                  <div className="ui-chat-session-indicator" />
                  <div className="ui-chat-session-content">
                    <span className="ui-chat-session-title">{s.title}</span>
                    <div className="mt-0.5 flex items-center gap-2">
                      <span className="ui-chat-session-meta">
                        {s.message_count} 条对话
                      </span>
                      {s.last_active && (
                        <>
                          <span className="ui-chat-session-separator" />
                          <span className="ui-chat-session-meta">
                            {new Date(s.last_active).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  className="ui-chat-session-delete-button"
                  onClick={() => {
                    setDeleteConfirmId(s.session_id);
                  }}
                  aria-label={`删除对话 ${s.title}`}
                >
                  <svg
                    className="w-3.5 h-3.5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                    />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        )}
      </ScrollArea>
    </>
  );

  return (
    <ChatWorkspaceLayout
      data-testid="chat-workspace"
      className="chat-workspace-layout-workspace min-w-0"
    >
      {/* Desktop sidebar */}
      <div className="ui-card ui-card-padding-none hidden h-full w-64 flex-shrink-0 flex-col md:flex">
        {sidebarContent}
      </div>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 md:hidden"
          onClick={() => setSidebarOpen(false)}
        >
          <div className="ui-drawer-backdrop absolute inset-0" />
          <div
            className="ui-drawer-panel ui-drawer-panel-left absolute bottom-0 left-0 top-0 w-72 overflow-hidden p-3"
            onClick={(e) => e.stopPropagation()}
          >
            {sidebarContent}
          </div>
        </div>
      )}

      {/* Delete confirmation dialog */}
      <ConfirmDialog
        isOpen={Boolean(deleteConfirmId)}
        title="删除对话"
        message="删除后，该对话将不可恢复，确认删除吗？"
        confirmText="删除"
        cancelText="取消"
        isDanger
        onConfirm={confirmDelete}
        onCancel={() => setDeleteConfirmId(null)}
      />

      {/* Main chat area */}
      <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
        <header className="mb-4 flex-shrink-0 space-y-3">
          <div className="flex items-start justify-between gap-4">
            <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
              <button
                onClick={() => setSidebarOpen(true)}
                className="-ml-1 ui-icon-button h-9 w-9 md:hidden"
                aria-label="历史对话"
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M4 6h16M4 12h16M4 18h16"
                  />
                </svg>
              </button>
              <svg
                className="w-6 h-6 text-primary"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                />
              </svg>
              问股
            </h1>
            {messages.length > 0 && (
              <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
                <Tooltip content="导出会话为 Markdown 文件">
                  <span className="inline-flex">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => downloadSession(messages)}
                      aria-label="导出会话为 Markdown 文件"
                    >
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                        />
                      </svg>
                      导出会话
                    </Button>
                  </span>
                </Tooltip>
                <Tooltip content="发送到已配置的通知机器人/邮箱">
                  <span className="inline-flex">
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={sending}
                      onClick={async () => {
                        if (sending) return;
                        setSending(true);
                        setSendToast(null);
                        try {
                          const content = formatSessionAsMarkdown(messages);
                          await agentApi.sendChat(content);
                          showSendFeedback({ type: 'success', message: '已发送到通知渠道' }, 3000);
                        } catch (err) {
                          const parsed = getParsedApiError(err);
                          showSendFeedback({
                            type: 'error',
                            message: parsed.message || '发送失败',
                          }, 5000);
                        } finally {
                          setSending(false);
                        }
                      }}
                      aria-label="发送到已配置的通知机器人/邮箱"
                    >
                      {sending ? (
                        <svg
                          className="w-4 h-4 animate-spin"
                          fill="none"
                          viewBox="0 0 24 24"
                        >
                          <circle
                            className="opacity-25"
                            cx="12"
                            cy="12"
                            r="10"
                            stroke="currentColor"
                            strokeWidth="4"
                          />
                          <path
                            className="opacity-75"
                            fill="currentColor"
                            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                          />
                        </svg>
                      ) : (
                        <svg
                          className="w-4 h-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                          />
                        </svg>
                      )}
                      发送
                    </Button>
                  </span>
                </Tooltip>
              </div>
            )}
          </div>
          <p className="text-secondary-text text-sm">
            向 AI 询问个股分析，获取基于技能视角的交易建议与实时决策报告。
          </p>
          {sendToast ? (
            <InlineAlert
              variant={sendToast.type === 'success' ? 'success' : 'danger'}
              title={sendToast.type === 'success' ? '发送成功' : '发送失败'}
              message={sendToast.message}
              className="max-w-md rounded-xl px-3 py-2 text-xs shadow-none"
            />
          ) : null}
        </header>

        <div className="ui-card ui-card-padding-none relative z-10 flex min-h-0 flex-1 flex-col">
          {/* Messages */}
          <ScrollArea
            className="relative z-10 flex-1"
            viewportRef={messagesViewportRef}
            onScroll={handleMessagesScroll}
            viewportClassName="space-y-6 p-4 md:p-6"
            testId="chat-message-scroll"
          >
            {messages.length === 0 && !loading ? (
              <div className="flex h-full items-center justify-center">
                <EmptyState
                  title="开始问股"
                  description="输入「分析 600519」或「茅台现在能买吗」，AI 将调用实时数据工具为您生成决策报告。"
                  className="max-w-2xl border-dashed bg-card/55"
                  icon={(
                    <svg
                      className="h-8 w-8"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={1.5}
                        d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                      />
                    </svg>
                  )}
                  action={(
                    <div className="flex max-w-lg flex-wrap justify-center gap-2">
                      {quickQuestions.map((q, i) => (
                        <button
                          key={i}
                          onClick={() => handleQuickQuestion(q)}
                          className="ui-chat-quick-question"
                        >
                          {q.label}
                        </button>
                      ))}
                    </div>
                  )}
                />
              </div>
            ) : (
              messages.map((msg) => {
                const skillLabel = getMessageSkillLabel(msg);
                return (
                <div
                  key={msg.id}
                  className={`flex gap-4 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
                >
                  <div
                    className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[10px] font-bold shadow-sm transition-all',
                      msg.role === 'user' ? 'ui-chat-avatar-user' : 'ui-chat-avatar-ai'
                    )}
                  >
                    {msg.role === 'user' ? 'U' : 'AI'}
                  </div>
                  <div
                    className={cn(
                      'ui-chat-message-bubble group/message min-w-0 overflow-hidden px-5 py-3.5 transition-colors',
                      msg.role === 'user'
                        ? 'ui-chat-message-bubble-user w-fit max-w-[min(100%,28rem)]'
                        : 'ui-chat-message-bubble-ai w-full'
                    )}
                  >
                    {msg.role === 'assistant' && skillLabel && (
                      <div className="mb-2">
                        <Badge variant="info" className="ui-chat-skill-badge shadow-none" aria-label={`技能 ${skillLabel}`}>
                          <svg
                            className="w-3 h-3"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M13 10V3L4 14h7v7l9-11h-7z"
                            />
                          </svg>
                          {skillLabel}
                        </Badge>
                      </div>
                    )}
                    {msg.role === 'assistant' && renderThinkingBlock(msg)}
                    {msg.role === 'assistant' &&
                      expandedThinking.has(msg.id) &&
                      msg.thinkingSteps &&
                      renderThinkingDetails(msg.thinkingSteps)}
                    {msg.role === 'assistant' ? (
                      <div className="relative">
                        <div className="ui-chat-message-actions">
                          <button
                            type="button"
                            onClick={() => copyMessageToClipboard(msg.id, msg.content)}
                            className="ui-chat-mini-action"
                            aria-label={copiedMessages.has(msg.id) ? text.copied : text.copy}
                          >
                            {copiedMessages.has(msg.id) ? text.copied : text.copy}
                          </button>
                          <button
                            type="button"
                            onClick={() => downloadMessageAsMarkdown(msg)}
                            className="ui-chat-mini-action"
                            aria-label="导出此条消息为 Markdown"
                          >
                            导出
                          </button>
                        </div>
                        <div
                          className="ui-prose prose prose-invert prose-sm max-w-none pr-20 sm:pr-24
                            prose-headings:text-foreground prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-2
                            prose-h1:text-lg prose-h2:text-base prose-h3:text-sm
                            prose-p:leading-relaxed prose-p:mb-2 prose-p:last:mb-0
                            prose-strong:text-foreground prose-strong:font-semibold
                            prose-ul:my-2 prose-ol:my-2 prose-li:my-1
                            prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none
                            prose-pre:border prose-table:border-collapse prose-hr:my-3
                            prose-a:no-underline hover:prose-a:underline prose-blockquote:text-secondary-text
                            break-words"
                        >
                          <Markdown remarkPlugins={[remarkGfm]}>
                            {msg.content}
                          </Markdown>
                        </div>
                      </div>
                    ) : (
                      msg.content
                        .split('\n')
                        .map((line, i) => (
                          <p
                            key={i}
                            className="mb-1 last:mb-0 leading-relaxed"
                          >
                            {line || '\u00A0'}
                          </p>
                        ))
                    )}
                  </div>
                </div>
                );
              })
            )}

            {loading && (
              <div className="flex gap-4">
                <div className="ui-chat-avatar-ai flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-xs font-bold">
                  AI
                </div>
                <div className="ui-chat-message-bubble ui-chat-message-bubble-ai w-full overflow-hidden px-5 py-4">
                  <div className="flex items-center gap-2.5 text-sm text-secondary-text">
                    <div className="relative w-4 h-4 flex-shrink-0">
                      <div className="absolute inset-0 rounded-full border-2 border-primary/20" />
                      <div className="absolute inset-0 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                    </div>
                    <span className="text-secondary-text">
                      {getCurrentStage(progressSteps)}
                    </span>
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </ScrollArea>

          {showJumpToBottom && (
            <div className="pointer-events-none absolute bottom-[5.75rem] right-4 z-20 md:bottom-24 md:right-6">
              <button
                type="button"
                className="pointer-events-auto ui-chat-mini-action shadow-card"
                onClick={() => {
                  requestScrollToBottom('smooth');
                  scrollToBottom('smooth');
                }}
                aria-label="查看最新消息"
              >
                <svg
                  className="h-3.5 w-3.5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M19 14l-7 7m0 0l-7-7m7 7V3"
                  />
                </svg>
                有新消息
              </button>
            </div>
          )}

          {/* Input area */}
          <div className="relative z-20 border-t border-border/70 bg-card/90 p-4 md:p-6">
            <div className="space-y-3">
              {chatError ? <ApiErrorAlert error={chatError} /> : null}
              {isFollowUpContextLoading ? (
                <InlineAlert
                  variant="info"
                  title="追问上下文加载中"
                  message="正在加载历史分析上下文；现在可直接发送追问。"
                  className="rounded-xl px-3 py-2 text-xs shadow-none"
                />
              ) : null}
            {skills.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  aria-pressed={selectedSkillIds.length === 0}
                  onClick={() => setSelectedSkillIds([])}
                  className={cn('ui-chat-skill-chip', selectedSkillIds.length === 0 && 'ui-chat-skill-chip-active')}
                >
                  通用分析
                </button>
                {skills.map((s) => {
                  const checked = selectedSkillIdSet.has(s.id);
                  const disabled = !checked && skillLimitReached;
                  return (
                    <div key={s.id} className="relative">
                      <button
                        type="button"
                        aria-pressed={checked}
                        disabled={disabled}
                        onClick={() => toggleSkillSelection(s.id)}
                        onMouseEnter={() => setShowSkillDesc(s.id)}
                        onMouseLeave={() => setShowSkillDesc(null)}
                        className={cn('ui-chat-skill-chip', checked && 'ui-chat-skill-chip-active')}
                      >
                        {s.name}
                      </button>
                      {showSkillDesc === s.id && s.description && (
                        <div className="ui-chat-skill-tooltip">
                          <p className="ui-chat-skill-tooltip-title">{s.name}</p>
                          <p>{s.description}</p>
                        </div>
                      )}
                    </div>
                  );
                })}
                {skillLimitReached && (
                  <span className="text-[11px] text-muted-text">
                    最多选 {MAX_SELECTED_SKILLS} 个
                  </span>
                )}
              </div>
            )}

              <div className="flex items-end gap-3">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="例如：分析 600519 / 茅台现在适合买入吗？ (Enter 发送, Shift+Enter 换行)"
                  disabled={loading}
                  rows={1}
                  className="ui-input flex-1 min-h-[44px] max-h-[200px] resize-none px-4 py-2.5 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                  style={{ height: 'auto' }}
                  onInput={(e) => {
                    const t = e.target as HTMLTextAreaElement;
                    t.style.height = 'auto';
                    t.style.height = `${Math.min(t.scrollHeight, 200)}px`;
                  }}
                />
                <Button
                  variant="primary"
                  onClick={() => handleSend()}
                  disabled={!input.trim() || loading}
                  isLoading={loading}
                  className="flex-shrink-0"
                >
                  发送
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </ChatWorkspaceLayout>
  );
};

export default ChatPage;
