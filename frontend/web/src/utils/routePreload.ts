const pageLoaders = {
  home: () => import('../pages/HomePage'),
  chat: () => import('../pages/ChatPage'),
  tasks: () => import('../pages/TasksPage'),
  portfolio: () => import('../pages/PortfolioPage'),
  backtest: () => import('../pages/BacktestPage'),
  settings: () => import('../pages/SettingsPage'),
  usage: () => import('../pages/UsagePage'),
  account: () => import('../pages/AccountPage'),
  watchlist: () => import('../pages/WatchlistPage'),
  alerts: () => import('../pages/AlertsPage'),
  billing: () => import('../pages/BillingPage'),
  orders: () => import('../pages/OrdersPage'),
  invoices: () => import('../pages/InvoicesPage'),
  admin: () => import('../pages/AdminPage'),
  notices: () => import('../pages/NoticesPage'),
  researchReports: () => import('../pages/ResearchReportsPage'),
  researchReportsStudio: () => import('../pages/ResearchReportsStudioPage'),
  help: () => import('../pages/HelpPage'),
  login: () => import('../pages/LoginPage'),
  userAuth: () => import('../pages/UserAuthPage'),
  forgotPassword: () => import('../pages/ForgotPasswordPage'),
  verifyEmail: () => import('../pages/VerifyEmailPage'),
  onboarding: () => import('../pages/OnboardingPage'),
  notFound: () => import('../pages/NotFoundPage'),
  terms: () => import('../pages/legal/TermsPage'),
  privacy: () => import('../pages/legal/PrivacyPage'),
  riskDisclosure: () => import('../pages/legal/RiskDisclosurePage'),
  stockDetail: () => import('../pages/StockDetailPage'),
} as const;

export const loadHomePage = pageLoaders.home;
export const loadChatPage = pageLoaders.chat;
export const loadTasksPage = pageLoaders.tasks;
export const loadPortfolioPage = pageLoaders.portfolio;
export const loadBacktestPage = pageLoaders.backtest;
export const loadSettingsPage = pageLoaders.settings;
export const loadUsagePage = pageLoaders.usage;
export const loadAccountPage = pageLoaders.account;
export const loadWatchlistPage = pageLoaders.watchlist;
export const loadAlertsPage = pageLoaders.alerts;
export const loadBillingPage = pageLoaders.billing;
export const loadOrdersPage = pageLoaders.orders;
export const loadInvoicesPage = pageLoaders.invoices;
export const loadAdminPage = pageLoaders.admin;
export const loadNoticesPage = pageLoaders.notices;
export const loadResearchReportsPage = pageLoaders.researchReports;
export const loadResearchReportsStudioPage = pageLoaders.researchReportsStudio;
export const loadHelpPage = pageLoaders.help;
export const loadLoginPage = pageLoaders.login;
export const loadUserAuthPage = pageLoaders.userAuth;
export const loadForgotPasswordPage = pageLoaders.forgotPassword;
export const loadVerifyEmailPage = pageLoaders.verifyEmail;
export const loadOnboardingPage = pageLoaders.onboarding;
export const loadNotFoundPage = pageLoaders.notFound;
export const loadTermsPage = pageLoaders.terms;
export const loadPrivacyPage = pageLoaders.privacy;
export const loadRiskDisclosurePage = pageLoaders.riskDisclosure;
export const loadStockDetailPage = pageLoaders.stockDetail;

const preloadByPath: Record<string, () => Promise<unknown>> = {
  '/': pageLoaders.home,
  '/chat': pageLoaders.chat,
  '/tasks': pageLoaders.tasks,
  '/portfolio': pageLoaders.portfolio,
  '/backtest': pageLoaders.backtest,
  '/settings': pageLoaders.settings,
  '/usage': pageLoaders.usage,
  '/account': pageLoaders.account,
  '/watchlist': pageLoaders.watchlist,
  '/alerts': pageLoaders.alerts,
  '/billing': pageLoaders.billing,
  '/account/orders': pageLoaders.orders,
  '/account/invoices': pageLoaders.invoices,
  '/admin': pageLoaders.admin,
  '/notices': pageLoaders.notices,
  '/research-reports': pageLoaders.researchReports,
  '/research-reports/studio': pageLoaders.researchReportsStudio,
  '/help': pageLoaders.help,
  '/login': pageLoaders.login,
  '/register': pageLoaders.userAuth,
  '/forgot-password': pageLoaders.forgotPassword,
  '/verify-email': pageLoaders.verifyEmail,
  '/onboarding': pageLoaders.onboarding,
  '/legal/terms': pageLoaders.terms,
  '/legal/privacy': pageLoaders.privacy,
  '/legal/risk-disclosure': pageLoaders.riskDisclosure,
};

const preloadedPaths = new Set<string>();

export const preloadRouteModule = (path: string): void => {
  const loader = preloadByPath[path];
  if (!loader || preloadedPaths.has(path)) return;
  preloadedPaths.add(path);
  void loader().catch(() => {
    preloadedPaths.delete(path);
  });
};
