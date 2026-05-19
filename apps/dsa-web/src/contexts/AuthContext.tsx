import type React from 'react';
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { createParsedApiError, getParsedApiError, type ParsedApiError } from '../api/error';
import { authApi } from '../api/auth';
import { accountApi, type AccountStatusResponse } from '../api/account';
import { useStockPoolStore } from '../stores';

type AuthContextValue = {
  // Legacy admin auth (single-admin password) state.
  authEnabled: boolean;
  loggedIn: boolean;
  passwordSet: boolean;
  passwordChangeable: boolean;
  setupState: 'enabled' | 'password_retained' | 'no_password';
  // To C user mode state.
  userMode: AccountStatusResponse | null;
  isLoading: boolean;
  loadError: ParsedApiError | null;
  // Effective "logged in" considering both auth modes.
  effectiveLoggedIn: boolean;
  login: (password: string, passwordConfirm?: string) => Promise<{ success: boolean; error?: ParsedApiError }>;
  loginWithEmail: (email: string, password: string) => Promise<{ success: boolean; error?: ParsedApiError }>;
  registerWithEmail: (input: {
    email: string;
    password: string;
    passwordConfirm: string;
    inviteCode?: string;
    termsAgreed?: boolean;
    termsVersion?: string;
  }) => Promise<{ success: boolean; error?: ParsedApiError }>;
  changePassword: (
    currentPassword: string,
    newPassword: string,
    newPasswordConfirm: string
  ) => Promise<{ success: boolean; error?: ParsedApiError }>;
  logout: () => Promise<void>;
  refreshStatus: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function extractLoginError(err: unknown): ParsedApiError {
  const parsed = getParsedApiError(err);
  if (parsed.status === 429) {
    return createParsedApiError({
      title: '尝试过于频繁',
      message: '尝试次数过多，请稍后再试。',
      rawMessage: parsed.rawMessage,
      status: parsed.status,
      category: parsed.category,
    });
  }
  return parsed;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [authEnabled, setAuthEnabled] = useState(false);
  const [loggedIn, setLoggedIn] = useState(false);
  const [passwordSet, setPasswordSet] = useState(false);
  const [passwordChangeable, setPasswordChangeable] = useState(false);
  const [setupState, setSetupState] = useState<'enabled' | 'password_retained' | 'no_password'>('no_password');
  const [userMode, setUserMode] = useState<AccountStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<ParsedApiError | null>(null);

  const fetchStatus = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const [adminStatus, accountStatus] = await Promise.all([
        authApi.getStatus().catch(() => null),
        accountApi.getStatus().catch(() => null),
      ]);
      if (adminStatus) {
        setAuthEnabled(adminStatus.authEnabled);
        setLoggedIn(adminStatus.loggedIn);
        setPasswordSet(adminStatus.passwordSet ?? false);
        setPasswordChangeable(adminStatus.passwordChangeable ?? false);
        setSetupState(adminStatus.setupState);
      } else {
        setAuthEnabled(false);
        setLoggedIn(false);
        setPasswordSet(false);
        setPasswordChangeable(false);
        setSetupState('no_password');
      }
      setUserMode(accountStatus);

      const adminLocked = (adminStatus?.authEnabled ?? false) && !(adminStatus?.loggedIn ?? false);
      const userLocked = (accountStatus?.userModeEnabled ?? false) && !(accountStatus?.loggedIn ?? false);
      if (adminLocked || userLocked) {
        useStockPoolStore.getState().resetDashboardState();
      }
    } catch (err) {
      setLoadError(getParsedApiError(err));
      setAuthEnabled(false);
      setLoggedIn(false);
      setPasswordSet(false);
      setPasswordChangeable(false);
      setSetupState('no_password');
      setUserMode(null);
      useStockPoolStore.getState().resetDashboardState();
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchStatus();
  }, [fetchStatus]);

  const login = useCallback(
    async (
      password: string,
      passwordConfirm?: string
    ): Promise<{ success: boolean; error?: ParsedApiError }> => {
      try {
        await authApi.login(password, passwordConfirm);
        await fetchStatus();
        return { success: true };
      } catch (err: unknown) {
        return { success: false, error: extractLoginError(err) };
      }
    },
    [fetchStatus]
  );

  const loginWithEmail = useCallback(
    async (
      email: string,
      password: string
    ): Promise<{ success: boolean; error?: ParsedApiError }> => {
      try {
        await accountApi.login({ email, password });
        await fetchStatus();
        return { success: true };
      } catch (err: unknown) {
        return { success: false, error: extractLoginError(err) };
      }
    },
    [fetchStatus]
  );

  const registerWithEmail = useCallback(
    async (input: {
      email: string;
      password: string;
      passwordConfirm: string;
      inviteCode?: string;
      termsAgreed?: boolean;
      termsVersion?: string;
    }): Promise<{ success: boolean; error?: ParsedApiError }> => {
      try {
        await accountApi.register(input);
        await fetchStatus();
        return { success: true };
      } catch (err: unknown) {
        return { success: false, error: extractLoginError(err) };
      }
    },
    [fetchStatus]
  );

  const changePassword = useCallback(
    async (
      currentPassword: string,
      newPassword: string,
      newPasswordConfirm: string
    ): Promise<{ success: boolean; error?: ParsedApiError }> => {
      try {
        if (userMode?.loggedIn) {
          await accountApi.changePassword({ currentPassword, newPassword, newPasswordConfirm });
        } else {
          await authApi.changePassword(currentPassword, newPassword, newPasswordConfirm);
        }
        return { success: true };
      } catch (err: unknown) {
        return { success: false, error: getParsedApiError(err) };
      }
    },
    [userMode]
  );

  const logout = useCallback(async () => {
    let logoutError: unknown = null;
    try {
      if (userMode?.loggedIn) {
        await accountApi.logout();
      } else {
        await authApi.logout();
      }
    } catch (err) {
      logoutError = err;
    } finally {
      await fetchStatus();
    }

    if (logoutError && getParsedApiError(logoutError).status !== 401) {
      throw logoutError;
    }
  }, [fetchStatus, userMode]);

  const adminEffectiveLoggedIn = !authEnabled || loggedIn;
  const userEffectiveLoggedIn = !(userMode?.userModeEnabled ?? false) || (userMode?.loggedIn ?? false);
  const effectiveLoggedIn = adminEffectiveLoggedIn && userEffectiveLoggedIn;

  return (
    <AuthContext.Provider
      value={{
        authEnabled,
        loggedIn,
        passwordSet,
        passwordChangeable,
        setupState,
        userMode,
        isLoading,
        loadError,
        effectiveLoggedIn,
        login,
        loginWithEmail,
        registerWithEmail,
        changePassword,
        logout,
        refreshStatus: fetchStatus,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- useAuth is a hook, co-located for context access
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
