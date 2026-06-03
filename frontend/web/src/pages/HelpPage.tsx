import type React from 'react';
import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Bell,
  Bot,
  BookOpenCheck,
  Building2,
  CheckCircle2,
  CreditCard,
  LifeBuoy,
  MessageCircle,
  MessageSquareQuote,
  Send,
  Settings2,
  ShieldAlert,
  Sparkles,
  UserCircle2,
  Webhook,
  type LucideIcon,
} from 'lucide-react';
import { Card, StandardPageLayout } from '../components/common';
import { useAuth } from '../hooks';

interface WebhookAppGuide {
  id: string;
  title: string;
  label: string;
  icon: LucideIcon;
  accentClassName: string;
  setup: string;
  endpoint: string;
  note: string;
}

const webhookAppGuides: WebhookAppGuide[] = [
  {
    id: 'webhook-feishu',
    title: '飞书通知',
    label: 'Feishu',
    icon: MessageCircle,
    accentClassName: 'border-sky-500/20 bg-sky-500/10 text-sky-500',
    setup: '在飞书群聊中添加「自定义机器人」，复制机器人 Webhook 地址，回到账户页的「飞书通知」中粘贴并保存。',
    endpoint: 'https://open.feishu.cn/open-apis/bot/v2/hook/...',
    note: '如果机器人开启了签名校验，需要在飞书侧关闭签名或由平台维护者接入签名参数；当前账户页只保存 Webhook URL。',
  },
  {
    id: 'webhook-wecom',
    title: '企业微信通知',
    label: 'WeCom',
    icon: Building2,
    accentClassName: 'border-emerald-500/20 bg-emerald-500/10 text-emerald-500',
    setup: '在企业微信群右上角进入「群机器人」，添加机器人后复制地址，填入「企业微信通知」。',
    endpoint: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...',
    note: '请确认机器人所在群成员能接收分析报告，key 泄露后应在企业微信中重建机器人。',
  },
  {
    id: 'webhook-dingtalk',
    title: '钉钉通知',
    label: 'DingTalk',
    icon: Bot,
    accentClassName: 'border-blue-500/20 bg-blue-500/10 text-blue-500',
    setup: '在钉钉群设置中添加「自定义机器人」，复制机器人地址，填入「钉钉通知」。',
    endpoint: 'https://oapi.dingtalk.com/robot/send?access_token=...',
    note: '如果开启安全设置，建议使用关键词方式，并把关键词设为 `DSA` 或 `AI 分析`。',
  },
  {
    id: 'webhook-discord',
    title: 'Discord 通知',
    label: 'Discord',
    icon: MessageSquareQuote,
    accentClassName: 'border-indigo-500/20 bg-indigo-500/10 text-indigo-500',
    setup: '在 Discord 频道设置中打开 Integrations / Webhooks，创建 Webhook 后复制 URL，填入「Discord 通知」。',
    endpoint: 'https://discord.com/api/webhooks/...',
    note: 'Webhook 会把报告发送到创建它的频道；如需换频道，请在 Discord 中为目标频道重新创建 Webhook。',
  },
  {
    id: 'webhook-telegram',
    title: 'Telegram 通知',
    label: 'Telegram',
    icon: Send,
    accentClassName: 'border-cyan-500/20 bg-cyan-500/10 text-cyan-500',
    setup: '先通过 BotFather 创建 Bot 并获取 token，再确认目标 chat_id，按下方格式填入「Telegram 通知」。',
    endpoint: 'https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>',
    note: '私聊需要先给 Bot 发一条消息；群聊需要把 Bot 加入群，并确认它有发送消息权限。',
  },
  {
    id: 'webhook-custom',
    title: '自定义 Webhook',
    label: 'Custom',
    icon: Webhook,
    accentClassName: 'border-amber-500/20 bg-amber-500/10 text-amber-500',
    setup: '准备一个可接收 HTTP POST 的 URL，账户页会向该地址推送 JSON 格式分析报告。',
    endpoint: 'https://your-service.example.com/webhook',
    note: '建议服务端自行校验来源、限制访问频率，并避免把包含密钥的 URL 公开分享。',
  },
];

const HelpPage: React.FC = () => {
  const { userMode } = useAuth();
  const userModeEnabled = Boolean(userMode?.userModeEnabled);
  const userIsAdmin = Boolean(userMode?.user?.isAdmin);
  const canAccessSystemSettings = !userModeEnabled || userIsAdmin;
  const settingsTarget = canAccessSystemSettings ? '/settings' : '/account';

  useEffect(() => {
    document.title = '帮助中心 - DSA';
  }, []);

  return (
    <StandardPageLayout>
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <p className="ui-eyebrow">HELP CENTER</p>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">帮助中心</h1>
          <p className="max-w-3xl text-sm leading-6 text-secondary-text/85">
            这里汇总常见使用问题、配置入口、反馈方式和投资风险提示。DSA 是股票 AI 分析助手，输出内容仅用于信息整理和辅助决策。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/notices" className="ui-button ui-button-size-md ui-button-outline">
            <Bell className="h-4 w-4" /> 查看公告
          </Link>
          <a href="#support" className="ui-button ui-button-size-md ui-button-primary">
            <LifeBuoy className="h-4 w-4" /> 反馈指引
          </a>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card className="border-primary/16 bg-primary/5" padding="md">
          <div className="flex items-start gap-3">
            <Sparkles className="mt-0.5 h-5 w-5 text-primary" />
            <div>
              <h2 className="text-sm font-semibold text-foreground">快速开始</h2>
              <p className="mt-1 text-sm leading-6 text-secondary-text">
                在首页输入股票代码或名称即可生成分析报告，也可以先添加自选股，后续开启每日推送。
              </p>
            </div>
          </div>
        </Card>
        <Card className="border-primary/16 bg-primary/5" padding="md">
          <div className="flex items-start gap-3">
            <MessageSquareQuote className="mt-0.5 h-5 w-5 text-primary" />
            <div>
              <h2 className="text-sm font-semibold text-foreground">问股助手</h2>
              <p className="mt-1 text-sm leading-6 text-secondary-text">
                进入问股页面可围绕个股、组合或市场进行多轮提问，Agent 调用会受当前套餐配额约束。
              </p>
            </div>
          </div>
        </Card>
        <Card className="border-primary/16 bg-primary/5" padding="md">
          <div className="flex items-start gap-3">
            <UserCircle2 className="mt-0.5 h-5 w-5 text-primary" />
            <div>
              <h2 className="text-sm font-semibold text-foreground">账户与配额</h2>
              <p className="mt-1 text-sm leading-6 text-secondary-text">
                在账户页管理自选股、通知偏好、模型偏好、个人数据导出和账号安全操作。
              </p>
            </div>
          </div>
        </Card>
        <Card className="border-primary/16 bg-primary/5" padding="md">
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 h-5 w-5 text-primary" />
            <div>
              <h2 className="text-sm font-semibold text-foreground">风险提示</h2>
              <p className="mt-1 text-sm leading-6 text-secondary-text">
                AI 分析不构成投资建议，行情和模型输出都可能出错，交易前请独立判断并控制仓位风险。
              </p>
            </div>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <Card title="常见问题" subtitle="FAQ" padding="lg">
          <div className="space-y-5">
            <section className="space-y-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <BookOpenCheck className="h-4 w-4 text-primary" /> 报告怎么看？
              </h2>
              <p className="text-sm leading-6 text-secondary-text">
                首页报告会展示情绪评分、操作建议、趋势判断、关键依据和资讯摘要。建议把它当作研究摘要，而不是买卖指令。
              </p>
            </section>
            <section className="space-y-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Bell className="h-4 w-4 text-primary" /> 每日推送在哪里配置？
              </h2>
              <p className="text-sm leading-6 text-secondary-text">
                登录后进入账户页，在「我的自选股」维护关注列表，在「通知偏好」开启每日推送或配置 Pro Webhook。
              </p>
            </section>
            <section className="space-y-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <CreditCard className="h-4 w-4 text-primary" /> 配额用完怎么办？
              </h2>
              <p className="text-sm leading-6 text-secondary-text">
                达到当日上限后，可等待次日刷新或升级套餐以获得更高配额。
              </p>
            </section>
            <section className="space-y-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Settings2 className="h-4 w-4 text-primary" /> 系统配置在哪里？
              </h2>
              <p className="text-sm leading-6 text-secondary-text">
                普通用户只能管理个人账户、自选股和通知偏好；部署级模型渠道、通知通道和系统参数仅平台管理员可见。
              </p>
            </section>
          </div>
        </Card>

        <div className="space-y-4">
          <Card title="配置入口" subtitle="SETTINGS" padding="lg">
            <div className="space-y-3 text-sm leading-6 text-secondary-text">
              <p>
                {canAccessSystemSettings
                  ? '你当前可以进入系统设置，管理部署级模型、数据源、通知与运行参数。'
                  : '你当前是普通用户，请在账户页管理个人资料、自选股、通知偏好和可用权益。'}
              </p>
              <Link to={settingsTarget} className="ui-button ui-button-size-md ui-button-secondary w-full justify-center">
                {canAccessSystemSettings ? '打开系统设置' : '打开账户设置'}
              </Link>
            </div>
          </Card>

          <Card title="反馈方式" subtitle="SUPPORT" padding="lg">
            <div className="space-y-3 text-sm leading-6 text-secondary-text">
              <p id="support">
                如遇到异常结果、页面报错或配额状态不一致，请优先记录问题上下文，并联系服务维护者处理。
              </p>
              <p>反馈问题时建议附上页面路径、操作步骤、错误提示和大致发生时间，便于定位日志。</p>
            </div>
          </Card>

          <Card className="border-amber-400/30 bg-amber-500/5" padding="lg">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
              <div className="space-y-2">
                <h2 className="text-sm font-semibold text-foreground">免责声明</h2>
                <p className="text-sm leading-6 text-secondary-text">
                  DSA 不保证数据实时、完整或绝对准确，也不承诺收益。任何投资行为均由你自行承担风险。
                </p>
              </div>
            </div>
          </Card>
        </div>
      </div>

      <div id="webhook-setup" className="scroll-mt-24">
        <Card title="机器人通知配置" subtitle="WEBHOOKS" padding="lg">
          <div className="mb-5 flex flex-col gap-3 border-b border-border/70 pb-5 lg:flex-row lg:items-end lg:justify-between">
            <p className="max-w-3xl text-sm leading-6 text-secondary-text">
              每个通知 App 使用独立 Webhook 地址。先在对应 App 里创建机器人，再把复制到的 URL 填到账户页对应通知渠道中。
            </p>
            <Link to="/account" className="ui-button ui-button-size-sm ui-button-outline w-full shrink-0 sm:w-auto">
              <Bell className="h-4 w-4" /> 打开账户通知
            </Link>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {webhookAppGuides.map(({ id, title, label, icon: Icon, accentClassName, setup, endpoint, note }) => (
              <section
                key={id}
                id={id}
                className="scroll-mt-24 rounded-xl border border-border/80 bg-card/80 p-4 shadow-card transition hover:-translate-y-0.5 hover:border-primary/25 hover:bg-card"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${accentClassName}`}>
                      <Icon className="h-5 w-5" />
                    </span>
                    <div>
                      <h2 className="text-base font-semibold text-foreground">{title}</h2>
                      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-text">{label}</p>
                    </div>
                  </div>
                  <CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-primary/70" />
                </div>

                <div className="mt-4 space-y-3">
                  <div className="rounded-lg border border-border/70 bg-muted/35 p-3">
                    <p className="text-xs font-semibold text-foreground">配置位置</p>
                    <p className="mt-1 text-sm leading-6 text-secondary-text">{setup}</p>
                  </div>

                  <div className="rounded-lg border border-border/70 bg-background/60 p-3">
                    <p className="text-xs font-semibold text-foreground">URL 示例</p>
                    <code className="mt-2 block break-all rounded-md bg-muted px-2.5 py-2 text-xs leading-5 text-secondary-text">
                      {endpoint}
                    </code>
                  </div>

                  <p className="text-xs leading-5 text-muted-text">{note}</p>
                </div>
              </section>
            ))}
          </div>
        </Card>
      </div>
    </StandardPageLayout>
  );
};

export default HelpPage;
