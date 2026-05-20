import React, { useEffect, useState } from 'react';
import { motion } from 'motion/react';
import {
  BarChart3,
  Bell,
  BriefcaseBusiness,
  HelpCircle,
  Home,
  LogOut,
  MessageSquareQuote,
  Settings2,
  UserCircle2,
} from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { noticesApi } from '../../api/notices';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { cn } from '../../utils/cn';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { StatusDot } from '../common/StatusDot';
import { ThemeToggle } from '../theme/ThemeToggle';
import { QuotaIndicator } from './QuotaIndicator';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
};

type NavItem = {
  key: string;
  label: string;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  badge?: 'completion';
};

const BASE_NAV_ITEMS: NavItem[] = [
  { key: 'home', label: '首页', to: '/', icon: Home, exact: true },
  { key: 'chat', label: '问股', to: '/chat', icon: MessageSquareQuote, badge: 'completion' },
  { key: 'portfolio', label: '持仓', to: '/portfolio', icon: BriefcaseBusiness },
  { key: 'backtest', label: '回测', to: '/backtest', icon: BarChart3 },
  { key: 'settings', label: '设置', to: '/settings', icon: Settings2 },
  { key: 'notices', label: '公告', to: '/notices', icon: Bell },
];

const ACCOUNT_NAV_ITEM: NavItem = {
  key: 'account',
  label: '我的',
  to: '/account',
  icon: UserCircle2,
};

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate }) => {
  const { authEnabled, userMode, logout } = useAuth();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [noticeCount, setNoticeCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const fetchCount = async () => {
      try {
        const count = await noticesApi.getUnreadCount();
        if (!cancelled) setNoticeCount(count);
      } catch {
        /* ignore */
      }
    };
    void fetchCount();
    return () => { cancelled = true; };
  }, []);

  const userModeEnabled = Boolean(userMode?.userModeEnabled);
  const userLoggedIn = Boolean(userMode?.loggedIn);
  const navItems: NavItem[] = userModeEnabled && userLoggedIn
    ? [...BASE_NAV_ITEMS, ACCOUNT_NAV_ITEM]
    : BASE_NAV_ITEMS;

  return (
    <div className="ui-sidebar">
      {/* Brand / Logo */}
      <div className={cn(
        'ui-sidebar-brand',
        collapsed ? 'justify-center px-0' : ''
      )}>
        <div className="group ui-sidebar-brand-mark">
          <BarChart3 className="h-4.5 w-4.5 transition-transform duration-200 group-hover:rotate-6" />
        </div>
        {!collapsed ? (
          <div className="min-w-0 flex-1">
            <p className="ui-sidebar-brand-title">
              DSA
            </p>
            <p className="ui-sidebar-brand-subtitle">
              Stock Analytics
            </p>
          </div>
        ) : null}
      </div>

      {/* Divider */}
      <div className={cn('ui-sidebar-divider mb-1', collapsed ? 'mx-0' : 'mx-1')} />

      {/* Main Navigation */}
      <nav className="ui-sidebar-nav" aria-label="主导航">
        {navItems.map(({ key, label, to, icon: Icon, exact, badge }) => (
          <NavLink
            key={key}
            to={to}
            end={exact}
            onClick={onNavigate}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                'group ui-sidebar-link',
                collapsed ? 'justify-center px-0' : '',
                isActive ? 'ui-sidebar-link-active' : ''
              )
            }
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <motion.div
                    layoutId="activeIndicator"
                    className="ui-sidebar-active-indicator"
                    initial={{ opacity: 0, scaleY: 0.5 }}
                    animate={{ opacity: 1, scaleY: 1 }}
                    transition={{ duration: 0.2, ease: 'easeOut' }}
                  />
                )}
                <Icon className={cn(
                  'ui-sidebar-icon h-4.5 w-4.5',
                  collapsed ? '' : 'ml-0.5'
                )} />
                {!collapsed ? (
                  <span className="truncate font-[450]">{label}</span>
                ) : null}
                {badge === 'completion' && completionBadge ? (
                  <StatusDot
                    tone="info"
                    data-testid="chat-completion-badge"
                    className={cn(
                      'ui-sidebar-status',
                      collapsed ? 'absolute right-1.5 top-1.5' : 'absolute right-2.5'
                    )}
                    aria-label="问股有新消息"
                  />
                ) : null}
                {key === 'notices' && noticeCount > 0 ? (
                  <span
                    className={cn(
                      'ui-sidebar-badge',
                      collapsed ? 'right-0.5 top-0.5 h-4 min-w-4 text-[9px]' : 'right-2'
                    )}
                    aria-label={`${noticeCount} 条公告`}
                  >
                    {noticeCount > 99 ? '99+' : noticeCount}
                  </span>
                ) : null}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Quota Indicator */}
      <QuotaIndicator collapsed={collapsed} onNavigate={onNavigate} />

      {/* Divider */}
      <div className={cn('ui-sidebar-divider my-1', collapsed ? 'mx-0' : 'mx-1')} />

      {/* Bottom actions */}
      <div className="flex flex-col gap-0.5">
        <ThemeToggle variant="nav" collapsed={collapsed} />

        {/* Help link */}
        <a
          href={import.meta.env.VITE_SUPPORT_URL as string | undefined ?? '#'}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            'ui-sidebar-action',
            collapsed ? 'justify-center px-0' : ''
          )}
          title="帮助与反馈"
        >
          <HelpCircle className="h-4.5 w-4.5 shrink-0" />
          {!collapsed ? <span className="font-[450]">帮助</span> : null}
        </a>

        {/* Logout (admin mode only) */}
        {authEnabled && !(userModeEnabled && userLoggedIn) ? (
          <button
            type="button"
            onClick={() => setShowLogoutConfirm(true)}
            className={cn(
              'ui-sidebar-action',
              collapsed ? 'justify-center px-0' : ''
            )}
          >
            <LogOut className="h-4.5 w-4.5 shrink-0" />
            {!collapsed ? <span className="font-[450]">退出</span> : null}
          </button>
        ) : null}
      </div>

      <ConfirmDialog
        isOpen={showLogoutConfirm}
        title="退出登录"
        message="确认退出当前登录状态吗？退出后需要重新输入密码。"
        confirmText="确认退出"
        cancelText="取消"
        isDanger
        onConfirm={() => {
          setShowLogoutConfirm(false);
          onNavigate?.();
          void logout();
        }}
        onCancel={() => setShowLogoutConfirm(false)}
      />
    </div>
  );
};
