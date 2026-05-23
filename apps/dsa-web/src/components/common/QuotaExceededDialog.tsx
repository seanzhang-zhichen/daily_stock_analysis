import type React from 'react';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, ArrowRight, Sparkles, X } from 'lucide-react';
import {
  QUOTA_EXCEEDED_EVENT,
  type QuotaExceededDetail,
} from '../../api';

const KIND_LABEL: Record<string, string> = {
  analysis: 'AI 分析',
  agent: 'Agent 问股',
  notify: '通知推送',
};

/**
 * Global QuotaExceededDialog (wireframe §7).
 *
 * 监听 ``window`` 上的 ``dsa:quota-exceeded`` 自定义事件 (由 axios interceptor
 * 在收到 402 ``quota_exceeded`` 时派发), 弹出统一的配额超限提示。
 */
export const QuotaExceededDialog: React.FC = () => {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<QuotaExceededDetail | null>(null);

  useEffect(() => {
    const handler = (event: Event) => {
      const ce = event as CustomEvent<QuotaExceededDetail>;
      if (ce.detail) {
        setDetail(ce.detail);
      }
    };
    window.addEventListener(QUOTA_EXCEEDED_EVENT, handler as EventListener);
    return () => {
      window.removeEventListener(QUOTA_EXCEEDED_EVENT, handler as EventListener);
    };
  }, []);

  if (!detail) {
    return null;
  }

  const close = () => setDetail(null);

  const kindLabel = KIND_LABEL[detail.kind] ?? detail.kind;
  const primaryAction = {
    label: '升级或续费套餐',
    target: `/billing?from=quota&kind=${encodeURIComponent(detail.kind)}`,
  };

  const dialog = (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={close}
    >
      <div
        role="dialog"
        aria-labelledby="quota-exceeded-title"
        className="mx-4 w-full max-w-md rounded-2xl border border-border/70 bg-elevated p-6 shadow-2xl animate-in fade-in zoom-in duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-amber-500/40 bg-amber-500/10 text-amber-300">
              <AlertTriangle className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <h3
                id="quota-exceeded-title"
                className="text-lg font-semibold text-foreground"
              >
                今日 {kindLabel} 额度已用完
              </h3>
              <p className="mt-1 text-sm text-secondary-text">
                {detail.message}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="关闭"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-secondary-text hover:bg-hover hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-5 space-y-3 rounded-xl border border-border/60 bg-card/60 p-4 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-secondary-text">当前套餐</span>
            <span className="font-medium text-foreground">{detail.planName}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-secondary-text">今日 {kindLabel} 用量</span>
            <span className="font-medium tabular-nums text-foreground">
              {detail.used}/{detail.limit > 0 ? detail.limit : '∞'}
            </span>
          </div>
          <p className="text-xs text-secondary-text">
            额度每天 0 点 (UTC) 重置, 或升级到更高套餐后继续使用:
          </p>
        </div>

        <div className="mt-5 flex flex-col gap-2">
          <button
            type="button"
            onClick={() => {
              close();
              navigate(primaryAction.target);
            }}
            className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary-gradient px-5 text-sm font-medium text-primary-foreground shadow-lg shadow-cyan/20 transition hover:brightness-105"
          >
            <Sparkles className="h-4 w-4" />
            {primaryAction.label}
            <ArrowRight className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={close}
            className="inline-flex h-10 w-full items-center justify-center text-sm text-secondary-text hover:text-foreground"
          >
            稍后再说
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
};
