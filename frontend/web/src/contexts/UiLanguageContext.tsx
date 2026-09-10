import type React from 'react';
import { createContext, useContext, useEffect, useMemo } from 'react';
import { formatUiText, UI_TEXT, type UiLanguage, type UiTextKey, type UiTextParams } from '../i18n/uiText';
type ContextValue = { language: UiLanguage; t: (key: UiTextKey, params?: UiTextParams) => string };
const fallback: ContextValue = { language: 'zh', t: (key, params) => formatUiText(UI_TEXT.zh[key], params) };
const Context = createContext<ContextValue>(fallback);
export const UiLanguageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  useEffect(() => { document.documentElement.lang = 'zh-CN'; }, []);
  const value = useMemo(() => ({ language: 'zh' as const, t: (key: UiTextKey, params?: UiTextParams) => formatUiText(UI_TEXT.zh[key], params) }), []);
  return <Context.Provider value={value}>{children}</Context.Provider>;
};
// eslint-disable-next-line react-refresh/only-export-components
export function useUiLanguage(): ContextValue { return useContext(Context); }
