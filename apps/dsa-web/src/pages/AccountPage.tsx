import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Bell,
  CheckCircle2,
  CreditCard,
  KeyRound,
  Loader2,
  Lock,
  LogOut,
  Mail,
  ShieldCheck,
  Sparkles,
  Webhook,
} from 'lucide-react';
import { Button, Input, Card } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import { accountApi, type NotificationPrefs } from '../api/account';
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

const WEBHOOK_PLATFORM_ITEMS = [
  { type: 'feishu', label: '飞书通知', desc: '通过飞书自定义机器人接收 AI 分析报告推送。', placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/...' },
  { type: 'wecom', label: '企业微信通知', desc: '通过企业微信群机器人接收 AI 分析报告推送。', placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...' },
  { type: 'dingtalk', label: '钉钉通知', desc: '通过钉钉自定义机器人接收 AI 分析报告推送。', placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=...' },
  { type: 'discord', label: 'Discord 通知', desc: '通过 Discord Webhook 接收 AI 分析报告推送。', placeholder: 'https://discord.com/api/webhooks/...' },
  { type: 'telegram', label: 'Telegram 通知', desc: '通过 Telegram Bot 接收 AI 分析报告推送。', placeholder: 'https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=...' },
  { type: 'custom', label: '自定义 Webhook', desc: '向自定义 URL 推送 JSON 格式分析报告，可对接任意支持 Webhook 的系统。', placeholder: 'https://your-service.example.com/webhook' },
] as const;

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

  // 通知偏好
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null);
  const [prefsLoading, setPrefsLoading] = useState(false);
  const [prefsError, setPrefsError] = useState<string | null>(null);
  const [prefsSaving, setPrefsSaving] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState('');
  const [expandedWebhookType, setExpandedWebhookType] = useState<string | null>(null);

  useEffect(() => {
    document.title = '账户设置 - DSA';
  }, []);

  const loadPrefs = useCallback(async () => {
    setPrefsLoading(true);
    setPrefsError(null);
    try {
      const res = await accountApi.getNotificationPrefs();
      setPrefs(res.prefs);
      setWebhookUrl(res.prefs.webhookUrl ?? '');
    } catch (err) {
      setPrefsError(getParsedApiError(err).message);
    } finally {
      setPrefsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (userMode?.loggedIn) {
      void loadPrefs();
    }
  }, [userMode?.loggedIn, loadPrefs]);

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

  const handleSaveWebhookForType = useCallback(async (type: string) => {
    setPrefsSaving(true);
    setPrefsError(null);
    try {
      const res = await accountApi.updateNotificationPrefs({
        webhookUrl: webhookUrl.trim() || null,
        webhookType: webhookUrl.trim() ? type : null,
        clearWebhook: !webhookUrl.trim(),
      });
      setPrefs(res.prefs);
      setWebhookUrl(res.prefs.webhookUrl ?? '');
      setExpandedWebhookType(null);
    } catch (err) {
      setPrefsError(getParsedApiError(err).message);
    } finally {
      setPrefsSaving(false);
    }
  }, [webhookUrl]);

  const handleClearWebhook = useCallback(async () => {
    setPrefsSaving(true);
    setPrefsError(null);
    try {
      const res = await accountApi.updateNotificationPrefs({ clearWebhook: true });
      setPrefs(res.prefs);
      setWebhookUrl('');
      setExpandedWebhookType(null);
    } catch (err) {
      setPrefsError(getParsedApiError(err).message);
    } finally {
      setPrefsSaving(false);
    }
  }, []);

  const planName = useMemo(() => plan?.name ?? user?.plan ?? '免费会员', [plan, user]);
  const planExpiresAt = useMemo(
    () => plan?.expiresAt ?? user?.planExpiresAt ?? null,
    [plan, user]
  );
  const canEmailNotifications = Boolean(plan?.isPro);
  const dailyPushEnabled = canEmailNotifications && (prefs?.dailyPushEnabled ?? false);
  const emailEnabled = canEmailNotifications && (prefs?.emailEnabled ?? true);

  if (userMode == null || !userMode.userModeEnabled) {
    return (
      <StandardPageLayout>
        <Card title="账户设置" subtitle="ACCOUNT">
          <p className="text-sm text-secondary-text">
            当前实例未启用 To C 多用户模式 (<code className="rounded bg-hover px-1">ENABLE_USER_REGISTRATION</code>),
            账户管理仅在管理员设置页可用。
          </p>
        </Card>
      </StandardPageLayout>
    );
  }

  if (!userMode.loggedIn || user == null) {
    return (
      <StandardPageLayout>
        <Card title="账户设置" subtitle="ACCOUNT">
          <p className="text-sm text-secondary-text">请先登录后查看账户信息。</p>
          <div className="mt-4">
            <Link to="/login">
              <Button variant="primary">前往登录</Button>
            </Link>
          </div>
        </Card>
      </StandardPageLayout>
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
    <StandardPageLayout>
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
                <span className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary">
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
                  <Bell className="h-4 w-4 text-primary" /> 每日推送
                </p>
                <p className="text-xs text-secondary-text">
                  交易日收盘后，自动推送自选股 AI 分析报告到已配置的通知渠道。
                </p>
              </div>
              {canEmailNotifications ? (
                <button
                  type="button"
                  disabled={prefsSaving}
                  onClick={() => void handleTogglePref('dailyPushEnabled', !dailyPushEnabled)}
                  className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
                    dailyPushEnabled ? 'bg-primary' : 'bg-border'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      dailyPushEnabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              ) : (
                <Link
                  to="/billing"
                  className="flex shrink-0 items-center gap-1 text-xs text-purple-400 hover:text-purple-300"
                >
                  <Lock className="h-3.5 w-3.5" /> 升级 Pro
                </Link>
              )}
            </div>

            {/* 邮件通知开关 */}
            <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-card/60 px-4 py-3">
              <div className="space-y-0.5">
                <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  <Mail className="h-4 w-4 text-primary" /> 邮件通知
                </p>
                <p className="text-xs text-secondary-text">
                  通过注册邮箱接收 AI 分析报告推送。
                </p>
              </div>
              {canEmailNotifications ? (
                <button
                  type="button"
                  disabled={prefsSaving}
                  onClick={() => void handleTogglePref('emailEnabled', !emailEnabled)}
                  className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
                    emailEnabled ? 'bg-primary' : 'bg-border'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      emailEnabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              ) : (
                <Link
                  to="/billing"
                  className="flex shrink-0 items-center gap-1 text-xs text-purple-400 hover:text-purple-300"
                >
                  <Lock className="h-3.5 w-3.5" /> 升级 Pro
                </Link>
              )}
            </div>

            {/* Webhook 推送 - 每平台独立行 */}
            {WEBHOOK_PLATFORM_ITEMS.map(({ type, label, desc, placeholder }) => {
              const isActive = prefs?.webhookType === type && !!prefs?.webhookUrl;
              const isExpanded = expandedWebhookType === type;
              return (
                <div
                  key={type}
                  className={`rounded-xl border bg-card/60 px-4 py-3 space-y-3 ${
                    isActive ? 'border-primary/30' : 'border-border/60'
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="space-y-0.5">
                      <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                        <Webhook className="h-4 w-4 text-purple-400" /> {label}
                      </p>
                      <p className="text-xs text-secondary-text">{desc}</p>
                      {plan?.canWebhook && isActive && !isExpanded && (
                        <button
                          type="button"
                          className="mt-0.5 text-xs text-purple-400 hover:text-purple-300"
                          onClick={() => {
                            setExpandedWebhookType(type);
                            setWebhookUrl(prefs?.webhookUrl ?? '');
                          }}
                        >
                          编辑 Webhook URL
                        </button>
                      )}
                    </div>
                    {plan?.canWebhook ? (
                      <button
                        type="button"
                        disabled={prefsSaving}
                        onClick={() => {
                          if (isActive) {
                            void handleClearWebhook();
                          } else if (isExpanded) {
                            setExpandedWebhookType(null);
                            setWebhookUrl('');
                          } else {
                            setExpandedWebhookType(type);
                            setWebhookUrl('');
                          }
                        }}
                        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
                          isActive ? 'bg-primary' : 'bg-border'
                        }`}
                      >
                        <span
                          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                            isActive ? 'translate-x-6' : 'translate-x-1'
                          }`}
                        />
                      </button>
                    ) : (
                      <Link
                        to="/billing"
                        className="flex shrink-0 items-center gap-1 text-xs text-purple-400 hover:text-purple-300"
                      >
                        <Lock className="h-3.5 w-3.5" /> 升级 Pro
                      </Link>
                    )}
                  </div>
                  {plan?.canWebhook && isExpanded && (
                    <div className="flex flex-col gap-2 pt-1 sm:flex-row sm:items-end">
                      <div className="flex-1">
                        <Input
                          type="url"
                          label="Webhook URL"
                          placeholder={placeholder}
                          value={webhookUrl}
                          onChange={(e) => setWebhookUrl(e.target.value)}
                          disabled={prefsSaving}
                        />
                      </div>
                      <div className="flex gap-2">
                        <Button
                          variant="primary"
                          isLoading={prefsSaving}
                          disabled={!webhookUrl.trim() || prefsSaving}
                          onClick={() => void handleSaveWebhookForType(type)}
                        >
                          保存
                        </Button>
                        <Button
                          variant="outline"
                          disabled={prefsSaving}
                          onClick={() => { setExpandedWebhookType(null); setWebhookUrl(''); }}
                        >
                          取消
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
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

      <div className="flex justify-center">
        <Button
          variant="danger-subtle"
          isLoading={isLoggingOut}
          onClick={handleLogout}
        >
          <LogOut className="h-4 w-4" /> 退出登录
        </Button>
      </div>
    </StandardPageLayout>
  );
};

export default AccountPage;
