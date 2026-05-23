import type React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ArrowLeft, FileText, ShieldCheck, AlertTriangle } from 'lucide-react';

const NAV_ITEMS = [
  { to: '/legal/terms', label: '用户服务协议', icon: FileText },
  { to: '/legal/privacy', label: '隐私政策', icon: ShieldCheck },
  { to: '/legal/risk-disclosure', label: '投资风险揭示书', icon: AlertTriangle },
];

interface LegalPageLayoutProps {
  title: string;
  version: string;
  effectiveDate: string;
  children: React.ReactNode;
}

/**
 * 协议类静态页通用布局: 顶部三件套切换 + 主体内容 + 底部回到登录的链接。
 *
 * 不依赖 Shell, 因此未登录用户也能访问 (/legal/* 已在 App.tsx 中注册为公共路由)。
 */
const LegalPageLayout: React.FC<LegalPageLayoutProps> = ({
  title,
  version,
  effectiveDate,
  children,
}) => {
  const location = useLocation();
  return (
    <div className="min-h-screen bg-base text-foreground">
      <header className="border-b border-border/60 bg-card/40 backdrop-blur">
        <div className="mx-auto flex max-w-4xl flex-col gap-3 px-4 py-4 md:flex-row md:items-center md:justify-between">
          <Link
            to="/login"
            className="inline-flex items-center gap-1 text-sm text-secondary-text hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" /> 返回登录
          </Link>
          <nav className="flex flex-wrap items-center gap-1 text-xs">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.to;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={
                    'inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 transition-colors ' +
                    (isActive
                      ? 'border-primary/40 bg-primary/10 text-primary'
                      : 'border-border/60 bg-card/40 text-secondary-text hover:border-primary/30 hover:text-foreground')
                  }
                >
                  <Icon className="h-3.5 w-3.5" /> {item.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-10">
        <div className="mb-8 space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="text-xs text-secondary-text">
            版本 <span className="font-mono">{version}</span> · 生效日期 {effectiveDate}
          </p>
        </div>

        <article className="prose-legal space-y-5 text-sm leading-relaxed text-foreground/90">
          {children}
        </article>

        <footer className="mt-12 border-t border-border/60 pt-6 text-xs text-secondary-text">
          <p>
            本服务基于 AI 模型生成观点，<strong className="text-foreground/90">不构成投资建议</strong>。
            投资有风险，入市需谨慎。
          </p>
          <p className="mt-2">
            如对本协议有疑问，可发送邮件至{' '}
            <a className="text-primary hover:underline" href="mailto:support@example.com">
              support@example.com
            </a>{' '}
            联系客服。
          </p>
        </footer>
      </main>
    </div>
  );
};

export default LegalPageLayout;
