import type React from 'react';
import { useEffect } from 'react';
import LegalPageLayout from './LegalPageLayout';

const TERMS_VERSION = '2026-05-18';
const EFFECTIVE_DATE = '2026 年 5 月 18 日';

const TermsPage: React.FC = () => {
  useEffect(() => {
    document.title = '用户服务协议 - DSA';
  }, []);

  return (
    <LegalPageLayout
      title="用户服务协议"
      version={TERMS_VERSION}
      effectiveDate={EFFECTIVE_DATE}
    >
      <p>
        欢迎使用 DSA 智能分析（以下简称"本服务"）。本协议是您与本服务运营方就使用本服务所订立的协议。
        请您在注册或使用本服务前仔细阅读本协议全部条款。一旦您完成注册或开始使用本服务，即视为已阅读并同意本协议。
      </p>

      <h2 className="mt-8 text-base font-semibold">1. 服务内容</h2>
      <p>
        本服务为基于人工智能模型的股票数据分析与研究辅助工具，提供包括但不限于行情查询、技术指标分析、AI 自动报告生成、
        Agent 问股、自选股管理与定时推送等功能。
      </p>
      <p>
        本服务<strong>不构成任何形式的投资建议、投资咨询或证券推介</strong>。本服务输出的所有内容均由 AI 模型生成或基于公开数据计算，
        不对任何投资决策产生的盈亏承担责任。
      </p>

      <h2 className="mt-8 text-base font-semibold">2. 注册与账户</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>注册时请提供真实有效的电子邮箱并设置安全的密码。</li>
        <li>您应妥善保管账户与密码，不得将账户出借、转让或多人共用，由此产生的损失由您自行承担。</li>
        <li>本服务仅向年满 18 周岁的中国大陆居民提供服务，未成年人请勿注册。</li>
        <li>检测到账户存在异常使用、刷量、违规等情况，本服务有权暂停或终止该账户，并保留追究相关责任的权利。</li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">3. 套餐与计费</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>免费档与付费档（Pro 月付 / Pro 年付）的功能差异以《会员中心》页面为准。</li>
        <li>付费款项一经支付即立即生效，由系统按订单快照价格冻结结算，后续涨价不影响已下单订单。</li>
        <li>第一版暂不支持自动续费 / 连续包月，所有续费均需用户主动下单。</li>
        <li>限时折扣或首单优惠的具体细则以活动页面公告为准。</li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">4. 退款规则</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          自付款之日起 7 个自然日内，<strong>未消费</strong>的订单可申请全额退款；
          已消费订单按已使用天数比例扣除（每日不退、最少扣 1 天）。
        </li>
        <li>退款请通过 <code className="rounded bg-card/40 px-1">/account/orders</code> 提交申请，由运营在 3 个工作日内审核。</li>
        <li>同一用户累计申请退款 ≥ 2 次时进入人工二审；存在恶意刷退、违规使用的账户将被拒绝退款并冻结。</li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">5. 用户行为规范</h2>
      <p>您承诺在使用本服务过程中不得：</p>
      <ul className="list-disc space-y-1 pl-5">
        <li>实施任何违反国家法律法规、公序良俗或第三方合法权益的行为；</li>
        <li>对本服务进行未授权的爬取、反编译、破解、绕开配额限制等行为；</li>
        <li>利用本服务输出的内容进行荐股荐基、操纵市场、内幕交易、虚假宣传等违法违规行为；</li>
        <li>批量注册账户、使用一次性邮箱、虚假身份信息进行刷量或薅羊毛。</li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">6. 知识产权</h2>
      <p>
        本服务及其所有原创内容（包括但不限于代码、UI、文档、报告模板、AI 生成报告）的著作权及相关权利归运营方所有。
        在不修改主体内容的前提下，您可在个人非商业场景下使用 AI 生成的报告；如需商用或公开发布，需另行获得授权。
      </p>

      <h2 className="mt-8 text-base font-semibold">7. 责任限制</h2>
      <p>
        本服务按"现状"提供，运营方在法律允许的最大范围内不对以下情况承担责任：
      </p>
      <ul className="list-disc space-y-1 pl-5">
        <li>因 AI 模型局限性、行情数据延迟或第三方数据源中断导致的报告偏差；</li>
        <li>因您自行决策投资股票产生的盈亏；</li>
        <li>因不可抗力（如自然灾害、监管政策、网络故障）导致的服务中断；</li>
        <li>因您泄露账户密码或会话凭证引发的损失。</li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">8. 协议变更</h2>
      <p>
        运营方有权根据法律法规、产品迭代或运营需要不定期修订本协议，更新后的协议将在本页面发布并标注新的版本号。
        用户在协议升版后下次登录时将被引导重新确认；不同意的用户可注销账户停止使用本服务。
      </p>

      <h2 className="mt-8 text-base font-semibold">9. 争议解决</h2>
      <p>
        本协议适用中华人民共和国法律。因本协议产生的任何争议，双方应友好协商；协商不成的，
        提交运营方所在地有管辖权的人民法院诉讼解决。
      </p>

      <h2 className="mt-8 text-base font-semibold">10. 联系我们</h2>
      <p>
        如对本协议有任何疑问，请发送邮件至{' '}
        <a className="text-primary hover:underline" href="mailto:support@example.com">
          support@example.com
        </a>
        。
      </p>
    </LegalPageLayout>
  );
};

export default TermsPage;
