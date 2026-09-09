import type React from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { formatUiText, UI_TEXT, type UiLanguage, type UiTextKey, type UiTextParams } from '../i18n/uiText';
import { getRuntimeInitialLanguage, persistUiLanguage } from '../utils/uiLanguage';
type ContextValue = { language: UiLanguage; setLanguage: (language: UiLanguage) => void; t: (key: UiTextKey, params?: UiTextParams) => string };
const fallback: ContextValue = { language: 'zh', setLanguage: () => undefined, t: (key, params) => formatUiText(UI_TEXT.zh[key], params) };
const Context = createContext<ContextValue>(fallback);
export const UiLanguageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [language, setState] = useState<UiLanguage>(getRuntimeInitialLanguage);
  const setLanguage = useCallback((next: UiLanguage) => { setState(next); persistUiLanguage(next); }, []);
  useEffect(() => { document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN'; }, [language]);
  const value = useMemo(() => ({ language, setLanguage, t: (key: UiTextKey, params?: UiTextParams) => formatUiText(UI_TEXT[language][key], params) }), [language, setLanguage]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
};
// eslint-disable-next-line react-refresh/only-export-components
export function useUiLanguage(): ContextValue { return useContext(Context); }
