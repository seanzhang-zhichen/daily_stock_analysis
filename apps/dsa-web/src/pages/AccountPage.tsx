import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Bell,
  BellOff,
  CheckCircle2,
  CreditCard,
  Download,
  KeyRound,
  Loader2,
  LogOut,
  Mail,
  Plus,
  ShieldCheck,
  Sparkles,
  Star,
  Trash2,
  Webhook,
} from 'lucide-react';
import { Button, Input, Card } from '../components/common';
import { SettingsAlert } from '../components/settings';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { accountApi, type WatchlistItem, type NotificationPrefs } from '../api/account';
import { getParsedApiError, isParsedApiError, type ParsedApiError } from '../api/error';
import { useAuth } from '../hooks';

type FormError = ParsedApiError | string | null;

const formatDate = (value?: string | null): string => {
  if (!value) {
    return '—';
  }
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

const WEBHOOK_TYPES = ['feishu', 'wecom', 'dingtalk', 'discord', 'telegram', 'custom'] as const;
const WEBHOOK_TYPE_LABELS: Record<string, string> = {
  feishu: '飞书',
  wecom: '企业微信',
  dingtalk: '钉钉',
  discord: 'Discord',
  telegram: 'Telegram',
  custom: '自定义',
};

const AccountPage: React.FC = () => {
  const { userMode, changePassword, logout, refreshStatus } = useAuth();
  const navigate = useNavigate();

  const user = userMode?.user ?? null;
  const plan = userMode?.plan ?? null;

  // 改密码 form
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newPasswordConfirm, setNewPasswordConfirm] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<FormError>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);

  // 自选股
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [maxStocks, setMaxStocks] = useState<number>(3);
  const [watchlistLoading, setWatchlistLoading] = useState(false);
  const [watchlistError, setWatchlistError] = useState<string | null>(null);
  const [addInput, setAddInput] = useState('');
  const [isAdding, setIsAdding] = useState(false);

  // 账号注销
  const [deletionPending, setDeletionPending] = useState(false);
  const [deletionRequestedAt, setDeletionRequestedAt] = useState<string | null>(null);
  const [deletionCoolingOff, setDeletionCoolingOff] = useState(7);
  const [isDeletionLoading, setIsDeletionLoading] = useState(false);
  const [deletionConfirmText, setDeletionConfirmText] = useState('');
  const [showDeletionConfirm, setShowDeletionConfirm] = useState(false);
  const [deletionMsg, setDeletionMsg] = useState<string | null>(null);

  // 数据导出
  const [isExporting, setIsExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  // 通知偏好
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null);
  const [prefsLoading, setPrefsLoading] = useState(false);
  const [prefsError, setPrefsError] = useState<string | null>(null);
  const [prefsSaving, setPrefsSaving] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState('');
  const [webhookType, setWebhookType] = useState('feishu');

  useEffect(() => {
    document.title = '账户设置 - DSA';
  }, []);

  const loadWatchlist = useCallback(async () => {
    setWatchlistLoading(true);
    setWatchlistError(null);
    try {
      const res = await accountApi.getWatchlist();
      setWatchlist(res.stocks);
      setMaxStocks(res.maxStocks);
    } catch (err) {
      setWatchlistError(getParsedApiError(err).message);
    } finally {
      setWatchlistLoading(false);
    }
  }, []);

  const loadPrefs = useCallback(async () => {
    setPrefsLoading(true);
    setPrefsError(null);
    try {
      const res = await accountApi.getNotificationPrefs();
      setPrefs(res.prefs);
      setWebhookUrl(res.prefs.webhookUrl ?? '');
      setWebhookType(res.prefs.webhookType ?? 'feishu');
    } catch (err) {
      setPrefsError(getParsedApiError(err).message);
    } finally {
      setPrefsLoading(false);
    }
  }, []);

  const loadDeletionStatus = useCallback(async () => {
    try {
      const res = await accountApi.getDeletionStatus();
      setDeletionPending(res.hasPendingDeletion);
      setDeletionRequestedAt(res.deletionRequestedAt);
      setDeletionCoolingOff(res.coolingOffDays);
    } catch {
      // 静默失败，不影响主流程
    }
  }, []);

  useEffect(() => {
    if (userMode?.loggedIn) {
      void loadWatchlist();
      void loadPrefs();
      void loadDeletionStatus();
    }
  }, [userMode?.loggedIn, loadWatchlist, loadPrefs, loadDeletionStatus]);

  const handleAddStock = useCallback(async (code: string, name?: string) => {
    if (!code.trim()) return;
    setIsAdding(true);
    setWatchlistError(null);
    try {
      const res = await accountApi.addWatchlistStock({ stockCode: code.trim(), stockName: name });
      setWatchlist((prev) => {
        if (prev.some((s) => s.stockCode === res.stock.stockCode)) return prev;
        return [...prev, res.stock];
      });
      setAddInput('');
    } catch (err) {
      setWatchlistError(getParsedApiError(err).message);
    } finally {
      setIsAdding(false);
    }
  }, []);

  const handleRemoveStock = useCallback(async (stockCode: string) => {
    setWatchlistError(null);
    try {
      await accountApi.removeWatchlistStock(stockCode);
      setWatchlist((prev) => prev.filter((s) => s.stockCode !== stockCode));
    } catch (err) {
      setWatchlistError(getParsedApiError(err).message);
    }
  }, []);

  const handleTogglePref = useCallback(
    async (field: 'dailyPushEnabled' | 'emailEnabled', value: boolean) => {
      setPrefsSaving(true);
      setPrefsError(null);
      try {
        const res = await accountApi.updateNotificationPrefs({ [field]: value });
        setPrefs(res.prefs);
      } catch (err) {
        setPrefsError(getParsedApiError(err).message);
      } finally {
        setPrefsSaving(false);
      }
    },
    []
  );

  const handleSaveWebhook = useCallback(async () => {
    setPrefsSaving(true);
    setPrefsError(null);
    try {
      const res = await accountApi.updateNotificationPrefs({
        webhookUrl: webhookUrl.trim() || null,
        webhookType: webhookUrl.trim() ? webhookType : null,
        clearWebhook: !webhookUrl.trim(),
      });
      setPrefs(res.prefs);
    } catch (err) {
      setPrefsError(getParsedApiError(err).message);
    } finally {
      setPrefsSaving(false);
    }
  }, [webhookUrl, webhookType]);

  const handleRequestDeletion = useCallback(async () => {
    if (deletionConfirmText !== '注销账号') return;
    setIsDeletionLoading(true);
    setDeletionMsg(null);
    try {
      await accountApi.requestDeletion();
      setDeletionPending(true);
      setDeletionRequestedAt(new Date().toISOString());
      setShowDeletionConfirm(false);
      setDeletionConfirmText('');
      setDeletionMsg(`注销申请已提交，${deletionCoolingOff} 天冷静期后账号将被注销。你已被强制登出，如需取消请重新登录。`);
      await logout();
      navigate('/login');
    } catch (err) {
      setDeletionMsg(getParsedApiError(err).message);
    } finally {
      setIsDeletionLoading(false);
    }
  }, [deletionConfirmText, deletionCoolingOff, logout, navigate]);

  const handleCancelDeletion = useCallback(async () => {
    setIsDeletionLoading(true);
    setDeletionMsg(null);
    try {
      await accountApi.cancelDeletion();
      setDeletionPending(false);
      setDeletionRequestedAt(null);
      setDeletionMsg('注销申请已取消，账号恢复正常。');
    } catch (err) {
      setDeletionMsg(getParsedApiError(err).message);
    } finally {
      setIsDeletionLoading(false);
    }
  }, []);

  const handleDataExport = useCallback(async () => {
    setIsExporting(true);
    setExportMsg(null);
    try {
      const res = await accountApi.requestDataExport();
      setExportMsg(res.message);
    } catch (err) {
      setExportMsg(`导出失败：${getParsedApiError(err).message}`);
    } finally {
      setIsExporting(false);
    }
  }, []);

  const planName = useMemo(() => plan?.name ?? user?.plan ?? '免费会员', [plan, user]);
  const planExpiresAt = useMemo(
    () => plan?.expiresAt ?? user?.planExpiresAt ?? null,
    [plan, user]
  );

  if (userMode == null || !userMode.userModeEnabled) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <Card title="账户设置" subtitle="ACCOUNT">
          <p className="text-sm text-secondary-text">
            当前实例未启用 To C 多用户模式 (<code className="rounded bg-hover px-1">ENABLE_USER_REGISTRATION</code>),
            账户管理仅在管理员设置页可用。
          </p>
        </Card>
      </div>
    );
  }

  if (!userMode.loggedIn || user == null) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <Card title="账户设置" subtitle="ACCOUNT">
          <p className="text-sm text-secondary-text">请先登录后查看账户信息。</p>
          <div className="mt-4">
            <Link to="/login">
              <Button variant="primary">前往登录</Button>
            </Link>
          </div>
        </Card>
      </div>
    );
  }

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (!currentPassword.trim()) {
      setError('请输入当前密码');
      return;
    }
    if (newPassword.length < 8) {
      setError('新密码至少 8 位');
      return;
    }
    if (newPassword !== newPasswordConfirm) {
      setError('两次输入的新密码不一致');
      return;
    }

    setIsSubmitting(true);
    try {
      const res = await changePassword(currentPassword, newPassword, newPasswordConfirm);
      if (res.success) {
        setSuccess('密码已更新, 当前会话已失效, 请重新登录。');
        setCurrentPassword('');
        setNewPassword('');
        setNewPasswordConfirm('');
        // 后端会吊销当前 session 并清 cookie; 刷新 status 让上层跳到登录页
        await refreshStatus();
        setTimeout(() => navigate('/login', { replace: true }), 1500);
      } else {
        setError(res.error ?? '修改失败');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleLogout = async () => {
    if (isLoggingOut) {
      return;
    }
    setIsLoggingOut(true);
    try {
      await logout();
      navigate('/login', { replace: true });
    } catch {
      // logout error is surfaced through fetchStatus refresh; navigate anyway
      navigate('/login', { replace: true });
    } finally {
      setIsLoggingOut(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6 lg:py-8">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.15em] text-muted-text">
            ACCOUNT
          </p>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">账户设置</h1>
          <p className="text-sm text-secondary-text/80">
            管理你的账户信息、订阅状态、安全设置以及自带 API Key (BYOK)。
          </p>
        </div>
        {user && (
          <div className="hidden shrink-0 items-center gap-3 sm:flex">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-primary/20 bg-primary/10 text-sm font-bold text-primary">
              {user.email.charAt(0).toUpperCase()}
            </div>
          </div>
        )}
      </div>

      {/* 账户信息 */}
      <Card title="账户信息" subtitle="PROFILE">
        <dl className="grid gap-4 text-sm md:grid-cols-2">
          <div className="space-y-1">
            <dt className="text-xs uppercase tracking-wider text-secondary-text">
              <Mail className="mr-1 inline h-3.5 w-3.5" /> 邮箱
            </dt>
            <dd className="flex items-center gap-2 text-foreground">
              <span className="break-all">{user.email}</span>
              {user.emailVerified ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-cyan/30 bg-cyan/10 px-2 py-0.5 text-xs text-cyan">
                  <CheckCircle2 className="h-3 w-3" /> 已验证
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-300">
                  未验证
                </span>
              )}
            </dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs uppercase tracking-wider text-secondary-text">
              <Sparkles className="mr-1 inline h-3.5 w-3.5" /> 当前套餐
            </dt>
            <dd className="flex flex-wrap items-center gap-2 text-foreground">
              <span>{planName}</span>
              {plan?.isPro ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-purple-400/40 bg-purple-500/10 px-2 py-0.5 text-xs text-purple-300">
                  Pro
                </span>
              ) : null}
              {planExpiresAt ? (
                <span className="text-xs text-secondary-text">
                  到期: {formatDate(planExpiresAt)}
                </span>
              ) : (
                <span className="text-xs text-secondary-text">永久</span>
              )}
            </dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs uppercase tracking-wider text-secondary-text">注册时间</dt>
            <dd className="text-foreground">{formatDate(user.createdAt)}</dd>
          </div>
          <div className="space-y-1">
            <dt className="text-xs uppercase tracking-wider text-secondary-text">上次登录</dt>
            <dd className="text-foreground">{formatDate(user.lastLoginAt)}</dd>
          </div>
        </dl>

        <div className="mt-5 flex flex-wrap gap-3">
          <Link to="/billing">
            <Button variant="primary">
              <CreditCard className="h-4 w-4" />
              {plan?.isPro ? '管理订阅' : '升级到 Pro'}
            </Button>
          </Link>
          {plan?.canByok ? (
            <Link to="/account/api-keys">
              <Button variant="outline">
                <KeyRound className="h-4 w-4" /> 管理我的 API Key
              </Button>
            </Link>
          ) : null}
        </div>
      </Card>

      {/* 自选股管理 */}
      <Card title="我的自选股" subtitle="WATCHLIST">
        {watchlistLoading ? (
          <div className="flex items-center gap-2 text-secondary-text text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : (
          <div className="space-y-4">
            {watchlistError && (
              <SettingsAlert title="操作失败" message={watchlistError} variant="error" />
            )}

            {/* 当前自选股列表 */}
            {watchlist.length === 0 ? (
              <p className="text-sm text-secondary-text">暂无自选股，在下方添加你关注的股票。</p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {watchlist.map((item) => (
                  <li
                    key={item.stockCode}
                    className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-card/60 px-3 py-1.5 text-sm"
                  >
                    <Star className="h-3.5 w-3.5 text-amber-400" />
                    <span className="font-medium text-foreground">{item.stockCode}</span>
                    {item.stockName && (
                      <span className="text-secondary-text">{item.stockName}</span>
                    )}
                    <button
                      type="button"
                      className="ml-1 text-secondary-text hover:text-red-400 transition-colors"
                      onClick={() => void handleRemoveStock(item.stockCode)}
                      title="删除"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {/* 添加新股票 */}
            {watchlist.length < maxStocks ? (
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <p className="mb-1.5 text-xs text-secondary-text">
                    添加自选股（{watchlist.length}/{maxStocks}）
                  </p>
                  <StockAutocomplete
                    value={addInput}
                    onChange={setAddInput}
                    onSubmit={(code, name) => void handleAddStock(code, name)}
                    disabled={isAdding}
                    placeholder="输入股票代码或名称"
                  />
                </div>
                <Button
                  variant="primary"
                  isLoading={isAdding}
                  onClick={() => void handleAddStock(addInput)}
                  disabled={!addInput.trim() || isAdding}
                >
                  <Plus className="h-4 w-4" /> 添加
                </Button>
              </div>
            ) : (
              <div className="rounded-lg border border-amber-400/20 bg-amber-500/5 px-3 py-2 text-sm text-amber-300">
                已达到当前套餐自选股上限（{maxStocks} 只）。
                {!plan?.isPro && (
                  <Link to="/billing" className="ml-1 underline hover:text-amber-200">
                    升级 Pro 解锁更多
                  </Link>
                )}
              </div>
            )}
          </div>
        )}
      </Card>

      {/* 通知偏好 */}
      <Card title="通知偏好" subtitle="NOTIFICATIONS">
        {prefsLoading ? (
          <div className="flex items-center gap-2 text-secondary-text text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : (
          <div className="space-y-4">
            {prefsError && (
              <SettingsAlert title="操作失败" message={prefsError} variant="error" />
            )}

            {/* 每日推送开关 */}
            <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-card/60 px-4 py-3">
              <div className="space-y-0.5">
                <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  <Bell className="h-4 w-4 text-cyan" /> 每日推送
                </p>
                <p className="text-xs text-secondary-text">
                  交易日收盘后，自动发送自选股 AI 分析报告到你的邮箱。
                </p>
              </div>
              <button
                type="button"
                disabled={prefsSaving}
                onClick={() => void handleTogglePref('dailyPushEnabled', !(prefs?.dailyPushEnabled ?? false))}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
                  prefs?.dailyPushEnabled ? 'bg-cyan' : 'bg-border'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    prefs?.dailyPushEnabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            {/* 邮件通知开关 */}
            <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-card/60 px-4 py-3">
              <div className="space-y-0.5">
                <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  <Mail className="h-4 w-4 text-cyan" /> 邮件通知
                </p>
                <p className="text-xs text-secondary-text">
                  通过注册邮箱接收分析报告与系统通知。
                </p>
              </div>
              <button
                type="button"
                disabled={prefsSaving}
                onClick={() => void handleTogglePref('emailEnabled', !(prefs?.emailEnabled ?? true))}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
                  (prefs?.emailEnabled ?? true) ? 'bg-cyan' : 'bg-border'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    (prefs?.emailEnabled ?? true) ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            {/* Pro Webhook */}
            {plan?.canWebhook ? (
              <div className="space-y-3 rounded-xl border border-border/60 bg-card/60 px-4 py-3">
                <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  <Webhook className="h-4 w-4 text-purple-400" /> Pro Webhook 推送
                </p>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="sm:col-span-2">
                    <Input
                      type="url"
                      label="Webhook URL"
                      placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                      value={webhookUrl}
                      onChange={(e) => setWebhookUrl(e.target.value)}
                      disabled={prefsSaving}
                    />
                  </div>
                  <div>
                    <p className="mb-1 text-xs text-secondary-text">类型</p>
                    <select
                      className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm"
                      value={webhookType}
                      onChange={(e) => setWebhookType(e.target.value)}
                      disabled={prefsSaving}
                    >
                      {WEBHOOK_TYPES.map((t) => (
                        <option key={t} value={t}>{WEBHOOK_TYPE_LABELS[t]}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="primary" isLoading={prefsSaving} onClick={() => void handleSaveWebhook()}>
                    保存 Webhook
                  </Button>
                  {prefs?.webhookUrl && (
                    <Button
                      variant="outline"
                      disabled={prefsSaving}
                      onClick={() => { setWebhookUrl(''); void handleSaveWebhook(); }}
                    >
                      <BellOff className="h-4 w-4" /> 清除
                    </Button>
                  )}
                </div>
                {prefs?.webhookUrl && (
                  <p className="text-xs text-secondary-text">
                    当前已配置 {WEBHOOK_TYPE_LABELS[prefs.webhookType ?? 'custom'] ?? prefs.webhookType} Webhook。
                  </p>
                )}
              </div>
            ) : (
              <div className="rounded-lg border border-border/40 bg-card/40 px-3 py-2 text-sm text-secondary-text">
                <Webhook className="mr-1.5 inline h-3.5 w-3.5" />
                Pro Webhook 推送（飞书 / 企业微信 / Discord / Telegram）需升级到{' '}
                <Link to="/billing" className="text-purple-400 underline hover:text-purple-300">
                  Pro 套餐
                </Link>
                。
              </div>
            )}
          </div>
        )}
      </Card>

      {/* 修改密码 */}
      <Card title="修改密码" subtitle="SECURITY">
        <form onSubmit={handleChangePassword} className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Input
              id="account-current-password"
              type="password"
              allowTogglePassword
              iconType="password"
              label="当前密码"
              placeholder="输入当前密码"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete="current-password"
            />
            <Input
              id="account-new-password"
              type="password"
              allowTogglePassword
              iconType="password"
              label="新密码"
              hint="至少 8 位"
              placeholder="设置新密码"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete="new-password"
            />
          </div>
          <div className="md:max-w-md">
            <Input
              id="account-new-password-confirm"
              type="password"
              allowTogglePassword
              iconType="password"
              label="确认新密码"
              placeholder="再次输入新密码"
              value={newPasswordConfirm}
              onChange={(e) => setNewPasswordConfirm(e.target.value)}
              disabled={isSubmitting}
              autoComplete="new-password"
            />
          </div>

          {error
            ? isParsedApiError(error)
              ? <SettingsAlert title="修改失败" message={error.message} variant="error" />
              : <SettingsAlert title="修改失败" message={error} variant="error" />
            : null}
          {success ? (
            <SettingsAlert title="修改成功" message={success} variant="success" />
          ) : null}

          <Button type="submit" variant="primary" isLoading={isSubmitting}>
            <ShieldCheck className="h-4 w-4" /> 保存新密码
          </Button>
          <p className="text-xs text-secondary-text">
            修改密码后, 当前登录会失效, 你需要使用新密码重新登录。
          </p>
        </form>
      </Card>

      {/* 数据与隐私 */}
      <Card title="数据与隐私" subtitle="DATA & PRIVACY">
        <div className="space-y-3">
          {/* 导出个人数据 */}
          <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-card/60 px-4 py-3">
            <div className="space-y-1">
              <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                <Download className="h-4 w-4 text-cyan" /> 导出个人数据
              </p>
              <p className="text-xs text-secondary-text">
                将你的账户信息、自选股、历史分析、订单等数据以 JSON 格式发送至注册邮箱。
              </p>
            </div>
            <Button variant="secondary" isLoading={isExporting} onClick={() => void handleDataExport()}>
              导出
            </Button>
          </div>
          {exportMsg && (
            <SettingsAlert
              title={exportMsg.startsWith('导出失败') ? '导出失败' : '已发送'}
              message={exportMsg}
              variant={exportMsg.startsWith('导出失败') ? 'error' : 'success'}
            />
          )}
        </div>
      </Card>

      {/* 危险操作 */}
      <Card title="危险操作" subtitle="DANGER ZONE">
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-card/60 px-4 py-3">
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">退出登录</p>
              <p className="text-xs text-secondary-text">
                清空当前浏览器的登录态, 下次访问需要重新输入邮箱密码。
              </p>
            </div>
            <Button
              variant="danger-subtle"
              isLoading={isLoggingOut}
              onClick={handleLogout}
            >
              <LogOut className="h-4 w-4" /> 退出
            </Button>
          </div>

          {/* 账号注销 */}
          {deletionPending ? (
            <div className="space-y-2 rounded-xl border border-amber-500/40 bg-amber-500/5 px-4 py-3">
              <p className="flex items-center gap-1.5 text-sm font-medium text-amber-400">
                <AlertTriangle className="h-4 w-4" /> 注销申请冷静期中
              </p>
              <p className="text-xs text-secondary-text">
                申请时间：{formatDate(deletionRequestedAt)}。
                冷静期（{deletionCoolingOff} 天）结束后账号将被软删除，30 天后个人数据物理清除。
              </p>
              <div className="pt-1">
                <Button variant="secondary" isLoading={isDeletionLoading} onClick={() => void handleCancelDeletion()}>
                  取消注销申请
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-3 rounded-xl border border-red-500/30 bg-card/60 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="space-y-1">
                  <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                    <Trash2 className="h-4 w-4 text-red-400" /> 注销账号
                  </p>
                  <p className="text-xs text-secondary-text">
                    进入 {deletionCoolingOff} 天冷静期后软删除账号，30 天后物理清除个人数据；订单与发票保留 5 年。
                  </p>
                </div>
                {!showDeletionConfirm && (
                  <Button variant="danger-subtle" onClick={() => setShowDeletionConfirm(true)}>
                    注销
                  </Button>
                )}
              </div>
              {showDeletionConfirm && (
                <div className="space-y-2 border-t border-border/40 pt-3">
                  <p className="text-xs text-secondary-text">
                    确认注销请在下方输入 <strong className="text-red-400">注销账号</strong> 并点击确认。
                    此操作将立即撤销你的所有会话。
                  </p>
                  <div className="flex items-center gap-2">
                    <input
                      className="flex-1 rounded-lg border border-border bg-base px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-red-400"
                      placeholder="输入『注销账号』以确认"
                      value={deletionConfirmText}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDeletionConfirmText(e.target.value)}
                    />
                    <Button
                      variant="danger"
                      isLoading={isDeletionLoading}
                      disabled={deletionConfirmText !== '注销账号'}
                      onClick={() => void handleRequestDeletion()}
                    >
                      确认注销
                    </Button>
                    <Button variant="secondary" onClick={() => { setShowDeletionConfirm(false); setDeletionConfirmText(''); }}>
                      取消
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {deletionMsg && (
            <SettingsAlert
              title={deletionMsg.includes('失败') ? '操作失败' : '操作成功'}
              message={deletionMsg}
              variant={deletionMsg.includes('失败') ? 'error' : 'success'}
            />
          )}
        </div>
      </Card>
    </div>
  );
};

export default AccountPage;
