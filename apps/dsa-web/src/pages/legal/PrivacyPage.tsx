import type React from 'react';
import { useEffect } from 'react';
import LegalPageLayout from './LegalPageLayout';

const TERMS_VERSION = '2026-05-18';
const EFFECTIVE_DATE = '2026 年 5 月 18 日';

const PrivacyPage: React.FC = () => {
  useEffect(() => {
    document.title = '隐私政策 - DSA';
  }, []);

  return (
    <LegalPageLayout
      title="隐私政策"
      version={TERMS_VERSION}
      effectiveDate={EFFECTIVE_DATE}
    >
      <p>
        我们高度重视用户的个人信息保护。本《隐私政策》说明您在使用 DSA 智能分析（以下简称"本服务"）期间，
        我们如何收集、使用、共享、保护与处理您的个人信息。本政策遵循《中华人民共和国个人信息保护法》（PIPL）、
        《网络安全法》及国际通行的 GDPR 双口径要求。
      </p>

      <h2 className="mt-8 text-base font-semibold">1. 我们收集的信息</h2>
      <p>本服务在不同场景下会收集以下信息：</p>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          <strong>注册与账户信息</strong>：电子邮箱、加密后的密码哈希、注册 IP、注册时间、协议同意版本号、浏览器 UA。
        </li>
        <li>
          <strong>登录与会话信息</strong>：登录时间、登录 IP、会话 token（本地存储为 httpOnly Cookie，服务端只保留 sha256 哈希）。
        </li>
        <li>
          <strong>使用行为</strong>：分析次数、Agent 调用次数、自选股列表、通知偏好；其中股票代码与分析报告均与您的账户绑定。
        </li>
        <li>
          <strong>付费与订单</strong>：套餐选择、订单金额、支付方式、支付通道交易号、发票抬头与邮箱（仅在您主动申请发票时）。
        </li>
        <li>
          <strong>BYOK 凭证</strong>：当您配置自带 API Key 时，我们会以 Fernet 对称加密的方式落库；
          仅在您发起对应分析 / 问股请求时由后端解密一次，不在日志中输出。
        </li>
        <li>
          <strong>设备与诊断信息</strong>：浏览器类型、操作系统、错误堆栈（用于排错与产品优化）。
        </li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">2. 信息使用目的</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>提供注册、登录、会话保持、配额计量、AI 分析、订阅推送等基本功能；</li>
        <li>处理您的付款、退款、发票申请等商务流程；</li>
        <li>识别异常登录、防止刷量与黑产攻击；</li>
        <li>响应客服请求与法律法规要求的合规调查。</li>
      </ul>
      <p>
        我们<strong>不会</strong>将您的个人信息用于与上述目的无关的商业广告投放或转售给第三方。
      </p>

      <h2 className="mt-8 text-base font-semibold">3. 第三方共享</h2>
      <p>为提供完整服务，我们会与以下类型的第三方共享必要信息：</p>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          <strong>支付通道</strong>（微信支付、支付宝）：在您下单时共享订单号、金额、商户号等支付必需字段；
          支付通道在其官方协议下处理资金流转。
        </li>
        <li>
          <strong>邮件服务商</strong>（如阿里云邮件推送 / SES / SendGrid）：用于发送验证码、密码重置邮件、订阅推送邮件，
          共享邮箱地址与邮件正文。
        </li>
        <li>
          <strong>LLM 服务商</strong>（如 OpenAI / Anthropic / 百度文心 / 通义千问等）：当您发起分析或 Agent 问股时，
          我们会将您输入的提示词、行情上下文等内容传给所选模型；BYOK 模式下使用您自填的 API Key 直接调用。
        </li>
        <li>
          <strong>行情与新闻数据源</strong>（如 AKShare、Baostock、Tushare、AlphaVantage 等）：仅传递股票代码等公开标识，不传递用户身份信息。
        </li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">4. 信息存储与保留期</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>账户信息保留至您主动注销账户后 30 天清理；</li>
        <li>订单、发票、退款记录依据财税法规保留 5 年；</li>
        <li>分析报告与会话日志默认保留 12 个月，您可随时通过《账户设置》删除；</li>
        <li>BYOK 凭证在您删除该 provider 配置后立即从数据库移除，无逻辑保留。</li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">5. 信息安全措施</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>密码采用 PBKDF2-SHA256 加盐哈希存储，逐步升级到 Argon2/bcrypt；</li>
        <li>BYOK API Key 使用 Fernet 对称加密落库，密钥单独从环境变量注入；</li>
        <li>会话 Cookie 默认 httpOnly + SameSite=Lax + 在 HTTPS 下 Secure；</li>
        <li>支付与回调入口启用签名校验、IP 白名单、幂等去重；</li>
        <li>日志中默认对邮箱、Token、API Key 等敏感字段做掩码。</li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">6. 您的权利</h2>
      <p>根据 PIPL / GDPR 等法律，您对自己的个人信息享有以下权利：</p>
      <ul className="list-disc space-y-1 pl-5">
        <li>查询与访问：在《账户设置》查看您的注册信息、自选股、订单与历史报告；</li>
        <li>更正：随时修改邮箱密码、自选股列表、通知偏好与 BYOK 凭证；</li>
        <li>删除与注销：通过工单或邮件申请注销账户，账户进入 7 天冷静期后软删；</li>
        <li>数据导出：通过《账户设置》申请导出个人数据，24 小时内邮件下载链接（MVP 阶段为人工处理）；</li>
        <li>撤回同意：您可随时通过注销账户撤回对协议三件套的同意，但部分功能将不可用。</li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">7. Cookie 与本地存储</h2>
      <p>
        本服务仅使用必要的 Cookie（dsa_user_session 等）维持登录态与会话；不投放跨站追踪或第三方广告 Cookie。
        浏览器本地存储仅保存非敏感的 UI 偏好（如主题、语言）。
      </p>

      <h2 className="mt-8 text-base font-semibold">8. 未成年人信息保护</h2>
      <p>
        本服务面向年满 18 周岁的用户，<strong>不会主动收集未成年人个人信息</strong>。如果监护人发现未成年人在未经许可的情况下注册账户，
        请联系我们删除相关账户与数据。
      </p>

      <h2 className="mt-8 text-base font-semibold">9. 政策更新</h2>
      <p>
        当本政策发生重大变更（涉及收集字段范围、共享对象或您的权利等）时，我们将在登录后通过显著方式告知您并请求重新同意。
        您可在本页面顶部查看当前版本号与生效日期。
      </p>

      <h2 className="mt-8 text-base font-semibold">10. 联系方式</h2>
      <p>
        如对本政策或您的个人信息处理有任何疑问，请发送邮件至{' '}
        <a className="text-primary hover:underline" href="mailto:privacy@example.com">
          privacy@example.com
        </a>
        ，我们将在 7 个工作日内回复。
      </p>
    </LegalPageLayout>
  );
};

export default PrivacyPage;
