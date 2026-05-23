import type React from 'react';
import { useEffect } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import HomePage from './pages/HomePage';
import BacktestPage from './pages/BacktestPage';
import SettingsPage from './pages/SettingsPage';
import LoginPage from './pages/LoginPage';
import NotFoundPage from './pages/NotFoundPage';
import ChatPage from './pages/ChatPage';
import PortfolioPage from './pages/PortfolioPage';
import UserAuthPage from './pages/UserAuthPage';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import AccountPage from './pages/AccountPage';
import WatchlistPage from './pages/WatchlistPage';
import BillingPage from './pages/BillingPage';
import VerifyEmailPage from './pages/VerifyEmailPage';
import OnboardingPage from './pages/OnboardingPage';
import OrdersPage from './pages/OrdersPage';
import InvoicesPage from './pages/InvoicesPage';
import AdminPage from './pages/AdminPage';
import NoticesPage from './pages/NoticesPage';
import HelpPage from './pages/HelpPage';
import TermsPage from './pages/legal/TermsPage';
import PrivacyPage from './pages/legal/PrivacyPage';
import RiskDisclosurePage from './pages/legal/RiskDisclosurePage';
import { ApiErrorAlert, Button, QuotaExceededDialog, RenewalBanner, Shell } from './components/common';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { useAgentChatStore } from './stores/agentChatStore';
import './App.css';

const AppContent: React.FC = () => {
  const location = useLocation();
  const { authEnabled, loggedIn, userMode, effectiveLoggedIn, isLoading, loadError, refreshStatus } = useAuth();
  const userModeEnabled = Boolean(userMode?.userModeEnabled);
  const userLoggedIn = Boolean(userMode?.loggedIn);
  const userIsAdmin = Boolean(userMode?.user?.isAdmin);
  const canAccessSystemSettings = !userModeEnabled || userIsAdmin;

  useEffect(() => {
    useAgentChatStore.getState().setCurrentRoute(location.pathname);
  }, [location.pathname]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-base">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-base px-4">
        <div className="w-full max-w-lg">
          <ApiErrorAlert error={loadError} />
        </div>
        <Button
          type="button"
          variant="primary"
          onClick={() => void refreshStatus()}
        >
          重试
        </Button>
      </div>
    );
  }

  // Public routes available even when not logged in (so the user mode unlocks
  // /register, /forgot-password, /verify-email without 401 redirect loops).
  // /legal/* 协议三件套对所有访客开放, 不参与登录跳转。
  const publicPaths = new Set(['/login', '/register', '/forgot-password', '/verify-email']);
  const isPublicPath = publicPaths.has(location.pathname);
  const isNoticesPath = location.pathname.startsWith('/notices');
  const isLegalPath = location.pathname.startsWith('/legal/');

  if (!effectiveLoggedIn && !isPublicPath && !isLegalPath && !isNoticesPath) {
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  if (effectiveLoggedIn && isPublicPath) {
    return <Navigate to="/" replace />;
  }

  // Pick the right login page: prefer the To C email/password form when user
  // mode is enabled and the current request hasn't logged into a user yet.
  const showUserAuth = userModeEnabled && !userLoggedIn;
  const loginElement = showUserAuth ? <UserAuthPage mode="login" /> : <LoginPage />;
  const adminAuthBlock = authEnabled && !loggedIn;

  return (
    <>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route
            path="/settings"
            element={
              canAccessSystemSettings ? <SettingsPage /> : <Navigate to="/account" replace />
            }
          />
          <Route
            path="/account"
            element={
              userModeEnabled ? <AccountPage /> : <Navigate to="/settings" replace />
            }
          />
          <Route
            path="/watchlist"
            element={
              userModeEnabled ? <WatchlistPage /> : <Navigate to="/settings" replace />
            }
          />
          <Route
            path="/billing"
            element={
              userModeEnabled ? <BillingPage /> : <Navigate to="/settings" replace />
            }
          />
          <Route
            path="/account/orders"
            element={
              userModeEnabled ? <OrdersPage /> : <Navigate to="/settings" replace />
            }
          />
          <Route
            path="/account/invoices"
            element={
              userModeEnabled ? <InvoicesPage /> : <Navigate to="/settings" replace />
            }
          />
          <Route
            path="/admin"
            element={
              userModeEnabled ? <AdminPage /> : <Navigate to="/settings" replace />
            }
          />
          <Route path="/notices" element={<NoticesPage />} />
          <Route path="/help" element={<HelpPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
        <Route path="/login" element={loginElement} />
        <Route
          path="/register"
          element={
            userModeEnabled && !adminAuthBlock ? (
              <UserAuthPage mode="register" />
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route
          path="/forgot-password"
          element={userModeEnabled ? <ForgotPasswordPage /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/verify-email"
          element={userModeEnabled ? <VerifyEmailPage /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/onboarding"
          element={userModeEnabled ? <OnboardingPage /> : <Navigate to="/login" replace />}
        />
        {/* Phase 6 协议三件套, 公开访问 */}
        <Route path="/legal/terms" element={<TermsPage />} />
        <Route path="/legal/privacy" element={<PrivacyPage />} />
        <Route path="/legal/risk-disclosure" element={<RiskDisclosurePage />} />
      </Routes>
      {/* Plan 到期 / 续费提示, 仅在 renewal.willExpireSoon || renewal.expired 时显示 */}
      <RenewalBanner />
      {/* 全局配额超限对话框, 监听 axios interceptor 派发的 quota_exceeded 事件 */}
      <QuotaExceededDialog />
    </>
  );
};

const App: React.FC = () => {
  return (
    <Router>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </Router>
  );
};

export default App;
