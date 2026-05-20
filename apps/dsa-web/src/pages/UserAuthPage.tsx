import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { BarChart3, Loader2, ShieldCheck, Sparkles, TrendingUp, Zap } from 'lucide-react';
import { Button, Input } from '../components/common';
import { SettingsAlert } from '../components/settings';
import { isParsedApiError, type ParsedApiError } from '../api/error';
import { useAuth } from '../hooks';

type Mode = 'login' | 'register';

const UserAuthPage: React.FC<{ mode: Mode }> = ({ mode }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const { loginWithEmail, registerWithEmail, userMode } = useAuth();

  const rawRedirect = searchParams.get('redirect') ?? '';
  const redirect =
    rawRedirect.startsWith('/') && !rawRedirect.startsWith('//') ? rawRedirect : '/';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [termsAgreed, setTermsAgreed] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<ParsedApiError | string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    document.title = mode === 'register' ? '注册 - DSA' : '登录 - DSA';
  }, [mode]);

  const termsVersion = userMode?.termsVersion;

  const inviteRequired = useMemo(
    () => mode === 'register' && Boolean(userMode?.inviteRequired),
    [mode, userMode]
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInfo(null);

    if (!email.trim() || !password) {
      setError('请填写邮箱和密码');
      return;
    }
    if (mode === 'register' && password !== passwordConfirm) {
      setError('两次输入的密码不一致');
      return;
    }
    if (mode === 'register' && !termsAgreed) {
      setError('请阅读并同意《用户服务协议》《隐私政策》《投资风险揭示书》');
      return;
    }

    setIsSubmitting(true);
    try {
      if (mode === 'login') {
        const res = await loginWithEmail(email.trim(), password);
        if (res.success) {
          navigate(redirect, { replace: true });
        } else {
          setError(res.error ?? '登录失败');
        }
      } else {
        const res = await registerWithEmail({
          email: email.trim(),
          password,
          passwordConfirm,
          inviteCode: inviteRequired ? inviteCode.trim() : undefined,
          termsAgreed,
          termsVersion,
        });
        if (res.success) {
          setInfo('注册成功！请前往邮箱完成验证，然后返回登录。');
        } else {
          setError(res.error ?? '注册失败');
        }
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const FEATURES = [
    { icon: TrendingUp, title: 'AI 智能分析', desc: '深度解析 A 股、港股、美股，三市联动' },
    { icon: Zap, title: '实时策略回测', desc: '多维度策略验证，助你找到最优买卖时机' },
    { icon: ShieldCheck, title: '风险预警监控', desc: '智能持仓监控，关键风险第一时间提醒' },
    { icon: Sparkles, title: '自然语言问股', desc: '用中文直接询问任何股票分析问题' },
  ];

  return (
    <div className="flex min-h-screen bg-[var(--login-bg-main)]">
      {/* Left: Branding Panel */}
      <div className="relative hidden lg:flex lg:w-[52%] xl:w-[55%] flex-col justify-between overflow-hidden p-10">
        {/* Background gradients */}
        <div aria-hidden="true" className="pointer-events-none absolute inset-0">
          <div className="absolute -left-[20%] -top-[20%] h-[70%] w-[70%] rounded-full opacity-20" style={{ background: 'radial-gradient(circle, hsl(var(--primary)) 0%, transparent 70%)' }} />
          <div className="absolute -bottom-[10%] -right-[10%] h-[50%] w-[50%] rounded-full opacity-10" style={{ background: 'radial-gradient(circle, hsl(247 84% 66%) 0%, transparent 70%)' }} />
          <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: 'linear-gradient(hsl(0 0% 100% / 0.08) 1px, transparent 1px), linear-gradient(90deg, hsl(0 0% 100% / 0.08) 1px, transparent 1px)', backgroundSize: '40px 40px' }} />
        </div>

        {/* Top: Logo */}
        <div className="relative z-10 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-gradient shadow-[0_8px_24px_hsl(var(--primary)/0.4)]">
            <BarChart3 className="h-5 w-5 text-white" />
          </div>
          <div>
            <p className="text-lg font-bold tracking-tight text-[var(--login-text-primary)]">DSA</p>
            <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--login-text-muted)]">Daily Stock Analytics</p>
          </div>
        </div>

        {/* Center: Hero text */}
        <div className="relative z-10 space-y-6">
          <div className="space-y-3">
            <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
              <Sparkles className="h-3 w-3" />
              AI 驱动的智能股票分析平台
            </div>
            <h1 className="text-4xl font-bold leading-[1.15] tracking-tight text-[var(--login-text-primary)] xl:text-5xl">
              让 AI 成为你的
              <br />
              <span className="bg-gradient-to-r from-primary to-[hsl(247_84%_72%)] bg-clip-text text-transparent">
                专属分析师
              </span>
            </h1>
            <p className="max-w-md text-base leading-relaxed text-[var(--login-text-secondary)]">
              覆盖 A 股、港股、美股全市场，AI 深度分析、策略回测、风险监控，一站式投资决策支持。
            </p>
          </div>

          {/* Feature list */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {FEATURES.map(({ icon: Icon, title, desc }) => (
              <div key={title} className="flex items-start gap-3 rounded-xl border border-[var(--login-border-card)] bg-[var(--login-bg-card)] p-3.5 backdrop-blur-sm">
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary">
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-[var(--login-text-primary)]">{title}</p>
                  <p className="mt-0.5 text-xs leading-relaxed text-[var(--login-text-secondary)]">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Bottom: Disclaimer */}
        <div className="relative z-10">
          <p className="text-xs text-[var(--login-text-muted)]">© {new Date().getFullYear()} DSA · AI 分析结果仅供参考，不构成投资建议</p>
        </div>
      </div>

      {/* Right: Auth Form */}
      <div className="flex flex-1 flex-col items-center justify-center px-6 py-12 lg:px-10">
        {/* Mobile logo */}
        <div className="mb-8 flex items-center gap-2 lg:hidden">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary-gradient">
            <BarChart3 className="h-4.5 w-4.5 text-white" />
          </div>
          <span className="text-lg font-bold text-[var(--login-text-primary)]">DSA</span>
        </div>

        <div className="w-full max-w-[400px]">
          {/* Form header */}
          <div className="mb-8">
            <h2 className="text-2xl font-bold tracking-tight text-[var(--login-text-primary)]">
              {mode === 'login' ? '欢迎回来' : '创建账号'}
            </h2>
            <p className="mt-2 text-sm text-[var(--login-text-secondary)]">
              {mode === 'login'
                ? '登录你的 DSA 账号，继续 AI 股票分析之旅'
                : '注册免费账号，开启 AI 智能选股体验'}
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              id="email"
              type="email"
              appearance="login"
              label="邮箱地址"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isSubmitting}
              autoComplete={mode === 'login' ? 'email' : 'new-email'}
              autoFocus
            />
            <Input
              id="password"
              type="password"
              appearance="login"
              allowTogglePassword
              iconType="password"
              label="密码"
              placeholder={mode === 'register' ? '至少 8 位密码' : '请输入密码'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
            {mode === 'register' && (
              <Input
                id="passwordConfirm"
                type="password"
                appearance="login"
                allowTogglePassword
                iconType="password"
                label="确认密码"
                placeholder="再次输入密码"
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                disabled={isSubmitting}
                autoComplete="new-password"
              />
            )}
            {inviteRequired && (
              <Input
                id="inviteCode"
                type="text"
                appearance="login"
                label="邀请码"
                placeholder="请输入邀请码"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                disabled={isSubmitting}
              />
            )}

            {mode === 'register' && (
              <label className="flex items-start gap-2.5 rounded-xl border border-[var(--login-border-card)] bg-[var(--login-bg-card)] p-3 text-xs leading-relaxed text-[var(--login-text-secondary)] cursor-pointer hover:bg-[var(--login-bg-card)]/80 transition-colors">
                <input
                  id="termsAgreed"
                  type="checkbox"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 cursor-pointer rounded border-white/25 bg-white/10 text-primary accent-primary focus:ring-1 focus:ring-primary/40"
                  checked={termsAgreed}
                  onChange={(e) => setTermsAgreed(e.target.checked)}
                  disabled={isSubmitting}
                />
                <span>
                  已阅读并同意{' '}
                  <Link to="/legal/terms" target="_blank" className="text-primary/90 hover:text-primary underline underline-offset-2">
                    《服务协议》
                  </Link>
                  {' '}
                  <Link to="/legal/privacy" target="_blank" className="text-primary/90 hover:text-primary underline underline-offset-2">
                    《隐私政策》
                  </Link>
                  {' '}
                  <Link to="/legal/risk-disclosure" target="_blank" className="text-primary/90 hover:text-primary underline underline-offset-2">
                    《风险揭示书》
                  </Link>
                  ，AI 分析不构成投资建议。
                </span>
              </label>
            )}

            {error && (
              <SettingsAlert
                title={mode === 'login' ? '登录失败' : '注册失败'}
                message={isParsedApiError(error) ? error.message : error}
                variant="error"
              />
            )}
            {info && <SettingsAlert title="操作成功" message={info} variant="success" />}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              className="mt-2 h-11 w-full rounded-xl text-sm font-semibold shadow-[0_4px_20px_hsl(var(--primary)/0.35)] transition-all hover:shadow-[0_6px_24px_hsl(var(--primary)/0.45)] hover:scale-[1.01]"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {mode === 'login' ? '登录中…' : '注册中…'}
                </span>
              ) : (
                <span>{mode === 'login' ? '立即登录' : '创建账号'}</span>
              )}
            </Button>
          </form>

          {/* Bottom links */}
          <div className="mt-6 flex items-center justify-between text-sm text-[var(--login-text-muted)]">
            {mode === 'login' ? (
              <>
                <Link to="/forgot-password" className="hover:text-[var(--login-text-secondary)] transition-colors">
                  忘记密码？
                </Link>
                {userMode?.registrationEnabled ? (
                  <Link
                    to={`/register${location.search}`}
                    className="text-primary/80 hover:text-primary transition-colors font-medium"
                  >
                    没有账号？注册
                  </Link>
                ) : null}
              </>
            ) : (
              <Link
                to={`/login${location.search}`}
                className="text-primary/80 hover:text-primary transition-colors font-medium"
              >
                ← 返回登录
              </Link>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default UserAuthPage;
