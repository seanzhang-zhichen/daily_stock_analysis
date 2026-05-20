import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  KeyRound,
  Lock,
  Plus,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react';
import { Button, Card, ConfirmDialog, Input, Loading, Select } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import {
  accountApi,
  type ByokCredential,
  type ByokListResponse,
} from '../api/account';
import { getParsedApiError, isParsedApiError, type ParsedApiError } from '../api/error';
import { useAuth } from '../hooks';
import { cn } from '../utils/cn';

type FormError = ParsedApiError | string | null;

const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  anspire: 'Anspire',
  aihubmix: 'Aihubmix',
  gemini: 'Google Gemini',
  anthropic: 'Anthropic Claude',
  deepseek: 'DeepSeek',
  custom: '自定义 (OpenAI 兼容)',
};

const formatDate = (value?: string | null): string => {
  if (!value) {
    return '—';
  }
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

const ApiKeysPage: React.FC = () => {
  const { userMode } = useAuth();
  const [data, setData] = useState<ByokListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<ParsedApiError | null>(null);

  // 表单
  const [provider, setProvider] = useState('openai');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<FormError>(null);
  const [submitInfo, setSubmitInfo] = useState<string | null>(null);

  // 删除确认
  const [pendingDelete, setPendingDelete] = useState<ByokCredential | null>(null);
  const [deleteError, setDeleteError] = useState<ParsedApiError | null>(null);

  useEffect(() => {
    document.title = '我的 API Key - DSA';
  }, []);

  const userModeEnabled = Boolean(userMode?.userModeEnabled);
  const loggedIn = Boolean(userMode?.loggedIn);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!loggedIn) {
        setLoading(false);
        return;
      }
      setLoading(true);
      setLoadError(null);
      try {
        const res = await accountApi.listApiKeys();
        if (!cancelled) {
          setData(res);
          if (res.supportedProviders.length > 0) {
            setProvider((prev) =>
              res.supportedProviders.includes(prev) ? prev : res.supportedProviders[0]
            );
          }
        }
      } catch (err) {
        if (!cancelled) setLoadError(getParsedApiError(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [loggedIn]);

  const canByok = data?.canByok ?? userMode?.plan?.canByok ?? false;

  const supportedProviderOptions = useMemo(() => {
    const list = data?.supportedProviders ?? [
      'openai',
      'anspire',
      'aihubmix',
      'gemini',
      'anthropic',
      'deepseek',
      'custom',
    ];
    return list.map((code) => ({
      value: code,
      label: PROVIDER_LABELS[code] ?? code,
    }));
  }, [data]);

  const refresh = async () => {
    try {
      const res = await accountApi.listApiKeys();
      setData(res);
    } catch (err) {
      setLoadError(getParsedApiError(err));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    setSubmitInfo(null);

    if (!apiKey.trim()) {
      setSubmitError('请输入 API Key');
      return;
    }
    setIsSubmitting(true);
    try {
      const res = await accountApi.upsertApiKey({
        provider,
        apiKey: apiKey.trim(),
        baseUrl: baseUrl.trim() || undefined,
        model: model.trim() || undefined,
      });
      setSubmitInfo(
        `已保存 ${PROVIDER_LABELS[res.credential.provider] ?? res.credential.provider} 的 API Key (${res.credential.keyPreview})`
      );
      setApiKey('');
      setBaseUrl('');
      setModel('');
      await refresh();
    } catch (err) {
      setSubmitError(getParsedApiError(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!pendingDelete) return;
    setDeleteError(null);
    try {
      await accountApi.deleteApiKey(pendingDelete.provider);
      setPendingDelete(null);
      await refresh();
    } catch (err) {
      setDeleteError(getParsedApiError(err));
    }
  };

  if (!userModeEnabled) {
    return (
      <StandardPageLayout>
        <Card title="我的 API Key" subtitle="BYOK">
          <p className="text-sm text-secondary-text">
            当前实例未启用 To C 多用户模式, BYOK 暂不可用。
          </p>
        </Card>
      </StandardPageLayout>
    );
  }

  if (!loggedIn) {
    return (
      <StandardPageLayout>
        <Card title="我的 API Key" subtitle="BYOK">
          <p className="text-sm text-secondary-text">请先登录后管理你的 API Key。</p>
          <div className="mt-4">
            <Link to="/login">
              <Button variant="primary">前往登录</Button>
            </Link>
          </div>
        </Card>
      </StandardPageLayout>
    );
  }

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <Loading />
      </div>
    );
  }

  if (loadError) {
    return (
      <StandardPageLayout>
        <SettingsAlert title="加载失败" message={loadError.message} variant="error" />
      </StandardPageLayout>
    );
  }

  return (
    <StandardPageLayout>
      <div className="space-y-1">
        <p className="text-xs font-medium uppercase tracking-wider text-secondary-text">
          BYOK
        </p>
        <h1 className="text-2xl font-semibold text-foreground">我的 API Key</h1>
        <p className="text-sm text-secondary-text">
          使用你自己的 API Key 后, 调用不再占用平台配额, 但需要自行承担调用成本和保管密钥安全。
        </p>
      </div>

      {!canByok ? (
        <Card title="未解锁 BYOK" subtitle="UPGRADE REQUIRED">
          <p className="text-sm text-secondary-text">
            当前套餐不支持 BYOK, 请先升级到 Pro 后再添加 API Key。
          </p>
          <div className="mt-4">
            <Link to="/billing">
              <Button variant="primary">前往会员中心升级</Button>
            </Link>
          </div>
        </Card>
      ) : null}

      <Card title="已配置的 Key" subtitle="CREDENTIALS">
        {(data?.credentials.length ?? 0) === 0 ? (
          <p className="text-sm text-secondary-text">尚未添加任何 API Key。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-secondary-text">
                <tr>
                  <th className="pb-2 pr-3">Provider</th>
                  <th className="pb-2 pr-3">Key 掩码</th>
                  <th className="pb-2 pr-3">默认模型</th>
                  <th className="pb-2 pr-3">Base URL</th>
                  <th className="pb-2 pr-3">状态</th>
                  <th className="pb-2 pr-3">更新时间</th>
                  <th className="pb-2">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {data!.credentials.map((row) => (
                  <tr key={row.id} className="text-foreground">
                    <td className="py-2 pr-3 font-medium">
                      {PROVIDER_LABELS[row.provider] ?? row.provider}
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs">{row.keyPreview}</td>
                    <td className="py-2 pr-3 text-xs">{row.model ?? '—'}</td>
                    <td className="py-2 pr-3 text-xs text-secondary-text">
                      {row.baseUrl ?? '—'}
                    </td>
                    <td className="py-2 pr-3">
                      <span
                        className={cn(
                          'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs',
                          row.status === 'active'
                            ? 'border-primary/30 bg-primary/10 text-primary'
                            : 'border-amber-500/40 bg-amber-500/10 text-amber-300'
                        )}
                      >
                        {row.status === 'active' ? (
                          <ShieldCheck className="h-3 w-3" />
                        ) : (
                          <XCircle className="h-3 w-3" />
                        )}
                        {row.status === 'active' ? '启用' : row.status}
                      </span>
                    </td>
                    <td className="py-2 pr-3 tabular-nums text-xs">
                      {formatDate(row.updatedAt)}
                    </td>
                    <td className="py-2">
                      <Button
                        type="button"
                        variant="danger-subtle"
                        size="sm"
                        onClick={() => setPendingDelete(row)}
                      >
                        <Trash2 className="h-3.5 w-3.5" /> 删除
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {deleteError ? (
          <SettingsAlert
            title="删除失败"
            message={deleteError.message}
            variant="error"
            className="mt-4"
          />
        ) : null}
      </Card>

      <Card title="添加或更新 Key" subtitle="UPSERT">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">Provider</label>
              <Select
                value={provider}
                onChange={(value) => setProvider(value)}
                options={supportedProviderOptions}
                disabled={isSubmitting || !canByok}
              />
              <p className="text-xs text-secondary-text">同一 provider 重复保存会覆盖之前的 Key。</p>
            </div>
            <Input
              id="byok-model"
              type="text"
              label="默认模型 (可选)"
              placeholder="例如: gpt-4o-mini"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={isSubmitting || !canByok}
            />
          </div>
          <Input
            id="byok-api-key"
            type="password"
            allowTogglePassword
            iconType="key"
            label="API Key"
            placeholder={provider === 'openai' ? 'sk-...' : '请输入 API Key'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            disabled={isSubmitting || !canByok}
            autoComplete="off"
          />
          <Input
            id="byok-base-url"
            type="text"
            label="Base URL (仅自定义 / 兼容端点需要)"
            hint="例如: https://api.openai.com/v1, 留空则使用 provider 默认。"
            placeholder="可选"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            disabled={isSubmitting || !canByok}
          />

          {submitError ? (
            isParsedApiError(submitError) ? (
              <SettingsAlert title="保存失败" message={submitError.message} variant="error" />
            ) : (
              <SettingsAlert title="保存失败" message={submitError} variant="error" />
            )
          ) : null}
          {submitInfo ? (
            <SettingsAlert title="已保存" message={submitInfo} variant="success" />
          ) : null}

          <Button type="submit" variant="primary" isLoading={isSubmitting} disabled={!canByok}>
            <Plus className="h-4 w-4" /> 保存 API Key
          </Button>
          <p className="text-xs text-secondary-text">
            <Lock className="mr-1 inline h-3 w-3" />
            Key 在落库前已加密 (优先 Fernet, 退回带 HMAC 的对称加密); 提交后无法再次查看明文。
          </p>
        </form>
      </Card>

      <p className="text-xs text-secondary-text">
        <KeyRound className="mr-1 inline h-3 w-3" />
        BYOK 模式下, 调用上游模型的费用由你自己承担; 平台不会保存调用日志中的明文 Key。
      </p>

      <ConfirmDialog
        isOpen={pendingDelete != null}
        title="删除 API Key"
        message={
          pendingDelete
            ? `确认删除 ${PROVIDER_LABELS[pendingDelete.provider] ?? pendingDelete.provider} 的 API Key 吗? 删除后, 该 provider 的调用将回到平台默认 Key。`
            : ''
        }
        confirmText="确认删除"
        cancelText="取消"
        isDanger
        onConfirm={handleDelete}
        onCancel={() => {
          setPendingDelete(null);
          setDeleteError(null);
        }}
      />
    </StandardPageLayout>
  );
};

export default ApiKeysPage;
