import { useEffect, useState } from 'react';
import { systemConfigApi } from '../../api/systemConfig';

export function GenerationBackendStatus() {
  const [status, setStatus] = useState<Awaited<ReturnType<typeof systemConfigApi.getGenerationBackendStatus>> | null>(null);
  useEffect(() => {
    let active = true;
    void systemConfigApi.getGenerationBackendStatus().then((value) => { if (active) setStatus(value); }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  if (!status) return null;
  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-background/40 px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium">报告生成后端</span>
        <span className={status.available ? 'text-emerald-600' : 'text-amber-600'}>
          {status.backend} · {status.available ? '可用' : '不可用'}
        </span>
      </div>
      {status.executable ? <div className="mt-1 text-muted-text">可执行文件：{status.executable}</div> : null}
      {status.fallbackBackend ? <div className="mt-1 text-muted-text">失败回退：{status.fallbackBackend}</div> : null}
      {status.error ? <div className="mt-1 text-amber-700">{status.error}</div> : null}
    </div>
  );
}
