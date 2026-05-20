import type React from 'react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, BarChart2, Plus, Star, Trash2 } from 'lucide-react';
import { Button } from '../components/common';
import { SettingsAlert } from '../components/settings';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { accountApi, type WatchlistItem } from '../api/account';
import { getParsedApiError } from '../api/error';
import { useAuth } from '../hooks';

const ONBOARDING_MAX = 3;

const OnboardingPage: React.FC = () => {
  const navigate = useNavigate();
  const { userMode } = useAuth();

  const [stocks, setStocks] = useState<WatchlistItem[]>([]);
  const [addInput, setAddInput] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const plan = userMode?.plan;
  const maxStocks = Math.min(plan?.maxStocks ?? ONBOARDING_MAX, ONBOARDING_MAX);

  useEffect(() => {
    document.title = '欢迎使用 DSA - 设置自选股';
  }, []);

  const handleAddStock = async (code: string, name?: string) => {
    const trimmed = code.trim().toUpperCase();
    if (!trimmed) return;
    if (stocks.some((s) => s.stockCode === trimmed)) {
      setError(`${trimmed} 已在列表中`);
      return;
    }
    if (stocks.length >= maxStocks) {
      setError(`最多添加 ${maxStocks} 只股票完成初始设置`);
      return;
    }

    setIsAdding(true);
    setError(null);
    try {
      const res = await accountApi.addWatchlistStock({ stockCode: trimmed, stockName: name });
      setStocks((prev) => [...prev, res.stock]);
      setAddInput('');
    } catch (err) {
      setError(getParsedApiError(err).message);
    } finally {
      setIsAdding(false);
    }
  };

  const handleRemoveStock = async (stockCode: string) => {
    setError(null);
    try {
      await accountApi.removeWatchlistStock(stockCode);
      setStocks((prev) => prev.filter((s) => s.stockCode !== stockCode));
    } catch (err) {
      setError(getParsedApiError(err).message);
    }
  };

  const handleFinish = async () => {
    setIsSaving(true);
    navigate('/', { replace: true });
  };

  const handleSkip = () => {
    navigate('/', { replace: true });
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[var(--login-bg-main)] px-4 py-12">
      {/* Background decoration */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0">
        <div className="absolute -left-[20%] -top-[20%] h-[60%] w-[60%] rounded-full opacity-15" style={{ background: 'radial-gradient(circle, hsl(var(--primary)) 0%, transparent 70%)' }} />
        <div className="absolute -bottom-[10%] -right-[10%] h-[40%] w-[40%] rounded-full opacity-10" style={{ background: 'radial-gradient(circle, hsl(247 84% 66%) 0%, transparent 70%)' }} />
      </div>

      <div className="relative z-10 w-full max-w-[480px]">
        {/* Header */}
        <div className="mb-8 text-center">
          <div className="mb-5 inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-gradient shadow-[0_8px_24px_hsl(var(--primary)/0.35)]">
            <BarChart2 className="h-7 w-7 text-white" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--login-text-primary)]">欢迎使用 DSA</h1>
          <p className="mt-2 text-sm text-[var(--login-text-secondary)]">
            先选 {maxStocks} 只你关注的股票，AI 将在每个交易日为你自动分析。
          </p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-[var(--login-border-card)] bg-[var(--login-bg-card)] p-6 backdrop-blur-xl">
          {error && (
            <div className="mb-4">
              <SettingsAlert title="提示" message={error} variant="error" />
            </div>
          )}

          {/* Stock list */}
          {stocks.length > 0 ? (
            <ul className="mb-4 space-y-2">
              {stocks.map((item, idx) => (
                <li
                  key={item.stockCode}
                  className="flex items-center gap-3 rounded-xl border border-[var(--login-border-card)] bg-[var(--login-bg-card)] px-4 py-2.5 transition-colors hover:bg-[var(--login-bg-card)]/80"
                >
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/20 text-xs font-bold text-primary">
                    {idx + 1}
                  </span>
                  <Star className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                  <span className="flex-1 text-sm font-semibold text-[var(--login-text-primary)]">{item.stockCode}</span>
                  {item.stockName && (
                    <span className="text-xs text-[var(--login-text-secondary)]">{item.stockName}</span>
                  )}
                  <button
                    type="button"
                    className="text-[var(--login-text-muted)] transition-colors hover:text-red-400"
                    onClick={() => void handleRemoveStock(item.stockCode)}
                    title="移除"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="mb-4 flex flex-col items-center gap-2 rounded-xl border border-dashed border-white/[0.08] py-8 text-center">
              <Star className="h-7 w-7 text-[var(--login-text-muted)]/40" />
              <p className="text-sm text-[var(--login-text-muted)]">暂无自选股，在下方搜索添加</p>
            </div>
          )}

          {/* Add input */}
          {stocks.length < maxStocks ? (
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <StockAutocomplete
                  value={addInput}
                  onChange={setAddInput}
                  onSubmit={(code, name) => void handleAddStock(code, name)}
                  disabled={isAdding}
                  placeholder="搜索股票代码或名称，如 600519 贵州茅台"
                />
              </div>
              <Button
                variant="primary"
                isLoading={isAdding}
                onClick={() => void handleAddStock(addInput)}
                disabled={!addInput.trim() || isAdding}
                className="h-11"
              >
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <div className="mb-2 rounded-xl border border-primary/20 bg-primary/8 px-4 py-2.5 text-center text-sm font-medium text-primary">
              已添加 {maxStocks} 只股票，可以开始体验了！
            </div>
          )}

          {/* Progress indicator */}
          <div className="mt-4 flex gap-1.5">
            {Array.from({ length: maxStocks }).map((_, i) => (
              <div
                key={i}
                className={`h-1 flex-1 rounded-full transition-all duration-300 ${
                  i < stocks.length ? 'bg-primary shadow-[0_0_6px_hsl(var(--primary)/0.5)]' : 'bg-white/10'
                }`}
              />
            ))}
          </div>

          {/* Actions */}
          <div className="mt-6 flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={handleSkip}
              className="text-sm text-[var(--login-text-muted)] transition-colors hover:text-[var(--login-text-secondary)]"
            >
              {stocks.length === 0 ? '稍后再设置' : '跳过'}
            </button>
            <Button
              variant="primary"
              size="lg"
              isLoading={isSaving}
              disabled={stocks.length === 0 && !isSaving}
              onClick={() => void handleFinish()}
              className="h-11 rounded-xl shadow-[0_4px_16px_hsl(var(--primary)/0.3)]"
            >
              {stocks.length === 0 ? '跳过，直接进入' : '完成设置，开始体验'}
              <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* Footer note */}
        <p className="mt-5 text-center text-xs text-[var(--login-text-muted)]">
          自选股随时可在「账户设置」中修改 · 免费档最多 {maxStocks} 只
        </p>
      </div>
    </div>
  );
};

export default OnboardingPage;
