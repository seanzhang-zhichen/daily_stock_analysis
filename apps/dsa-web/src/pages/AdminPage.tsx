import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BarChart3,
  Bell,
  CheckCircle2,
  ClipboardList,
  FileText,
  Pin,
  RotateCcw,
  Search,
  ShieldCheck,
  Trash2,
  Users,
  XCircle,
} from 'lucide-react';
import { Button, Card, Input, Loading } from '../components/common';
import { SettingsAlert } from '../components/settings';
import { adminApi, type AdminStats, type AdminUser, type AuditLogEntry } from '../api/admin';
import { noticesApi, type Notice } from '../api/notices';
import type { BillingInvoice, BillingOrder, BillingRefund } from '../api/billing';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { useAuth } from '../hooks';
import { cn } from '../utils/cn';

type TabKey = 'overview' | 'orders' | 'refunds' | 'invoices' | 'users' | 'grant' | 'audit' | 'notices';

const TABS: { key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: 'overview', label: '总览', icon: BarChart3 },
  { key: 'orders', label: '订单', icon: FileText },
  { key: 'refunds', label: '退款审核', icon: RotateCcw },
  { key: 'invoices', label: '发票审核', icon: FileText },
  { key: 'users', label: '用户', icon: Users },
  { key: 'grant', label: '手动开通', icon: CheckCircle2 },
  { key: 'audit', label: '审计日志', icon: ClipboardList },
  { key: 'notices', label: '公告管理', icon: Bell },
];

const formatPrice = (cents: number): string => `¥${(cents / 100).toFixed(2)}`;
const formatDate = (value: string | null): string => {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

const STATUS_COLOR: Record<string, string> = {
  created: 'text-secondary-text',
  pending: 'text-amber-300',
  paid: 'text-emerald-300',
  failed: 'text-red-300',
  closed: 'text-secondary-text',
  refunded: 'text-purple-300',
  partial_refunded: 'text-purple-300',
  rejected: 'text-red-300',
  approved: 'text-emerald-300',
  issued: 'text-emerald-300',
};

const StatusBadge: React.FC<{ status: string }> = ({ status }) => (
  <span
    className={cn(
      'inline-flex items-center rounded-full border border-border/60 bg-card/40 px-2 py-0.5 text-xs',
      STATUS_COLOR[status] ?? 'text-foreground/80'
    )}
  >
    {status}
  </span>
);

// ============================================================
// Overview tab
// ============================================================

const OverviewTab: React.FC = () => {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.stats();
      setStats(data);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (loading) return <Loading />;
  if (error) return <SettingsAlert title="加载失败" message={error.message} variant="error" />;
  if (!stats) return null;

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Card title="用户" subtitle="USERS">
        <p className="text-3xl font-semibold text-foreground">{stats.users.total}</p>
        <p className="text-xs text-secondary-text">付费用户: {stats.users.paid}</p>
      </Card>
      <Card title="订单" subtitle="ORDERS">
        <p className="text-3xl font-semibold text-foreground">{stats.orders.paid} / {stats.orders.total}</p>
        <p className="text-xs text-secondary-text">
          总收入: {formatPrice(stats.orders.revenueCents)}
        </p>
      </Card>
      <Card title="待处理" subtitle="PENDING">
        <p className="text-3xl font-semibold text-foreground">
          {stats.pending.refunds + stats.pending.invoices}
        </p>
        <p className="text-xs text-secondary-text">
          退款待审核 {stats.pending.refunds} · 发票待开具 {stats.pending.invoices}
        </p>
      </Card>
    </div>
  );
};

// ============================================================
// Orders tab
// ============================================================

const OrdersTab: React.FC = () => {
  const [orders, setOrders] = useState<BillingOrder[]>([]);
  const [status, setStatus] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.listOrders({ status: status || undefined, limit: 200 });
      setOrders(data.orders);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Card title="订单" subtitle={`ORDERS (${orders.length})`}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-md border border-border/60 bg-card px-2 py-1.5 text-sm text-foreground"
        >
          <option value="">全部状态</option>
          <option value="created">created</option>
          <option value="pending">pending</option>
          <option value="paid">paid</option>
          <option value="failed">failed</option>
          <option value="closed">closed</option>
          <option value="refunded">refunded</option>
        </select>
        <Button type="button" size="sm" variant="secondary" onClick={refresh} isLoading={loading}>
          刷新
        </Button>
      </div>
      {error ? <SettingsAlert title="加载失败" message={error.message} variant="error" /> : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-secondary-text">
            <tr>
              <th className="pb-2 pr-3">订单号</th>
              <th className="pb-2 pr-3">套餐</th>
              <th className="pb-2 pr-3">金额</th>
              <th className="pb-2 pr-3">通道</th>
              <th className="pb-2 pr-3">状态</th>
              <th className="pb-2">创建时间</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {orders.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-6 text-center text-xs text-secondary-text">
                  暂无订单
                </td>
              </tr>
            ) : (
              orders.map((o) => (
                <tr key={o.orderNo} className="text-foreground">
                  <td className="py-2 pr-3 font-mono text-xs">{o.orderNo}</td>
                  <td className="py-2 pr-3">{o.planCode}</td>
                  <td className="py-2 pr-3 tabular-nums">{formatPrice(o.amountCents)}</td>
                  <td className="py-2 pr-3 text-xs">{o.provider}</td>
                  <td className="py-2 pr-3">
                    <StatusBadge status={o.status} />
                  </td>
                  <td className="py-2 text-xs">{formatDate(o.createdAt)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

// ============================================================
// Refunds tab
// ============================================================

const RefundsTab: React.FC = () => {
  const [refunds, setRefunds] = useState<BillingRefund[]>([]);
  const [status, setStatus] = useState<string>('pending');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [actingRefund, setActingRefund] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.listRefunds({ status: status || undefined, limit: 200 });
      setRefunds(data.refunds);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleApprove = async (refundNo: string) => {
    const providerRefundNo = window.prompt('通道退款单号 (可留空):') ?? undefined;
    setActingRefund(refundNo);
    try {
      await adminApi.approveRefund(refundNo, providerRefundNo || undefined);
      await refresh();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActingRefund(null);
    }
  };

  const handleReject = async (refundNo: string) => {
    const note = window.prompt('拒绝原因 (可选):') ?? undefined;
    setActingRefund(refundNo);
    try {
      await adminApi.rejectRefund(refundNo, note || undefined);
      await refresh();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActingRefund(null);
    }
  };

  return (
    <Card title="退款审核" subtitle={`REFUNDS (${refunds.length})`}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-md border border-border/60 bg-card px-2 py-1.5 text-sm text-foreground"
        >
          <option value="">全部状态</option>
          <option value="pending">pending (待审核)</option>
          <option value="refunded">refunded</option>
          <option value="rejected">rejected</option>
        </select>
        <Button type="button" size="sm" variant="secondary" onClick={refresh} isLoading={loading}>
          刷新
        </Button>
      </div>
      {error ? <SettingsAlert title="加载失败" message={error.message} variant="error" /> : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-secondary-text">
            <tr>
              <th className="pb-2 pr-3">退款单号</th>
              <th className="pb-2 pr-3">订单号</th>
              <th className="pb-2 pr-3">金额</th>
              <th className="pb-2 pr-3">状态</th>
              <th className="pb-2 pr-3">原因</th>
              <th className="pb-2">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {refunds.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-6 text-center text-xs text-secondary-text">
                  暂无退款记录
                </td>
              </tr>
            ) : (
              refunds.map((r) => (
                <tr key={r.refundNo} className="text-foreground">
                  <td className="py-2 pr-3 font-mono text-xs">{r.refundNo}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{r.orderNo}</td>
                  <td className="py-2 pr-3 tabular-nums">{formatPrice(r.amountCents)}</td>
                  <td className="py-2 pr-3">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="max-w-[240px] truncate py-2 pr-3 text-xs text-secondary-text">
                    {r.reason ?? '—'}
                  </td>
                  <td className="py-2">
                    {r.status === 'pending' ? (
                      <div className="flex gap-1">
                        <Button
                          type="button"
                          size="xsm"
                          variant="primary"
                          isLoading={actingRefund === r.refundNo}
                          onClick={() => void handleApprove(r.refundNo)}
                        >
                          <CheckCircle2 className="h-3 w-3" /> 通过
                        </Button>
                        <Button
                          type="button"
                          size="xsm"
                          variant="ghost"
                          disabled={actingRefund === r.refundNo}
                          onClick={() => void handleReject(r.refundNo)}
                        >
                          <XCircle className="h-3 w-3" /> 拒绝
                        </Button>
                      </div>
                    ) : (
                      <span className="text-xs text-secondary-text">—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

// ============================================================
// Invoices tab
// ============================================================

const InvoicesTab: React.FC = () => {
  const [invoices, setInvoices] = useState<BillingInvoice[]>([]);
  const [status, setStatus] = useState<string>('pending');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.listInvoices({ status: status || undefined, limit: 200 });
      setInvoices(data.invoices);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleIssue = async (invoiceNo: string) => {
    const issuedUrl = window.prompt('电子发票下载链接 (可留空, 后续补):') ?? undefined;
    setActing(invoiceNo);
    try {
      await adminApi.issueInvoice(invoiceNo, issuedUrl || undefined);
      await refresh();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActing(null);
    }
  };

  const handleReject = async (invoiceNo: string) => {
    if (!window.confirm('确认拒绝该发票申请?')) return;
    setActing(invoiceNo);
    try {
      await adminApi.rejectInvoice(invoiceNo);
      await refresh();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActing(null);
    }
  };

  return (
    <Card title="发票审核" subtitle={`INVOICES (${invoices.length})`}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-md border border-border/60 bg-card px-2 py-1.5 text-sm text-foreground"
        >
          <option value="">全部状态</option>
          <option value="pending">pending</option>
          <option value="issued">issued</option>
          <option value="rejected">rejected</option>
        </select>
        <Button type="button" size="sm" variant="secondary" onClick={refresh} isLoading={loading}>
          刷新
        </Button>
      </div>
      {error ? <SettingsAlert title="加载失败" message={error.message} variant="error" /> : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-secondary-text">
            <tr>
              <th className="pb-2 pr-3">发票号</th>
              <th className="pb-2 pr-3">订单</th>
              <th className="pb-2 pr-3">抬头</th>
              <th className="pb-2 pr-3">税号</th>
              <th className="pb-2 pr-3">收件邮箱</th>
              <th className="pb-2 pr-3">金额</th>
              <th className="pb-2 pr-3">状态</th>
              <th className="pb-2">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {invoices.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-6 text-center text-xs text-secondary-text">
                  暂无发票记录
                </td>
              </tr>
            ) : (
              invoices.map((i) => (
                <tr key={i.invoiceNo} className="text-foreground">
                  <td className="py-2 pr-3 font-mono text-xs">{i.invoiceNo}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{i.orderNo}</td>
                  <td className="max-w-[180px] truncate py-2 pr-3">{i.title}</td>
                  <td className="py-2 pr-3 text-xs">{i.taxId ?? '—'}</td>
                  <td className="max-w-[160px] truncate py-2 pr-3 text-xs">{i.email}</td>
                  <td className="py-2 pr-3 tabular-nums">{formatPrice(i.amountCents)}</td>
                  <td className="py-2 pr-3">
                    <StatusBadge status={i.status} />
                  </td>
                  <td className="py-2">
                    {i.status === 'pending' ? (
                      <div className="flex gap-1">
                        <Button
                          type="button"
                          size="xsm"
                          variant="primary"
                          isLoading={acting === i.invoiceNo}
                          onClick={() => void handleIssue(i.invoiceNo)}
                        >
                          <CheckCircle2 className="h-3 w-3" /> 已开具
                        </Button>
                        <Button
                          type="button"
                          size="xsm"
                          variant="ghost"
                          disabled={acting === i.invoiceNo}
                          onClick={() => void handleReject(i.invoiceNo)}
                        >
                          <XCircle className="h-3 w-3" /> 拒绝
                        </Button>
                      </div>
                    ) : (
                      <span className="text-xs text-secondary-text">—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

// ============================================================
// Users tab
// ============================================================

const UsersTab: React.FC = () => {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [emailLike, setEmailLike] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const refresh = useCallback(async (q?: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminApi.listUsers({ emailLike: q || undefined, limit: 200 });
      setUsers(data.users);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Card title="用户" subtitle={`USERS (${users.length})`}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="min-w-[220px] flex-1">
          <Input
            id="admin-user-search"
            type="search"
            placeholder="按邮箱模糊搜索"
            value={emailLike}
            onChange={(e) => setEmailLike(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void refresh(emailLike);
            }}
          />
        </div>
        <Button type="button" size="sm" variant="primary" onClick={() => void refresh(emailLike)} isLoading={loading}>
          <Search className="h-4 w-4" /> 搜索
        </Button>
      </div>
      {error ? <SettingsAlert title="加载失败" message={error.message} variant="error" /> : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-secondary-text">
            <tr>
              <th className="pb-2 pr-3">ID</th>
              <th className="pb-2 pr-3">邮箱</th>
              <th className="pb-2 pr-3">套餐</th>
              <th className="pb-2 pr-3">到期</th>
              <th className="pb-2 pr-3">协议版本</th>
              <th className="pb-2 pr-3">Admin</th>
              <th className="pb-2">最近登录</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {users.map((u) => (
              <tr key={u.id} className="text-foreground">
                <td className="py-2 pr-3 font-mono text-xs">{u.id}</td>
                <td className="max-w-[220px] truncate py-2 pr-3">{u.email}</td>
                <td className="py-2 pr-3">
                  <StatusBadge status={u.plan} />
                </td>
                <td className="py-2 pr-3 text-xs">{formatDate(u.planExpiresAt)}</td>
                <td className="py-2 pr-3 text-xs">{u.termsVersion ?? '—'}</td>
                <td className="py-2 pr-3">
                  {u.isAdmin ? (
                    <span className="inline-flex items-center gap-1 text-xs text-cyan">
                      <ShieldCheck className="h-3 w-3" /> admin
                    </span>
                  ) : (
                    <span className="text-xs text-secondary-text">—</span>
                  )}
                </td>
                <td className="py-2 text-xs">{formatDate(u.lastLoginAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

// ============================================================
// Grant plan tab
// ============================================================

const GrantPlanTab: React.FC = () => {
  const [userId, setUserId] = useState('');
  const [planCode, setPlanCode] = useState('pro');
  const [grantDays, setGrantDays] = useState('30');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const handleSubmit = async () => {
    setError(null);
    setInfo(null);
    const uid = parseInt(userId, 10);
    const days = parseInt(grantDays, 10);
    if (!Number.isFinite(uid) || uid <= 0) {
      setError(getParsedApiError(new Error('请输入合法的 userId')));
      return;
    }
    if (!planCode.trim()) {
      setError(getParsedApiError(new Error('套餐代码不能为空')));
      return;
    }
    if (!Number.isFinite(days) || days <= 0) {
      setError(getParsedApiError(new Error('开通天数必须为正整数')));
      return;
    }
    setSubmitting(true);
    try {
      const res = await adminApi.grantPlan({
        userId: uid,
        planCode: planCode.trim(),
        grantDays: days,
        note: note.trim() || undefined,
      });
      setInfo(
        `已为 ${res.user.email} 开通 ${res.subscription.planCode}, 到期 ${formatDate(res.subscription.expiresAt)}。`
      );
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card title="手动开通套餐" subtitle="GRANT-PLAN">
      <p className="mb-4 text-xs text-secondary-text">
        用于 §11.10 兜底人工开通、KOL 内测、客服补单等场景。开通记录会写入 ``app_subscriptions``,
        来源标记为 ``admin``。
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Input
          id="grant-user-id"
          type="number"
          label="目标 user ID"
          placeholder="例: 12"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          disabled={submitting}
        />
        <Input
          id="grant-plan-code"
          type="text"
          label="套餐代码"
          placeholder="pro / pro_yearly"
          value={planCode}
          onChange={(e) => setPlanCode(e.target.value)}
          disabled={submitting}
        />
        <Input
          id="grant-days"
          type="number"
          label="开通天数"
          placeholder="30 / 365"
          value={grantDays}
          onChange={(e) => setGrantDays(e.target.value)}
          disabled={submitting}
        />
        <Input
          id="grant-note"
          type="text"
          label="备注 (可选)"
          placeholder="如: 微信支付 ¥39 已到账"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          disabled={submitting}
        />
      </div>
      <div className="mt-4">
        <Button type="button" variant="primary" onClick={handleSubmit} isLoading={submitting}>
          <CheckCircle2 className="h-4 w-4" /> 立即开通
        </Button>
      </div>
      {error ? <SettingsAlert title="开通失败" message={error.message} variant="error" className="mt-4" /> : null}
      {info ? <SettingsAlert title="开通成功" message={info} variant="success" className="mt-4" /> : null}
    </Card>
  );
};

// ============================================================
// Audit logs tab
// ============================================================

const ACTION_OPTIONS = [
  '', 'auth.login', 'auth.register', 'auth.change_password', 'auth.reset_password',
  'byok.upsert', 'byok.delete', 'plan.redeem', 'plan.grant',
  'order.create', 'order.cancel',
  'refund.create', 'refund.approve', 'refund.reject',
  'invoice.issue', 'invoice.reject',
  'admin.grant_plan', 'admin.approve_refund', 'admin.reject_refund',
  'admin.issue_invoice', 'admin.reject_invoice',
];

const AuditLogsTab: React.FC = () => {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [action, setAction] = useState('');
  const [userId, setUserId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminApi.listAuditLogs({
        action: action || undefined,
        userId: userId ? Number(userId) : undefined,
        limit: 200,
      });
      setLogs(res.logs);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [action, userId]);

  useEffect(() => { load(); }, [load]);

  return (
    <Card className="p-4">
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-secondary-text">动作筛选</label>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="rounded border border-border/60 bg-card px-2 py-1.5 text-sm text-foreground"
          >
            {ACTION_OPTIONS.map((a) => (
              <option key={a} value={a}>{a || '全部'}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-secondary-text">用户 ID</label>
          <input
            type="number"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            placeholder="不填则不限"
            className="w-32 rounded border border-border/60 bg-card px-2 py-1.5 text-sm text-foreground"
          />
        </div>
        <Button size="sm" onClick={load} disabled={loading}>
          <Search className="mr-1 h-3.5 w-3.5" /> 查询
        </Button>
        {loading && <Loading />}
      </div>
      {error ? <SettingsAlert title="加载失败" message={error.message} variant="error" /> : null}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left uppercase tracking-wider text-secondary-text">
            <tr>
              <th className="pb-2 pr-3">时间</th>
              <th className="pb-2 pr-3">动作</th>
              <th className="pb-2 pr-3">用户ID</th>
              <th className="pb-2 pr-3">AdminID</th>
              <th className="pb-2 pr-3">关联标识</th>
              <th className="pb-2 pr-3">IP</th>
              <th className="pb-2 pr-3">详情</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/30">
            {logs.length === 0 && !loading ? (
              <tr><td colSpan={7} className="py-4 text-center text-secondary-text">暂无数据</td></tr>
            ) : (
              logs.map((log) => (
                <tr key={log.id} className="text-foreground">
                  <td className="py-1.5 pr-3 text-secondary-text whitespace-nowrap">{log.createdAt ? new Date(log.createdAt).toLocaleString('zh-CN') : '—'}</td>
                  <td className="py-1.5 pr-3 font-mono">{log.action}</td>
                  <td className="py-1.5 pr-3">{log.userId ?? '—'}</td>
                  <td className="py-1.5 pr-3">{log.adminId ?? '—'}</td>
                  <td className="max-w-[120px] truncate py-1.5 pr-3">{log.targetRef ?? '—'}</td>
                  <td className="py-1.5 pr-3">{log.ip ?? '—'}</td>
                  <td className="max-w-[200px] truncate py-1.5 pr-3 text-secondary-text">{log.detail ?? '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

// ============================================================
// NoticesAdminTab
// ============================================================

const NoticesAdminTab: React.FC = () => {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newContent, setNewContent] = useState('');
  const [newType, setNewType] = useState<'info' | 'warning' | 'danger'>('info');
  const [newPinned, setNewPinned] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await noticesApi.adminList();
      setNotices(data);
    } catch {
      setError('加载公告失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const handleCreate = async () => {
    if (!newTitle.trim() || !newContent.trim()) return;
    setCreating(true);
    try {
      await noticesApi.create({ title: newTitle, content: newContent, noticeType: newType, isPinned: newPinned });
      setNewTitle('');
      setNewContent('');
      setNewType('info');
      setNewPinned(false);
      await load();
    } catch {
      setError('创建公告失败');
    } finally {
      setCreating(false);
    }
  };

  const handlePublish = async (id: number, publish: boolean) => {
    try {
      if (publish) await noticesApi.publish(id);
      else await noticesApi.unpublish(id);
      await load();
    } catch {
      setError('操作失败');
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm('确认删除此公告？')) return;
    try {
      await noticesApi.remove(id);
      await load();
    } catch {
      setError('删除失败');
    }
  };

  return (
    <div className="space-y-6">
      {error ? <p className="text-sm text-red-400">{error}</p> : null}

      {/* 创建公告表单 */}
      <Card className="p-4">
        <h3 className="mb-3 text-sm font-semibold text-foreground">创建新公告</h3>
        <div className="space-y-3">
          <Input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="标题"
            className="w-full"
          />
          <textarea
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            placeholder="公告内容（支持换行）"
            rows={4}
            className="w-full rounded-xl border border-border/60 bg-card/60 px-3 py-2 text-sm text-foreground placeholder:text-secondary-text/50 focus:outline-none focus:ring-1 focus:ring-cyan/50"
          />
          <div className="flex flex-wrap items-center gap-3">
            <select
              value={newType}
              onChange={(e) => setNewType(e.target.value as 'info' | 'warning' | 'danger')}
              className="rounded-lg border border-border/60 bg-card/60 px-2 py-1.5 text-sm text-foreground focus:outline-none"
            >
              <option value="info">信息</option>
              <option value="warning">警示</option>
              <option value="danger">重要</option>
            </select>
            <label className="flex items-center gap-1.5 text-sm text-secondary-text">
              <input type="checkbox" checked={newPinned} onChange={(e) => setNewPinned(e.target.checked)} className="rounded" />
              置顶
            </label>
            <Button size="sm" onClick={() => void handleCreate()} disabled={creating || !newTitle.trim() || !newContent.trim()}>
              {creating ? '创建中...' : '创建（草稿）'}
            </Button>
          </div>
        </div>
      </Card>

      {/* 公告列表 */}
      {loading ? <Loading /> : null}
      {!loading && notices.length === 0 ? (
        <p className="text-sm text-secondary-text">暂无公告</p>
      ) : null}
      {notices.map((n) => (
        <Card key={n.id} className="p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <span className="text-sm font-semibold text-foreground">{n.title}</span>
                {n.isPinned ? <Pin className="h-3.5 w-3.5 text-primary" /> : null}
                <span className={cn(
                  'rounded-full px-2 py-0.5 text-xs',
                  n.isPublished ? 'bg-emerald-400/10 text-emerald-400' : 'bg-secondary-text/10 text-secondary-text'
                )}>
                  {n.isPublished ? '已发布' : '草稿'}
                </span>
                <span className="text-xs text-secondary-text/60">{n.noticeType}</span>
              </div>
              <p className="whitespace-pre-wrap text-xs text-secondary-text">{n.content}</p>
              <p className="mt-1 text-xs text-secondary-text/40">创建: {formatDate(n.createdAt)}</p>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <Button
                size="sm"
                variant={n.isPublished ? 'ghost' : 'primary'}
                onClick={() => void handlePublish(n.id, !n.isPublished)}
                className="text-xs"
              >
                {n.isPublished ? '下架' : '发布'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void handleDelete(n.id)}
                className="text-red-400 hover:text-red-300"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
};

// ============================================================
// AdminPage 主框架
// ============================================================

const AdminPage: React.FC = () => {
  const { userMode } = useAuth();
  const [tab, setTab] = useState<TabKey>('overview');

  useEffect(() => {
    document.title = '运营后台 - DSA';
  }, []);

  const isAdmin = useMemo(() => Boolean(userMode?.user?.isAdmin), [userMode]);

  if (!userMode?.userModeEnabled) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <SettingsAlert title="未启用" message="当前实例未启用 To C 多用户模式, 运营后台不可用。" variant="warning" />
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <SettingsAlert
          title="无权访问"
          message="您当前账号不具备平台管理员权限。如需开通, 请使用 scripts/grant_admin.py 在服务器侧赋权。"
          variant="error"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-cyan" />
        <h1 className="text-xl font-semibold text-foreground">运营后台</h1>
      </div>

      <div className="flex flex-wrap gap-2">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={cn(
                'inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm transition-colors',
                active
                  ? 'border-cyan/40 bg-cyan/10 text-cyan'
                  : 'border-border/60 bg-card/40 text-secondary-text hover:border-cyan/30 hover:text-foreground'
              )}
            >
              <Icon className="h-4 w-4" /> {t.label}
            </button>
          );
        })}
      </div>

      <div>
        {tab === 'overview' && <OverviewTab />}
        {tab === 'orders' && <OrdersTab />}
        {tab === 'refunds' && <RefundsTab />}
        {tab === 'invoices' && <InvoicesTab />}
        {tab === 'users' && <UsersTab />}
        {tab === 'grant' && <GrantPlanTab />}
        {tab === 'audit' && <AuditLogsTab />}
        {tab === 'notices' && <NoticesAdminTab />}
      </div>
    </div>
  );
};

export default AdminPage;
