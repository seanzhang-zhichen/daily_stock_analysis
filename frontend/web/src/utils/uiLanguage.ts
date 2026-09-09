import type { UiLanguage } from '../i18n/uiText';
export const UI_LANGUAGE_STORAGE_KEY = 'dsa.uiLanguage';
export function normalizeUiLanguage(value?: string | null): UiLanguage | null { return value === 'en' || value === 'zh' ? value : null; }
export function getRuntimeInitialLanguage(): UiLanguage {
  try { const stored = normalizeUiLanguage(localStorage.getItem(UI_LANGUAGE_STORAGE_KEY)); if (stored) return stored; } catch { /* ignore */ }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('en') ? 'en' : 'zh';
}
export function persistUiLanguage(language: UiLanguage): void { try { localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, language); } catch { /* ignore */ } }
