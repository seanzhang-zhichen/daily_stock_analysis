import type React from 'react';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CheckCircle2, Loader2, MailCheck, XCircle } from 'lucide-react';
import { Button } from '../components/common';
import { SettingsAlert } from '../components/settings';
import { accountApi } from '../api/account';
import { getParsedApiError, isParsedApiError, type ParsedApiError } from '../api/error';

const VerifyEmailPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') ?? '';

  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>(
    token ? 'loading' : 'idle'
  );
  const [error, setError] = useState<ParsedApiError | string | null>(null);

  useEffect(() => {
    document.title = '邮箱验证 - DSA';
  }, []);

  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;
    accountApi
      .verifyEmail(token)
      .then(() => {
        if (!cancelled) setStatus('success');
      })
      .catch((err) => {
        if (!cancelled) {
          setStatus('error');
          setError(getParsedApiError(err));
        }
      });
    return () => { cancelled = true; };
  }, [token]);

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[var(--login-bg-main)] px-4 py-12">
      {/* Background decoration */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0">
        <div className="absolute -left-[20%] -top-[20%] h-[60%] w-[60%] rounded-full opacity-15" style={{ background: 'radial-gradient(circle, hsl(var(--primary)) 0%, transparent 70%)' }} />
        <div className="absolute -bottom-[10%] -right-[10%] h-[40%] w-[40%] rounded-full opacity-10" style={{ background: 'radial-gradient(circle, hsl(247 84% 66%) 0%, transparent 70%)' }} />
      </div>

      <div className="relative z-10 w-full max-w-[400px]">
        {/* Logo */}
        <div className="mb-8 flex items-center justify-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary-gradient shadow-[0_8px_24px_hsl(var(--primary)/0.35)]">
            <MailCheck className="h-4.5 w-4.5 text-white" />
          </div>
          <span className="text-lg font-bold tracking-tight text-[var(--login-text-primary)]">DSA</span>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-[var(--login-border-card)] bg-[var(--login-bg-card)] p-7 backdrop-blur-xl">
          <div className="mb-6">
            <h2 className="text-xl font-bold tracking-tight text-[var(--login-text-primary)]">邮箱验证</h2>
            <p className="mt-1.5 text-sm text-[var(--login-text-secondary)]">验证你的邮箱地址以激活账号</p>
          </div>

          {!token && (
            <div className="space-y-4">
              <SettingsAlert
                title="链接无效"
                message="未找到验证 token，请检查邮件中的链接是否完整。"
                variant="error"
              />
              <p className="text-sm text-[var(--login-text-secondary)]">
                如需重新发送验证邮件，请登录账号后在账户设置中操作；或联系站点管理员。
              </p>
            </div>
          )}

          {token && status === 'idle' && (
            <p className="text-sm text-[var(--login-text-secondary)]">正在准备验证…</p>
          )}

          {status === 'loading' && (
            <div className="flex items-center gap-3 rounded-xl border border-[var(--login-border-card)] bg-[var(--login-bg-card)] p-4">
              <Loader2 className="h-5 w-5 shrink-0 animate-spin text-[var(--login-text-primary)]" />
              <span className="text-sm text-[var(--login-text-secondary)]">正在验证邮箱，请稍候…</span>
            </div>
          )}

          {status === 'success' && (
            <div className="space-y-4">
              <div className="flex items-start gap-3 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.08] p-4">
                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />
                <div>
                  <p className="text-sm font-semibold text-emerald-300">邮箱验证成功！</p>
                  <p className="mt-1 text-xs text-[var(--login-text-muted)]">你的账号已激活，现在可以登录使用 DSA。</p>
                </div>
              </div>
              <Link to="/login" className="block">
                <Button
                  variant="primary"
                  size="lg"
                  className="h-11 w-full rounded-xl font-semibold shadow-[0_4px_16px_hsl(var(--primary)/0.3)]"
                >
                  前往登录
                </Button>
              </Link>
            </div>
          )}

          {status === 'error' && (
            <div className="space-y-3">
              <div className="flex items-center gap-3 rounded-xl border border-red-500/20 bg-red-500/[0.08] p-4">
                <XCircle className="h-5 w-5 shrink-0 text-red-400" />
                <p className="text-sm font-semibold text-red-300">验证失败</p>
              </div>
              {error && (
                <SettingsAlert
                  title="错误详情"
                  message={isParsedApiError(error) ? error.message : error}
                  variant="error"
                />
              )}
              <p className="text-xs text-[var(--login-text-muted)]">
                可能原因：链接已过期（有效期 24 小时）、已使用过或链接不完整。请重新注册或联系站点管理员。
              </p>
            </div>
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

export default VerifyEmailPage;
