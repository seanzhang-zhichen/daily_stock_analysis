import { Languages } from 'lucide-react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
export function UiLanguageToggle() {
  const { language, setLanguage, t } = useUiLanguage();
  return <button type="button" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border/70 bg-card/70 px-3 text-sm text-secondary-text hover:bg-hover" aria-label={t('language.toggle')} title={t('language.toggle')}><Languages className="h-4 w-4" /><span>{language === 'zh' ? t('language.short.zh') : t('language.short.en')}</span></button>;
}
