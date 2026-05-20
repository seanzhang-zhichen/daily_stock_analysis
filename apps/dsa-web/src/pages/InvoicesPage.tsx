import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  ArrowUpRight,
  FileText,
  Receipt,
  RefreshCw,
} from 'lucide-react';
import { Button, Card, Input, Loading } from '../components/common';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import { billingApi, type BillingInvoice } from '../api/billing';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { useAuth } from '../hooks';

const formatDate = (value?: string | null): string => {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
};

const INVOICE_STATUS_LABEL: Record<string, string> = {
  pending: '审核中',
  issued: '已开具',
  rejected: '已拒绝',
};

const INVOICE_STATUS_COLOR: Record<string, string> = {
  pending: 'text-amber-300 border-amber-400/40 bg-amber-500/10',
  issued: 'text-primary border-primary/40 bg-primary/10',
  rejected: 'text-red-400 border-red-400/40 bg-red-500/10',
};

type InvoiceFormProps = {
  defaultOrderNo?: string;
  onSuccess: (invoice: BillingInvoice) => void;
};

const InvoiceForm: React.FC<InvoiceFormProps> = ({ defaultOrderNo = '', onSuccess }) => {
  const [orderNo, setOrderNo] = useState(defaultOrderNo);
  const [invoiceType, setInvoiceType] = useState<'personal' | 'company'>('personal');
  const [title, setTitle] = useState('');
  const [email, setEmail] = useState('');
  const [taxId, setTaxId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!orderNo.trim()) { setError('请填写订单号'); return; }
    if (!title.trim()) { setError('请填写发票抬头'); return; }
    if (!email.trim()) { setError('请填写收件邮箱'); return; }
    if (invoiceType === 'company' && !taxId.trim()) { setError('企业发票须填写税号'); return; }

    setSubmitting(true);
    try {
      const res = await billingApi.requestInvoice({
        orderNo: orderNo.trim(),
        invoiceType,
        title: title.trim(),
        email: email.trim(),
        taxId: taxId.trim() || undefined,
      });
      onSuccess(res.invoice);
    } catch (err) {
      setError(getParsedApiError(err).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          id="invoice-order-no"
          label="关联订单号"
          placeholder="DSA20250518..."
          value={orderNo}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setOrderNo(e.target.value)}
          disabled={submitting}
        />
        <div>
          <p className="mb-1 text-xs text-secondary-text">发票类型</p>
          <select
            className="ui-input h-11 w-full appearance-none px-3 text-sm"
            value={invoiceType}
            onChange={(e) => setInvoiceType(e.target.value as 'personal' | 'company')}
            disabled={submitting}
          >
            <option value="personal">个人 / 非企业单位</option>
            <option value="company">企业（含税号）</option>
          </select>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          id="invoice-title"
          label="发票抬头"
          placeholder={invoiceType === 'company' ? '公司全称' : '姓名或个人'}
          value={title}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)}
          disabled={submitting}
        />
        {invoiceType === 'company' && (
          <Input
            id="invoice-tax-id"
            label="税号（企业必填）"
            placeholder="统一社会信用代码"
            value={taxId}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTaxId(e.target.value)}
            disabled={submitting}
          />
        )}
      </div>
      <Input
        id="invoice-email"
        label="收件邮箱"
        placeholder="接收发票的邮箱地址"
        value={email}
        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setEmail(e.target.value)}
        disabled={submitting}
      />
      <p className="text-xs text-secondary-text">
        本平台仅开具<strong>电子普通发票</strong>（增值税普通发票），服务名称为「信息技术服务费」。
        审核通过后发票将发送至填写的邮箱，处理时间约 1–3 个工作日。
      </p>
      {error && <SettingsAlert title="提交失败" message={error} variant="error" />}
      <Button type="submit" variant="primary" isLoading={submitting}>
        <FileText className="h-4 w-4" /> 提交发票申请
      </Button>
    </form>
  );
};

const InvoicesPage: React.FC = () => {
  const { userMode } = useAuth();
  const [searchParams] = useSearchParams();
  const defaultOrderNo = searchParams.get('orderNo') ?? '';

  const [invoices, setInvoices] = useState<BillingInvoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    document.title = '我的发票 - DSA';
  }, []);

  const loadInvoices = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await billingApi.listInvoices();
      setInvoices(res.invoices);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (userMode?.loggedIn) void loadInvoices();
  }, [userMode?.loggedIn, loadInvoices]);

  const handleSuccess = (invoice: BillingInvoice) => {
    setSuccessMsg(`发票申请 ${invoice.invoiceNo} 已提交，审核通过后将发送到 ${invoice.email}。`);
    void loadInvoices();
  };

  if (!userMode?.userModeEnabled) {
    return (
      <StandardPageLayout>
        <Card title="我的发票" subtitle="INVOICES">
          <p className="text-sm text-secondary-text">当前实例未启用 To C 多用户模式。</p>
        </Card>
      </StandardPageLayout>
    );
  }

  return (
    <StandardPageLayout>
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wider text-secondary-text">INVOICES</p>
          <h1 className="text-2xl font-semibold text-foreground">我的发票</h1>
          <p className="text-sm text-secondary-text">申请电子普通发票，填写抬头及税号后提交，运营审核后发送至邮箱。</p>
        </div>
        <Button variant="outline" onClick={loadInvoices} disabled={loading}>
          <RefreshCw className={`h-4 w-4${loading ? ' animate-spin' : ''}`} /> 刷新
        </Button>
      </div>

      {successMsg && (
        <SettingsAlert title="申请已提交" message={successMsg} variant="success" />
      )}
      {error && <SettingsAlert title="加载失败" message={error.message} variant="error" />}

      {/* 申请表单 */}
      <Card title="申请发票" subtitle="NEW">
        <InvoiceForm defaultOrderNo={defaultOrderNo} onSuccess={handleSuccess} />
      </Card>

      {/* 历史列表 */}
      <Card title="申请记录" subtitle="HISTORY">
        {loading ? (
          <div className="flex min-h-[10vh] items-center justify-center">
            <Loading />
          </div>
        ) : invoices.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <Receipt className="h-10 w-10 text-secondary-text/40" />
            <p className="text-sm text-secondary-text">暂无发票记录。</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-secondary-text">
                <tr>
                  <th className="pb-2 pr-4">发票编号</th>
                  <th className="pb-2 pr-4">关联订单</th>
                  <th className="pb-2 pr-4">抬头</th>
                  <th className="pb-2 pr-4">状态</th>
                  <th className="pb-2 pr-4">申请时间</th>
                  <th className="pb-2">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {invoices.map((inv) => (
                  <tr key={inv.invoiceNo} className="text-foreground">
                    <td className="py-2 pr-4 font-mono text-xs">{inv.invoiceNo}</td>
                    <td className="py-2 pr-4 font-mono text-xs">{inv.orderNo}</td>
                    <td className="py-2 pr-4">{inv.title}</td>
                    <td className="py-2 pr-4">
                      <span
                        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${
                          INVOICE_STATUS_COLOR[inv.status] ?? 'text-secondary-text border-border/40 bg-card/40'
                        }`}
                      >
                        {INVOICE_STATUS_LABEL[inv.status] ?? inv.status}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-xs text-secondary-text">
                      {formatDate(inv.createdAt)}
                    </td>
                    <td className="py-2">
                      {inv.status === 'issued' && inv.issuedUrl ? (
                        <a
                          href={inv.issuedUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                        >
                          下载 <ArrowUpRight className="h-3 w-3" />
                        </a>
                      ) : (
                        <span className="text-xs text-secondary-text">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="text-xs text-secondary-text">
        <Link to="/account/orders" className="inline-flex items-center gap-1 text-primary hover:underline">
          返回我的订单 <ArrowUpRight className="h-3 w-3" />
        </Link>
      </p>
    </StandardPageLayout>
  );
};

export default InvoicesPage;
