import type React from 'react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { Button, Input } from '../components/common';
import { BrandLogo } from '../components/common/BrandLogo';
import { SettingsAlert } from '../components/settings';
import { accountApi } from '../api/account';
import { getParsedApiError, isParsedApiError, type ParsedApiError } from '../api/error';

const ForgotPasswordPage: React.FC = () => {
  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newPasswordConfirm, setNewPasswordConfirm] = useState('');
  const [step, setStep] = useState<'request' | 'reset'>('request');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<ParsedApiError | string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    document.title = '找回密码 - AlphaLens';
  }, []);

  const handleRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (!email.trim()) {
      setError('请输入邮箱');
      return;
    }
    setIsSubmitting(true);
    try {
      await accountApi.requestPasswordReset(email.trim());
      setInfo('如果邮箱已注册，重置 token 已发送，请前往邮箱查收。');
      setStep('reset');
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (!token.trim()) {
      setError('请输入收到的重置 token');
      return;
    }
    if (newPassword !== newPasswordConfirm) {
      setError('两次输入的新密码不一致');
      return;
    }
    setIsSubmitting(true);
    try {
      await accountApi.resetPassword({
        token: token.trim(),
        newPassword,
        newPasswordConfirm,
      });
      setInfo('密码重置成功，请使用新密码登录。');
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[var(--login-bg-main)] px-4 py-12">
      {/* Background texture */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: 'linear-gradient(hsl(0 0% 100% / 0.1) 1px, transparent 1px), linear-gradient(90deg, hsl(0 0% 100% / 0.1) 1px, transparent 1px)', backgroundSize: '40px 40px' }} />
      </div>

      <div className="relative z-10 w-full max-w-[400px]">
        {/* Logo */}
        <div className="mb-8 flex items-center justify-center gap-2">
          <BrandLogo showText size="sm" textClassName="text-[var(--login-text-primary)]" />
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-[var(--login-border-card)] bg-[var(--login-bg-card)] p-7 backdrop-blur-xl">
          <div className="mb-6">
            <h2 className="text-xl font-bold tracking-tight text-[var(--login-text-primary)]">找回密码</h2>
            <p className="mt-1.5 text-sm text-[var(--login-text-secondary)]">
              {step === 'request'
                ? '输入注册邮箱，我们将发送重置链接'
                : '填写邮件中的 token 和新密码完成重置'}
            </p>
          </div>

          {step === 'request' ? (
            <form onSubmit={handleRequest} className="space-y-4">
              <Input
                id="email"
                type="email"
                appearance="login"
                label="账号邮箱"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isSubmitting}
                autoFocus
              />
              {error && (
                <SettingsAlert
                  title="无法发送"
                  message={isParsedApiError(error) ? error.message : error}
                  variant="error"
                />
              )}
              {info && <SettingsAlert title="已发送" message={info} variant="success" />}
              <Button
                type="submit"
                variant="primary"
                size="lg"
                className="mt-1 h-11 w-full rounded-xl font-semibold shadow-[0_4px_16px_hsl(var(--primary)/0.3)]"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <span className="inline-flex items-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin" /> 发送中…
                  </span>
                ) : (
                  <span>发送重置邮件</span>
                )}
              </Button>
            </form>
          ) : (
            <form onSubmit={handleReset} className="space-y-4">
              <Input
                id="token"
                type="text"
                appearance="login"
                label="重置 Token"
                placeholder="粘贴邮件中收到的 token"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                disabled={isSubmitting}
                autoFocus
              />
              <Input
                id="newPassword"
                type="password"
                appearance="login"
                allowTogglePassword
                iconType="password"
                label="新密码"
                placeholder="至少 8 位密码"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                disabled={isSubmitting}
                autoComplete="new-password"
              />
              <Input
                id="newPasswordConfirm"
                type="password"
                appearance="login"
                allowTogglePassword
                iconType="password"
                label="确认新密码"
                placeholder="再次输入新密码"
                value={newPasswordConfirm}
                onChange={(e) => setNewPasswordConfirm(e.target.value)}
                disabled={isSubmitting}
                autoComplete="new-password"
              />
              {error && (
                <SettingsAlert
                  title="重置失败"
                  message={isParsedApiError(error) ? error.message : error}
                  variant="error"
                />
              )}
              {info && <SettingsAlert title="重置成功" message={info} variant="success" />}
              <Button
                type="submit"
                variant="primary"
                size="lg"
                className="mt-1 h-11 w-full rounded-xl font-semibold shadow-[0_4px_16px_hsl(var(--primary)/0.3)]"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <span className="inline-flex items-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin" /> 重置中…
                  </span>
                ) : (
                  <span>提交重置</span>
                )}
              </Button>
            </form>
          )}
        </div>

        <div className="mt-5 text-center">
          <Link to="/login" className="text-sm text-[var(--login-text-muted)] transition-colors hover:text-[var(--login-text-secondary)]">
            ← 返回登录
          </Link>
        </div>
      </div>
    </div>
  );
};

export default ForgotPasswordPage;
