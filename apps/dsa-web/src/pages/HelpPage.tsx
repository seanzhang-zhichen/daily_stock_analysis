import type React from 'react';
import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Bell,
  BookOpenCheck,
  CreditCard,
  LifeBuoy,
  MessageSquareQuote,
  Settings2,
  ShieldAlert,
  Sparkles,
  UserCircle2,
} from 'lucide-react';
import { Card, StandardPageLayout } from '../components/common';
import { useAuth } from '../hooks';

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
    </StandardPageLayout>
  );
};

export default HelpPage;
