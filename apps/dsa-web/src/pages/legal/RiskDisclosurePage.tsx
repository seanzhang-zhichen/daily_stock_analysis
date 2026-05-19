import type React from 'react';
import { useEffect } from 'react';
import LegalPageLayout from './LegalPageLayout';

const TERMS_VERSION = '2026-05-18';
const EFFECTIVE_DATE = '2026 年 5 月 18 日';

const RiskDisclosurePage: React.FC = () => {
  useEffect(() => {
    document.title = '投资风险揭示书 - DSA';
  }, []);

  return (
    <LegalPageLayout
      title="投资风险揭示书"
      version={TERMS_VERSION}
      effectiveDate={EFFECTIVE_DATE}
    >
      <p>
        本揭示书旨在使您充分了解使用 DSA 智能分析（以下简称"本服务"）输出的内容时所面临的投资风险。
        请您在使用本服务前认真阅读全部内容，<strong>本揭示书的存在不能也不会涵盖证券市场的全部风险</strong>。
      </p>

      <h2 className="mt-8 text-base font-semibold">1. 服务定位</h2>
      <p>
        本服务是一款<strong>面向个人投资者的 AI 数据分析与研究辅助工具</strong>。本服务输出的所有报告、观点、评分、目标价、买卖参考、
        Agent 对话内容均由 AI 模型基于公开数据自动生成，<strong>不构成任何形式的证券投资建议、投资咨询或投资承诺</strong>。
      </p>
      <p>
        本服务运营方<strong>不持有</strong>《证券投资咨询业务资格证书》。如您有专业投顾需求，请咨询持牌的证券公司或基金公司。
      </p>

      <h2 className="mt-8 text-base font-semibold">2. 投资市场基本风险</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          <strong>市场风险</strong>：股票市场受宏观经济、政策环境、行业景气、地缘政治等多重因素影响，价格波动可能造成本金亏损。
        </li>
        <li>
          <strong>个股风险</strong>：单只股票可能因业绩、舆情、退市等突发因素出现剧烈波动甚至归零。
        </li>
        <li>
          <strong>流动性风险</strong>：部分港股、美股标的与小市值 A 股可能存在交易不活跃、买卖价差大、无法及时成交的情况。
        </li>
        <li>
          <strong>汇率风险</strong>：港股、美股以非人民币计价，汇率变化可能影响您的实际收益。
        </li>
        <li>
          <strong>政策风险</strong>：监管政策、税收政策、退市规则等的调整可能对持仓造成实质影响。
        </li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">3. AI 分析特有风险</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          <strong>模型局限</strong>：AI 模型基于历史数据训练，无法保证对未来走势预测的准确性；模型存在产生
          "幻觉" / 错误事实 / 漏判突发事件的可能。
        </li>
        <li>
          <strong>数据延迟</strong>：行情、新闻、财报数据来自第三方源，可能存在延迟、缺失或错误；本服务不对数据准确性作出担保。
        </li>
        <li>
          <strong>样本偏差</strong>：AI 报告中提及的"过往胜率""回测收益"等指标基于历史样本，
          <strong>过往表现不代表未来收益</strong>。
        </li>
        <li>
          <strong>同质化风险</strong>：当大量用户同时基于 AI 报告做相似决策时，可能加剧市场波动。
        </li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">4. 用户应承担的责任</h2>
      <ul className="list-disc space-y-1 pl-5">
        <li>
          您应充分理解所投资标的的基本面、行业、估值、流动性等核心信息，
          <strong>自主独立做出投资决策并承担全部投资结果</strong>。
        </li>
        <li>
          您不应将本服务输出的任何内容视为"内幕信息"、"必涨推荐"、"保证收益"，并避免对外传播误导他人。
        </li>
        <li>
          您应根据自身的<strong>风险承受能力、资金状况与投资目标</strong>合理配置仓位，避免单一标的重仓与高杠杆。
        </li>
        <li>
          建议您预留充足的应急资金，<strong>不要使用借贷资金、信用卡套现资金、不可承受亏损的资金</strong>从事股票投资。
        </li>
      </ul>

      <h2 className="mt-8 text-base font-semibold">5. 文案口径声明</h2>
      <p>
        本服务的所有出口（包括落地页、报告模板、邮件推送、Webhook 通知、聊天回复）均会按以下口径输出，请您据此理解：
      </p>
      <ul className="list-disc space-y-1 pl-5">
        <li>"AI 辅助分析" / "数据驱动复盘" / "参考观点" / "可关注信号"</li>
        <li>不会出现"稳赚 / 必涨 / 保收益 / 推荐买入 / 跑赢大盘 / 内幕 / 绝佳机会"等措辞；</li>
        <li>不会出现"7 天体验返本 / 无效退款双倍赔付"等业绩或退款承诺。</li>
      </ul>
      <p>如您发现违反上述口径的内容，欢迎反馈至客服邮箱，我们将立即修正并改进 prompt。</p>

      <h2 className="mt-8 text-base font-semibold">6. 用户确认</h2>
      <p>
        您确认在勾选《用户服务协议》《隐私政策》《投资风险揭示书》时已认真阅读并理解全部内容；
        您理解并接受证券投资的固有风险，自愿在该理解基础上使用本服务，
        因此产生的全部投资盈亏与本服务运营方无关。
      </p>

      <p className="mt-8 text-xs text-secondary-text">
        如本揭示书后续有更新，运营方将在本页面发布最新版本并请您重新确认。如对内容有疑问，请联系{' '}
        <a className="text-cyan hover:underline" href="mailto:support@example.com">
          support@example.com
        </a>
        。
      </p>
    </LegalPageLayout>
  );
};

export default RiskDisclosurePage;
