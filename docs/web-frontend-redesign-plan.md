# Web 前端重构计划

本文档记录 `apps/dsa-web/` 前端页面、交互、组件体系与样式体系的完整重构方案。本次重构的定位不是局部换肤、样式微调或在现有页面上继续补丁式优化，而是对旧 Web 前端 UI 进行完全删除式重构：旧页面结构、旧组件视觉语义、旧样式体系、旧布局方式和旧视觉风格全部视为废弃对象，最终目标是从代码中移除，而不是继续兼容或包裹。

重构边界是：旧前端展示层必须彻底重写并删除，后端 API、业务流程、数据契约、认证/配额/订单/通知等核心业务语义默认保持兼容。允许在前端内部重组目录、替换页面 JSX 结构、重写通用组件 API、删除旧样式、删除旧页面布局、删除旧视觉语义和删除旧工具类；本计划不主动推动后端接口破坏性变更。

## 1. 背景与目标

当前 Web 前端已经具备较完整的业务能力，包括个股分析、AI 问股、持仓管理、回测、设置、账号、订阅、订单、发票、公告、管理员后台等页面。问题不只是“页面不好看”，而是现有页面结构、样式组织、组件抽象和视觉语言已经不适合继续小修小补；本次不在旧结构上继续叠加样式，不做旧 UI 的延寿，不以“渐进兼容旧视觉”为目标。

本次重构目标如下：

- 重建产品视觉语言，让应用从“功能堆叠型 WebUI”升级为完整、专业、现代的金融智能工作台。
- 重写核心页面的信息架构和 JSX 布局，让首页、问股、持仓、回测等页面围绕用户任务重新组织。
- 重建设计系统，包括 token、布局、组件、表单、状态、表格、图表、Markdown 阅读体验和响应式规则。
- 删除旧的 cyber / terminal / glass / dashboard 混杂风格，删除旧样式、旧工具类、旧页面布局和旧组件视觉语义。
- 仅复用 React、Vite、Tailwind CSS、API client、业务 hooks/store 和测试基础中与 UI 旧结构无关的部分；页面层、组件层、样式层按新架构重建。
- 建立后续新增页面必须复用的 UI 基础设施，避免再次回到页面级 className 堆叠模式。

本次重构不以更换前端技术栈为目标，但必须删除旧前端 UI 体系。React、Vite、Tailwind CSS 可以继续作为技术底座；页面结构、组件 API、CSS 文件组织、设计 token、通用组件实现和页面内布局按新方案整体替换。除非另有明确需求，不引入新的大型 UI 组件库，不改后端 API，不重写业务 hooks/store，不改认证、配额、订单、支付、通知等业务语义。

## 2. 当前前端结构概览

当前前端位于 `apps/dsa-web/`，关键结构如下：

| 区域 | 现状 | 重构关注点 |
| --- | --- | --- |
| `src/App.tsx` | 路由、认证保护、全局弹窗挂载 | 保持业务路由语义，必要时调整 Shell 接入和页面分组 |
| `src/components/layout/` | Shell、SidebarNav、QuotaIndicator | 彻底重建全局应用框架、导航和移动端骨架 |
| `src/components/common/` | Button、Card、Input、EmptyState、Alert、Drawer 等 | 以新设计系统为准重写或替换通用组件 |
| `src/pages/` | 首页、问股、持仓、回测、设置、账号、商业化页面等 | 按用户任务重写页面结构，而不是简单替换 className |
| `src/index.css` | 主题变量、工具类、页面样式混合，体量较大 | 改造为新样式入口，旧样式内容在页面重构完成后必须删除 |
| `tailwind.config.js` | Tailwind 扩展色彩、阴影、动画、半径等 | 重新收敛为设计系统 token 的 Tailwind 映射 |

### 2.1 路由与页面盘点

当前 `App.tsx` 暴露的主要页面和重构定位如下。重构时以这些路由的用户任务和业务结果为保留对象，不以旧 DOM 层级、旧 className 和旧视觉结构为保留对象。

| 路由 / 页面 | 当前入口 | 新布局类型 | 重构优先级 | 必须保持的业务语义 |
| --- | --- | --- | --- | --- |
| `/` | `HomePage` | 分析工作台 | P0 | 股票输入、策略选择、推送选项、大盘复盘、任务状态、历史报告、报告查看 |
| `/chat` | `ChatPage` | AI 投研对话工作台 | P0 | 会话创建/切换/删除、消息发送、技能选择上限、流式输出、上下文追问 |
| `/portfolio` | `PortfolioPage` | 投资组合工作台 | P0 | 账户切换、成本口径、持仓/现金/交易录入、CSV 导入、价格/汇率/风险状态 |
| `/backtest` | `BacktestPage` | 策略评估工作台 | P1 | 参数配置、执行回测、指标/图表/交易结果展示、执行错误反馈 |
| `/settings` | `SettingsPage` | 标准内容页 | P1 | 后端配置读取/保存、连接检查、危险操作确认、管理员兼容入口 |
| `/account` | `AccountPage` | 标准内容页 | P2 | 用户资料、权益、配额、续费提示、用户模式判断 |
| `/account/api-keys` | `ApiKeysPage` | 标准内容页 | P2 | API Key 创建、展示、撤销和敏感信息展示规则 |
| `/billing` | `BillingPage` | 标准内容页 | P2 | 订阅计划、支付入口、权益说明、配额引导 |
| `/account/orders` | `OrdersPage` | 标准内容页 | P2 | 订单列表、状态、金额、支付/退款相关入口 |
| `/account/invoices` | `InvoicesPage` | 标准内容页 | P2 | 发票申请、发票记录、开票信息展示 |
| `/admin` | `AdminPage` | 标准内容页 / 后台数据页 | P2 | 管理员权限判断、用户/订单/系统数据展示与操作 |
| `/notices` | `NoticesPage` | 标准内容页 | P2 | 公告公开访问、公告列表和详情阅读 |
| `/login` | `LoginPage` / `UserAuthPage` | 认证页 | P1 | 管理员登录、用户登录、用户模式切换、redirect 参数 |
| `/register` | `UserAuthPage` | 认证页 | P2 | 注册、协议勾选、邮箱验证引导 |
| `/forgot-password` | `ForgotPasswordPage` | 认证页 | P2 | 找回密码、邮件发送状态、错误反馈 |
| `/verify-email` | `VerifyEmailPage` | 认证页 | P2 | 邮箱验证状态、失败重试、跳转登录 |
| `/onboarding` | `OnboardingPage` | 首次引导页 | P2 | 首次配置、用户模式判断、下一步引导 |
| `/legal/*` | `TermsPage` / `PrivacyPage` / `RiskDisclosurePage` | 公开阅读页 | P3 | 公开访问、可读性、协议内容不丢失 |
| `*` | `NotFoundPage` | 轻量状态页 | P3 | 404 说明、返回入口 |

### 2.2 全局能力盘点

以下能力不是单页 UI，可以随页面重构同步换视觉，但不应改变触发语义：

| 能力 | 当前承载 | 重构要求 |
| --- | --- | --- |
| 登录态与路由保护 | `AuthProvider`、`AppContent`、`Navigate` | 保持未登录跳转、公开页面例外、用户模式和管理员模式判断 |
| 全局续费提示 | `RenewalBanner` | 可以换成新 banner/inline notice 视觉，但过期和即将过期语义不变 |
| 配额超限 | `QuotaExceededDialog` | 作为产品引导状态重写，不降级为普通 toast |
| API 错误展示 | `ApiErrorAlert`、页面内错误块 | 错误靠近发生区域，全局错误保留重试入口 |
| 主题与深色模式 | 当前 CSS token / theme class | 新 token 必须同步定义浅色和深色值 |
| 移动端导航 | `Shell` / `Drawer` | 新 Shell 统一负责，不让页面各自实现主导航 |

### 2.3 可复用与必须重写边界

为避免“看似重构、实际套壳”，先明确边界：

| 类别 | 可复用 | 必须重写 / 删除 |
| --- | --- | --- |
| 技术栈 | React、Vite、Tailwind、React Router、Zustand、axios API client | 不为 UI 重构引入新的大型框架或平行状态管理 |
| 业务逻辑 | hooks、store、API client、格式化函数、权限判断、轮询/流式逻辑 | 页面内只服务旧布局的状态拆分和 className 拼接 |
| 测试资产 | 关键用户路径测试、必要 `data-testid`、API mock | 锁死旧 DOM 层级、旧按钮文案位置、旧 className 的断言 |
| 组件资产 | 语义仍成立的基础组件名和导出入口 | 旧 visual variant、terminal/glass/neon 命名、页面专属组件样式 |
| 样式资产 | 少量全局 reset、字体、滚动条、基础 token 思路 | 旧装饰背景、旧页面 token、旧 dashboard 工具类、未引用样式 |

## 3. 主要问题诊断

### 3.1 视觉风格混杂

当前页面同时存在以下风格倾向：

- cyber / neon 发光风格。
- terminal-inspired 卡片命名与样式。
- glass / 半透明面板。
- 后台 dashboard 式表格与表单。
- To C 商业化页面的表单与卡片风格。

这些风格单独看都可以成立，但混在同一个产品中会造成强烈割裂感，用户会感觉页面像多次迭代叠加出来，而不是一个完整产品。

### 3.2 信息层级不清晰

以核心页面为例：

- 首页把股票输入、策略选择、推送、大盘复盘、分析按钮、任务、历史、报告塞在同一个高密度工作区内。
- 问股页功能完整，但技能选择、消息列表、输入区、会话列表之间的主次关系不够明确。
- 持仓页承载账户、快照、风险、图表、录入、CSV 导入、流水等大量信息，当前更像内部管理页，缺少投资组合工作台的层级。

### 3.3 样式治理成本偏高

`src/index.css` 中包含大量主题变量、组件类、页面专属类和兼容类。页面中也存在很多直接拼接 Tailwind className 的实现。长期看会带来以下问题：

- 修改一个 token 或工具类可能影响多个页面，但影响范围难以快速判断。
- 同类组件在不同页面有不同 padding、radius、border 和 hover 效果。
- 后续新增页面容易继续复制局部样式，导致风格继续漂移。

### 3.4 通用组件能力未充分收敛

项目已有 `Button`、`Card`、`Input`、`EmptyState`、`PageHeader`、`AppPage`、`SectionCard`、`StatCard` 等通用组件，但页面中仍有大量原生 `button`、`input`、`select`、自定义 `div` 卡片和页面级 className。说明设计系统的默认能力还没有强到足以承接大多数页面场景。

## 4. 产品视觉方向

推荐方向：现代金融智能工作台。新版本应当像一个独立成熟的产品，而不是现有 WebUI 的延续版本。

关键词：

- 专业。
- 清爽。
- 低噪声。
- 数据优先。
- AI 辅助感明确。
- 适合长期阅读分析报告。
- 适合桌面宽屏，也兼顾移动端操作。

视觉原则：

- 浅色优先，暗色兼容。
- 降低强霓虹、强发光、强玻璃拟态的使用频率。
- 使用中性色、大面积留白、清晰边框和柔和阴影建立层级。
- 主色保留蓝青色系，但降低饱和度，让它成为操作强调色而不是背景主角。
- 红/绿/黄等金融状态色只服务于数据状态，不参与大面积装饰。
- 动效克制，用于导航切换、弹层、加载和状态反馈，不用于制造视觉噪声。

本次视觉重构必须整体废弃旧页面观感，包括旧的发光边框、terminal-card、过度透明面板、页面专属按钮样式和局部拼接的 dashboard 风格。新界面不需要与旧界面保持视觉连续性，只需要保持业务功能可达、信息表达更清晰、交互结果与后端契约兼容。

## 5. 重构后排版方案

重构后的排版目标是“任务先行、内容分层、数据可读、操作收敛”。页面不再沿用旧版多面板堆叠结构，而是按用户在当前页面要完成的任务重新组织。

### 5.1 全局排版骨架

桌面端采用稳定工作台结构：

```text
┌──────────────────────────────────────────────────────────────┐
│ App Shell                                                     │
│ ┌──────────────┐ ┌─────────────────────────────────────────┐ │
│ │ Sidebar      │ │ Page / Workspace Content                │ │
│ │ Primary Nav  │ │ ┌─────────────────────────────────────┐ │ │
│ │ Secondary    │ │ │ Page Header / Task Header            │ │ │
│ │ Account/Help │ │ └─────────────────────────────────────┘ │ │
│ └──────────────┘ │ ┌─────────────────────────────────────┐ │ │
│                  │ │ Main Content                         │ │ │
│                  │ └─────────────────────────────────────┘ │ │
│                  └─────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

排版规则：

- 左侧导航固定，主内容区独立滚动。
- 页面顶部只保留当前任务相关标题、说明和主操作。
- 普通页面使用居中内容宽度，数据工作台使用宽屏栅格。
- 主要操作靠近任务入口，低频操作收纳到次级工具栏、菜单、侧栏或底部区域。
- 不再使用大面积装饰光斑、漂浮背景层和只服务旧视觉的 wrapper。

### 5.2 标准内容页排版

适用页面：设置、账号、订阅、订单、发票、公告、管理员后台、协议页面。

```text
PageHeader
  ├─ eyebrow / breadcrumb
  ├─ title
  ├─ description
  └─ actions

Content
  ├─ SectionCard A
  ├─ SectionCard B
  └─ SectionCard C
```

排版规则：

- 最大内容宽度控制在 `1120px` 到 `1280px`。
- 页面纵向间距使用统一 section gap。
- 一个 section 只承载一个明确主题，避免一个卡片塞入多个业务域。
- 表单采用 label、description、control、error 的固定垂直结构。
- 危险操作独立为 danger section，不与普通设置混排。

### 5.3 工作台页面排版

适用页面：首页、持仓、回测。

```text
WorkspaceHeader
  ├─ Main task input / filters
  └─ Primary actions

WorkspaceBody
  ├─ Left / Top secondary rail
  │   ├─ Tasks
  │   └─ History / filters
  └─ Main panel
      ├─ Summary / metrics
      ├─ Chart / report / table
      └─ Context actions
```

排版规则：

- 首屏优先展示主任务入口和最关键结果。
- 次级信息放入辅助 rail、右侧 panel 或折叠区。
- 工作台内部允许全高布局，但每个滚动区域必须边界清晰。
- 指标卡使用 2/3/4 列响应式栅格，数值旁放趋势、状态和说明。
- 表格优先保证数值对齐、列标题稳定、横向滚动清晰。

### 5.4 首页排版

首页重写为“分析工作台”：

```text
┌─────────────────────────────────────────────┐
│ Analyze Bar                                 │
│ [股票搜索输入] [策略] [推送] [分析] [复盘] │
└─────────────────────────────────────────────┘
┌───────────────┬─────────────────────────────┐
│ Task/History  │ Report Workspace            │
│ - Active task │ - Empty guide / report       │
│ - History     │ - Summary cards              │
│ - Batch ops   │ - Report actions             │
└───────────────┴─────────────────────────────┘
```

排版规则：

- 股票搜索是页面最高优先级元素。
- 策略、推送、大盘复盘作为辅助操作，不与主按钮争抢视觉中心。
- 历史记录默认作为辅助 rail，移动端改为 drawer 或 bottom sheet。
- 报告区优先阅读体验，操作按钮集中在报告标题或工具条中。
- 空状态提供示例股票、常用策略和下一步引导。

### 5.5 问股页排版

问股页重写为“AI 投研对话工作台”：

```text
┌───────────────┬────────────────────────────────────┐
│ Sessions      │ Chat Workspace                     │
│ - New chat    │ ┌────────────────────────────────┐ │
│ - History     │ │ Message stream                  │ │
│ - Export      │ │ User bubble / AI document card  │ │
│               │ └────────────────────────────────┘ │
│               │ ┌────────────────────────────────┐ │
│               │ │ Composer + skill chips          │ │
│               │ └────────────────────────────────┘ │
└───────────────┴────────────────────────────────────┘
```

排版规则：

- 会话列表是左侧辅助区，不抢占消息阅读区。
- 用户消息用短气泡，AI 消息用文档卡片，便于承载 Markdown、表格和长段落。
- 输入区固定在底部，技能选择以 chips 或折叠面板呈现。
- 工具调用、思考过程、进度状态放在 AI 消息附近，不打断输入区。
- 移动端默认隐藏会话栏，通过顶部按钮打开。

### 5.6 持仓页排版

持仓页重写为“投资组合工作台”：

```text
PortfolioHeader
  ├─ Account switcher
  ├─ Cost method
  └─ Refresh / Create account

MetricGrid
  ├─ 总权益
  ├─ 总市值
  ├─ 总现金
  └─ 汇率状态

MainGrid
  ├─ Positions Table
  └─ Risk / Concentration Panel

Operations
  ├─ Manual entry
  ├─ CSV import
  └─ Ledgers
```

排版规则：

- 顶部先回答“当前看哪个账户、用什么成本口径、数据是否最新”。
- 指标区固定在表格之前，先给组合概览，再进入明细。
- 持仓表是主内容，风险与集中度作为右侧解释面板。
- 录入、导入、流水属于操作区，不与持仓阅读区混在同一层。
- 缺价、汇率过期、写保护、风险降级提示放在相关模块附近。

### 5.7 回测页排版

回测页重写为“策略评估工作台”：

```text
BacktestHeader
  ├─ Strategy / symbol / date range
  └─ Run action

ResultSummary
  ├─ 收益
  ├─ 回撤
  ├─ 胜率
  └─ 交易次数

AnalysisGrid
  ├─ Equity curve / drawdown chart
  ├─ Trades table
  └─ Parameters / execution log
```

排版规则：

- 参数配置和执行按钮放在顶部，结果展示放在下方。
- 核心指标先于图表，图表先于明细表。
- 执行日志和参数详情作为辅助信息，不抢占主图表区域。

### 5.8 响应式排版

桌面端：

- `>=1280px`：侧边栏 + 宽屏工作台。
- `1024px-1279px`：侧边栏保持，工作台减少列数。
- `768px-1023px`：辅助 rail 可折叠，主内容保持单列或双列。

移动端：

- 侧边栏改为 drawer。
- 工作台辅助区改为 drawer、bottom sheet 或折叠 section。
- 顶部主操作保留，次级操作进入菜单。
- 表格使用横向滚动、卡片化摘要或列裁剪策略。
- 聊天输入区固定底部，会话列表默认隐藏。

### 5.9 阅读与数据排版

报告与 Markdown：

- 报告正文使用较窄阅读宽度，避免超长行。
- 标题、列表、引用、表格、代码块、风险提示必须有清晰层级。
- 报告操作集中在顶部工具条，不插入正文中间。

表格：

- 数字列右对齐，文本列左对齐。
- 表头 sticky 视具体高度决定。
- 空状态、加载态、错误态必须内嵌在表格容器内。

卡片：

- 卡片只表达一个主题。
- 重要数字使用更强字号和权重，解释文字降低权重。
- 卡片之间使用统一 gap，不依靠阴影制造层级。

## 6. 设计系统重构方案

### 6.1 Design Tokens

建议收敛为少量可解释 token，优先保证全局一致：

| Token 类型 | 建议内容 | 用途 |
| --- | --- | --- |
| 颜色 | background、foreground、card、muted、border、primary、success、warning、danger | 页面基础、文本、卡片、状态色 |
| 表面 | surface-base、surface-raised、surface-muted、surface-overlay | 页面底色、卡片、浮层、工具栏 |
| 边框 | border-subtle、border-default、border-strong、border-focus | 容器、输入框、选中态 |
| 阴影 | shadow-sm、shadow-md、shadow-lg | 卡片、弹层、固定操作区 |
| 圆角 | radius-sm、radius-md、radius-lg、radius-xl、radius-2xl | 组件与容器 |
| 间距 | page-padding、section-gap、card-padding、control-height | 布局节奏 |
| 字体 | title、subtitle、body、caption、label | 文本层级 |

不建议继续扩展大量页面专属变量，例如 `--home-*`、`--chat-*`、`--settings-*`。页面确实需要特殊风格时，也应基于全局 token 组合，而不是另起一套色彩系统。

### 6.2 通用组件

首批需要重写或替换的组件：

| 组件 | 目标 |
| --- | --- |
| `Button` | 按新交互规范重写 primary、secondary、outline、ghost、danger 等变体；移除页面专属按钮变体 |
| `Card` | 重写为产品级 Surface/Card 体系，废弃 terminal 命名和旧 gradient/glass 语义 |
| `Input` / `Select` / `Checkbox` / `Textarea` | 建立统一表单控件体系，覆盖高度、说明、错误、禁用、loading、focus 状态 |
| `PageHeader` | 重写为页面任务入口，支持标题、说明、面包屑/eyebrow、主操作和次操作 |
| `AppPage` | 重写为标准页面容器，支持普通页面、宽屏工作台、沉浸页面三类密度 |
| `EmptyState` | 重写为空状态、首次引导、无数据、无权限、加载失败等状态组件 |
| `InlineAlert` / `ApiErrorAlert` | 重写提示体系，统一 info/success/warning/danger 与 API 错误呈现 |
| `StatCard` | 重写指标卡，支持趋势、状态、说明、快捷操作和 skeleton |
| `Toolbar` | 重写筛选、批量操作、页面操作栏，减少页面内重复拼装 |
| `DataTable` | 新增或抽象表格基础能力，统一密度、空态、横向滚动、数值对齐和移动端策略 |
| `PageShell` / `WorkspaceLayout` | 新增或重写页面级布局组件，承接工作台和沉浸式页面 |

### 6.2.1 通用组件迁移契约

通用组件重写时要先稳定“组件承担什么语义”，再决定具体 JSX 和样式。建议按以下契约推进，避免每个页面自行补洞：

| 组件 | 必备 props / 能力 | 禁止事项 | 优先迁移页面 |
| --- | --- | --- | --- |
| `Button` | `variant`、`size`、`loading`、`disabled`、图标前后缀、危险操作样式 | 禁止新增页面专属 variant；禁止用 className 覆盖核心 padding、radius、颜色 | 全部页面 |
| `Input` / `Textarea` | label 关联、description、error、prefix/suffix、loading/disabled、可访问 focus | 禁止页面自行拼 label/error；禁止输入框高度漂移 | 首页、设置、认证页 |
| `Select` | label、placeholder、empty、error、disabled、键盘可达 | 禁止用原生 select 与自定义 select 长期并存两套视觉 | 首页、持仓、回测、设置 |
| `Card` / `SectionCard` | header、description、actions、footer、density、surface variant | 禁止 terminal/glass/gradient 语义继续保留为正式 variant | 首页、持仓、账号类页面 |
| `PageHeader` | eyebrow/breadcrumb、title、description、actions、meta | 禁止页面自行复制 header 结构 | 标准内容页 |
| `EmptyState` | icon、title、description、primaryAction、secondaryAction、示例内容 | 禁止只显示“暂无数据”而不说明下一步 | 首页、问股、持仓、表格 |
| `InlineAlert` / `ApiErrorAlert` | status、title、description、actions、details 展开 | 禁止业务关键错误只进 toast | 设置、持仓、回测、账号 |
| `StatCard` | label、value、unit、trend、status、description、loading | 禁止数值格式和涨跌色在页面内散落实现 | 持仓、回测、首页摘要 |
| `Toolbar` | filters、primary action、secondary actions、批量操作、移动端折叠 | 禁止页面重复拼同类筛选栏 | 表格类页面 |
| `DataTable` | columns、rowKey、loading、empty、error、pagination、density、数字列对齐、移动端策略 | 禁止每页独立实现表格空态、错误态和横向滚动 | 持仓、订单、发票、管理员 |
| `Drawer` / `ConfirmDialog` | focus trap、Escape、aria、尺寸、footer actions | 禁止弹层没有焦点回收或用普通 div 伪装对话框 | 移动端导航、危险操作 |

组件迁移的完成标准：

- 组件的默认样式已经符合新视觉，不需要页面额外补大量 className。
- 组件导出入口稳定，页面通过统一入口引用，避免出现多个平行版本。
- 组件测试覆盖基础交互、禁用、loading、错误和可访问标签。
- 旧 variant 如果只服务旧视觉，应删除；如果短期保留，必须标记迁移页面和删除阶段。

### 6.2.2 页面组件拆分建议

复杂页面重写时不建议在单个 `Page.tsx` 中堆完整 JSX。建议按“页面容器 + 业务区块 + 展示原语”拆分：

| 页面 | 页面容器 | 建议拆出的业务区块 |
| --- | --- | --- |
| 首页 | `HomePage` | `AnalyzeCommandBar`、`AnalysisTaskRail`、`ReportWorkspace`、`ReportEmptyGuide`、`ReportActionBar` |
| 问股 | `ChatPage` | `ChatSessionRail`、`ChatMessageStream`、`AssistantMessageCard`、`ChatComposer`、`SkillChipPicker` |
| 持仓 | `PortfolioPage` | `PortfolioControlBar`、`PortfolioMetricGrid`、`PositionsTable`、`PortfolioRiskPanel`、`PortfolioOperationsPanel` |
| 回测 | `BacktestPage` | `BacktestConfigBar`、`BacktestMetricGrid`、`BacktestChartsPanel`、`TradesTable`、`BacktestExecutionLog` |
| 设置 | `SettingsPage` | `ProviderConfigSection`、`NotificationConfigSection`、`SystemConfigSection`、`DangerZoneSection` |
| 账号/商业化 | `AccountPage` / `BillingPage` | `PlanSummaryCard`、`QuotaUsagePanel`、`OrderTable`、`InvoiceRequestPanel` |

拆分规则：

- 页面容器负责数据获取、业务状态和页面级布局。
- 业务区块负责局部状态展示和用户操作入口。
- 展示原语只负责 UI 呈现，不直接调用 API。
- 业务 hooks/store 不因 UI 重构而大规模改名或迁移。
- 不为迁就旧页面结构保留无语义 wrapper。

### 6.3 布局组件

建议明确三类布局，并允许为这些布局新增专门组件，而不是继续让页面自行拼接：

| 布局 | 适用页面 | 特征 |
| --- | --- | --- |
| 标准内容页 | 设置、账号、账单、公告、后台 | `Shell` + `AppPage` + `PageHeader` + section/card |
| 工作台页 | 首页、持仓、回测 | 宽屏栅格、概览区、主内容区、辅助面板 |
| 沉浸式对话页 | 问股 | 全高布局、会话侧栏、消息流、固定输入区 |

页面重构应优先拆出稳定布局原语，例如：

- `StandardPageLayout`：普通设置、账号、账单、公告、后台页面。
- `WorkspacePageLayout`：首页、持仓、回测等数据工作台。
- `ChatWorkspaceLayout`：问股页的会话、消息流和输入区。
- `AuthLayout`：登录、注册、找回密码、邮箱验证和首次引导。
- `LegalLayout`：协议、隐私和风险披露等公开阅读页。

### 6.4 信息架构与导航建议

删除旧前端后，导航不应简单照搬旧菜单顺序，而应按用户任务重新分组。

建议主导航分为四类：

| 分组 | 页面 | 排版目标 |
| --- | --- | --- |
| 投研入口 | 首页、问股 | 最短路径进入“分析股票 / 追问 AI” |
| 资产与策略 | 持仓、回测 | 围绕组合、风险、策略验证组织 |
| 账户与商业化 | 我的、订阅、订单、发票、API Key | 统一账号、权益、消费记录和 BYOK 管理 |
| 系统与支持 | 设置、公告、帮助、管理员后台 | 低频但必须可达 |

导航规则：

- 一级导航只保留高频入口，避免把所有子功能摊平。
- 账号、订单、发票、API Key 不必全部放入主导航，可在“我的”页面内作为二级入口。
- 帮助、主题切换、退出、配额放在侧边栏底部稳定区域。
- 移动端导航只展示主入口，二级功能进入页面内 tab 或菜单。
- 管理员入口仅在有权限时展示，且不挤占普通用户主流程。

### 6.5 交互状态与反馈建议

新前端必须系统化处理状态，而不是每个页面临时拼提示。

必须覆盖的状态：

| 状态 | 排版规则 |
| --- | --- |
| Loading | 使用 skeleton 或局部 loading，不用整页空白转圈替代所有场景 |
| Empty | 说明“为什么为空”和“下一步做什么” |
| Error | 错误信息靠近发生区域，API 错误可展开技术细节 |
| Success | 操作完成后给短反馈，不长期占用主内容 |
| Disabled | 禁用原因可通过说明、tooltip 或 inline hint 呈现 |
| Quota exceeded | 作为产品引导状态处理，而不是普通错误 |
| Read-only / write-blocked | 明确告诉用户当前不能写入的原因和解除方式 |

交互规则：

- 主操作按钮每个区域最多一个。
- destructive 操作必须二次确认。
- 长任务必须展示进度、当前阶段和可取消/可关闭语义。
- 表单提交失败后，焦点和错误提示应回到对应字段或表单顶部。
- 全局 toast 只承载轻量反馈，业务关键错误必须在页面内展示。

### 6.6 数据可视化建议

金融产品的数据展示应优先保证可读性，不追求炫技。

图表规则：

- 收益、回撤、趋势类使用折线图或面积图。
- 集中度、配置占比使用环形图或条形图；当分类超过 6 个时优先条形图。
- 红绿状态色只用于涨跌、盈亏和风险状态，不用于普通装饰。
- 图表必须有标题、口径说明、更新时间或数据来源说明。
- tooltip 需要展示单位、币种、百分比和日期，不只展示裸数字。
- 图表空态必须说明缺少什么数据，以及如何产生数据。

表格规则：

- 金额、数量、百分比、日期使用统一格式化。
- 金额和百分比右对齐，代码和名称左对齐。
- 表格操作列固定在右侧或收纳进行级菜单。
- 高密度表格默认不使用大阴影，靠边框、分隔线和 hover 建立可读性。
- 移动端表格优先横向滚动；过宽业务表可提供卡片化摘要。

### 6.7 可访问性与键盘操作建议

完整重构不应只追求视觉，也要顺手修正旧 UI 中的可访问性短板。

最低要求：

- 所有交互元素必须有可见 focus 状态。
- 图标按钮必须有 `aria-label`。
- Drawer、Dialog、Popover 必须支持 Escape 关闭和焦点回收。
- 表单字段必须有关联 label，错误信息应能被辅助技术读取。
- 颜色不能作为唯一状态表达；涨跌、成功、失败还应有文字或图标。
- Markdown 报告中的标题层级应连续，不跳级。
- 移动端点击目标不小于合理触控尺寸。

键盘路径：

- 侧边栏导航可 Tab 访问。
- 搜索框、策略选择、分析按钮形成清晰 tab order。
- 问股页输入框支持键盘发送，但必须保留换行方式。
- 表格行内操作可以通过键盘触达。

### 6.8 性能与渲染建议

删除旧前端时应避免把新界面做成更重的界面。

性能规则：

- 减少全局大面积 blur、backdrop-filter、复杂 box-shadow 和无限动画。
- 长列表使用分页、虚拟化或明确的加载更多策略。
- Markdown 长报告渲染应避免不必要的全量重渲染。
- Recharts 图表只在容器尺寸和数据变化时重渲染。
- 聊天流式输出时避免每个 token 触发布局抖动。
- 首屏页面不要等待低优先级数据才展示主任务入口。
- 图片、动态图、装饰背景默认不进入首屏关键路径。

### 6.9 删除式重构完成定义

每个页面完成重写后，必须同时满足以下条件，才算真正完成：

- 页面不再依赖旧 `terminal-card`、旧 `glass-panel`、旧 neon/glow 页面样式。
- 页面不再使用只为旧布局服务的 wrapper 和 decorative layer。
- 页面主结构由新布局组件承载，而不是页面内大段临时 className 堆叠。
- 页面内按钮、表单、卡片、提示、表格优先使用新通用组件。
- 该页面对应的旧 CSS、旧 variant、旧页面专属 token 已删除。
- 关键用户路径测试或手工验证已覆盖。
- 移动端、暗色模式、加载态、空态、错误态已检查。

## 7. 全局布局重构方案

### 7.1 Shell

`Shell` 应重写为全局产品框架，而不是只负责套一个 sidebar。

建议结构：

- 页面背景使用统一 `bg-background` 和新 surface token，移除旧的大面积 decorative blur。
- 桌面端左侧固定导航，移动端使用 drawer。
- 主内容区按页面类型区分标准页面和沉浸页面。
- 预留顶部移动端导航按钮和主题切换入口。
- 保留 `Outlet` 和当前路由语义，不改变认证保护逻辑。

### 7.2 SidebarNav

侧边栏应转为低噪声 SaaS 导航，并整体替换旧实现的视觉结构：

- Logo 区简洁化，突出 DSA 与产品定位。
- 主导航按业务优先级排列：首页、问股、持仓、回测、设置、公告、我的。
- 当前激活项使用柔和背景、文字权重和左侧细条，不使用强发光。
- 配额、帮助、主题、退出放在底部稳定区域。
- 通知角标保留，但降低视觉侵入性。

### 7.3 页面 Header

每个标准页面使用重写后的统一 `PageHeader`：

- eyebrow 可选，用于功能分组。
- title 清楚表达页面任务。
- description 控制在一到两行。
- actions 放置主操作。
- 不建议每个页面自行实现完全不同的 header。

## 8. 核心页面重构方案

### 8.1 首页 / 个股分析页

首页是当前最重要的页面，应优先完整重写 UI 结构。

#### 当前问题

- 主输入区和次级操作拥挤。
- 历史记录、任务、报告之间层级不够清晰。
- 空状态缺少产品引导。
- 报告操作按钮视觉层级偏弱。

#### 目标结构

建议重写为“分析工作台”：

1. 顶部分析启动区。
   - 大号股票搜索框。
   - 主操作：开始分析。
   - 次操作：大盘复盘。
   - 辅助选项：策略选择、推送通知。
2. 左侧辅助面板。
   - 当前任务。
   - 历史报告。
   - 批量删除等低频操作。
3. 主报告区。
   - 无报告时展示能力说明、示例股票、最近动作。
   - 有报告时展示报告摘要和操作按钮。
   - 完整 Markdown 报告可重新设计为 drawer、侧边阅读模式或独立阅读视图，以新体验为准。
4. 错误与配置提醒。
   - 输入错误、重复任务、基础配置缺失放在启动区下方。
   - 避免打断报告阅读流。

#### 交互细化

- 股票搜索框默认聚焦优先级最高，支持输入代码、名称和市场前缀。
- 策略选择默认收纳为 compact select 或 popover，不应占据主任务入口同等视觉权重。
- 推送通知应以开关或次级选项呈现，并解释会发送到哪里。
- 大盘复盘是次级 CTA，不能强于“开始分析”。
- 当前任务区显示任务阶段、开始时间、可刷新/关闭状态；不要和历史报告混在同一列表。
- 历史报告支持按股票、时间或状态扫描，但批量删除等危险/低频操作要收纳。
- 报告阅读区应支持摘要、完整报告、复制/下载/发送等操作，操作集中在报告工具条。
- 空状态至少提供 3 类入口：示例股票、常用策略、查看历史。

#### 数据与状态保持

- 保持现有分析 API、历史 API、任务轮询和报告载荷语义。
- 保留关键用户路径测试所需的稳定测试标识，但不保留旧 DOM 嵌套。
- 分析中、成功、失败、无配置、配额不足、网络错误应分别有明确 UI 状态。
- Markdown 报告渲染使用新阅读样式，不在页面内追加独立 Markdown 风格。

#### 预期收益

- 用户一进入首页就知道“输入股票 -> 选择策略 -> 开始分析”。
- 历史和任务从主流程中退到辅助区域。
- 报告阅读区域更干净。

### 8.2 问股页

问股页是 AI 体验入口，应彻底重写为对话式投研工作台，强调对话、工具调用过程和长报告阅读。

#### 当前问题

- 会话、技能、输入、消息都在同一视觉权重下。
- 技能选择区占用输入区上方较多空间。
- AI 消息虽然支持 Markdown，但更像普通聊天气泡，不利于长报告阅读。

#### 目标结构

建议重写为“AI 投研对话页”：

1. 左侧会话栏。
   - 新建对话。
   - 会话列表。
   - 删除/导出等低频动作。
2. 中央消息流。
   - 用户消息短气泡。
   - AI 消息使用文档卡片风格。
   - Markdown 内容更接近报告阅读排版。
3. 底部输入 composer。
   - 文本输入为核心。
   - 技能选择改为 compact chips 或可折叠区域。
   - 发送、通知、导出等操作层级分明。
4. 空状态。
   - 提供高质量问题模板。
   - 区分个股分析、策略解读、报告追问、大盘判断。

#### 交互细化

- 左侧会话栏在桌面端常驻，在移动端通过顶部按钮或手势 drawer 打开。
- 会话列表应展示标题、最近更新时间和简短摘要；删除操作进入更多菜单或二次确认。
- 技能选择默认以 chips 呈现，最多 3 个的限制要在交互中提前提示，而不是只在提交后报错。
- 用户消息保持短气泡，AI 消息使用可阅读文档卡，支持 Markdown、表格、列表、引用和工具状态。
- 流式输出时提供“生成中”状态、自动滚动控制和停止/重试入口。
- 输入 composer 支持 Enter 发送、Shift+Enter 换行；移动端不应遮挡最后一条消息。
- 空会话应提供 prompt 模板，模板点击后填入输入框而不是立即发送，避免误触。

#### 数据与状态保持

- 保持 agent chat store、当前路由同步、会话上下文和追问语义。
- 保持技能选择上限、流式响应、错误重试、会话导出/删除等行为。
- 工具调用状态、错误和空回答应靠近对应 AI 消息展示。
- 长回答重渲染应克制，避免流式输出造成滚动抖动。

#### 预期收益

- 用户更容易把问股页理解为 AI 投研助手，而不是普通聊天窗口。
- 长回答的阅读体验更好。
- 技能选择仍可见但不喧宾夺主。

### 8.3 持仓页

持仓页信息密度最高，应彻底重写为投资组合工作台。

#### 当前问题

- 账户筛选、创建账户、统计卡、持仓表、图表、风险、录入、CSV 导入、流水平铺在长页面中。
- 用户难以快速判断当前组合状态和下一步动作。
- 表格、表单和图表风格偏后台。

#### 目标结构

建议重写为“投资组合工作台”：

1. 顶部控制区。
   - 账户视图。
   - 成本口径。
   - 刷新数据。
   - 新建账户入口。
2. 概览指标区。
   - 总权益。
   - 总市值。
   - 总现金。
   - 汇率状态。
3. 主分析区。
   - 左侧持仓明细表。
   - 右侧集中度图表和风险摘要。
4. 操作区。
   - 手工录入。
   - CSV 导入。
   - 交易/现金/公司行动流水。
5. 风险提示。
   - 降级、缺价、汇率过期、写保护等提示固定在相关区域附近。

允许引入新的页面分组方式，例如“概览 / 持仓 / 交易 / 导入”标签页、分栏工作台或可折叠面板。选择哪一种以最终用户任务清晰度为准，不需要受当前长页面结构约束。

#### 交互细化

- 顶部账户切换应明确当前账户、币种/市场覆盖、成本口径和最后更新时间。
- 组合指标卡应优先展示总权益、总市值、现金、收益/风险或汇率状态，并说明口径。
- 持仓表默认是主视图，列密度可调；金额、数量、比例右对齐，股票代码和名称左对齐。
- 风险面板应解释集中度、缺价、汇率过期和风险降级，而不是只显示图表。
- 手工录入、CSV 导入和流水管理可进入 tab、drawer 或操作面板，避免挤压持仓阅读空间。
- 写保护或只读模式下，所有写操作入口必须保留可见但禁用，并解释原因。

#### 数据与状态保持

- 保持账户、持仓、现金流水、交易流水、公司行动、价格刷新、汇率刷新等 API 语义。
- 保持 CSV 导入字段契约、错误反馈和导入结果展示。
- 缺价、缺汇率、风险降级、空账户、无持仓、写保护是独立状态，不能合并成一个普通错误。
- 高风险操作必须通过统一确认组件处理。

### 8.4 回测页

回测页应与持仓页保持一致的数据工作台风格：

- 顶部配置区。
- 关键指标卡。
- 图表区。
- 结果表格区。
- 参数与执行日志区。

回测页可以按新数据工作台范式重写页面结构。业务参数、API 调用和结果语义保持兼容，但配置区、结果区、图表区和执行状态可以重新组织。

细化要求：

- 配置区应清晰区分必填参数、可选参数和高级参数。
- 执行按钮只在参数有效时可用，参数错误应定位到对应字段。
- 执行中展示阶段、耗时和可取消/可关闭语义；不应只用全页 loading。
- 结果摘要先展示收益、回撤、胜率、交易次数等核心指标。
- 图表区应说明数据口径，tooltip 展示日期、收益、回撤和单位。
- 交易明细表支持空态、错误态、横向滚动和数值对齐。
- 参数快照和执行日志作为辅助信息展示，便于复现实验。

### 8.5 设置与账号类页面

设置、账号、API Key、订阅、订单、发票、管理员页面应使用标准内容页布局：

- `AppPage` 包裹。
- `PageHeader` 统一标题与说明。
- 使用 `SectionCard` 分组。
- 表单项高度、label、说明、错误状态统一。
- 危险操作使用一致的 warning/danger 区块。
- 允许拆分过长页面，改为分组、标签页、侧栏锚点或多卡片信息架构。

细化要求：

- 设置页按模型、数据源、通知、系统、安全/危险操作分组。
- 保存类操作优先使用 section 内局部保存，避免用户不知道保存范围。
- 连接测试、密钥校验和配置错误应在对应 section 内展示。
- API Key、订单、发票、管理员页面应统一使用 `DataTable` 或同等表格原语。
- 账号页聚合用户资料、权益、配额和续费入口，不把所有商业化子页面摊到主导航。
- 管理员页面可以保持更高信息密度，但仍应使用新 token、表格和提示体系。

### 8.6 登录、注册和验证页面

认证页面是 To C 用户的第一印象，应与产品主视觉统一：

- 左侧可选品牌说明区。
- 右侧表单卡片。
- 清晰展示登录、注册、找回密码、邮箱验证状态。
- 协议链接、错误提示、成功提示保持统一。
- 移动端优先保证表单易用性。

细化要求：

- 登录页需要同时兼容管理员密码登录和 To C 用户登录的选择逻辑。
- `redirect` 参数语义保持不变，登录成功后回到原目标页面。
- 注册页必须清晰展示协议、隐私和风险披露链接。
- 找回密码、邮箱验证和首次引导应使用同一认证视觉体系。
- 认证错误不只显示 toast，应在表单顶部或字段附近展示。

### 8.7 页面级验收清单

每个页面完成重写后，提交前按以下清单自检：

| 检查项 | 要求 |
| --- | --- |
| 主任务 | 用户进入页面 3 秒内能判断当前页面能做什么 |
| 主操作 | 每个视觉区域最多一个 primary action |
| 状态 | loading、empty、error、success、disabled 至少覆盖当前页面关键路径 |
| 响应式 | 桌面、平板、移动端均有明确布局策略 |
| 深色模式 | 文本、边框、图表、状态色在深色模式下可读 |
| 可访问性 | 表单 label、图标按钮 `aria-label`、focus 状态、弹层键盘行为可用 |
| 数据格式 | 金额、百分比、数量、日期、股票代码和币种格式一致 |
| 旧样式 | 不再引用该页面旧 token、旧 wrapper、terminal/glass/neon 类 |
| 测试 | 对应页面测试更新到新用户路径，不断言旧 DOM 结构 |

## 9. 样式治理计划

### 9.1 重建样式文件体系

`index.css` 体量较大，且混合了 token、base、组件、页面专属样式和兼容类。彻底重构时不应继续把新体系堆回同一个大文件，而应重建样式文件组织。

推荐目标结构：

- `src/styles/tokens.css`：颜色、半径、阴影、间距、字体等 CSS variables。
- `src/styles/base.css`：全局 reset、body、链接、滚动条、基础排版。
- `src/styles/utilities.css`：项目级工具类，数量必须克制。
- `src/styles/components.css`：仅放通用组件无法完全用 Tailwind 表达的样式。
- `src/styles/markdown.css`：报告和 AI 消息 Markdown 阅读体验。
- `src/styles/pages.css`：仅允许作为页面重写过程中的临时中转样式；对应页面完成后必须清空或删除相关旧样式。
- `src/index.css`：只保留 Tailwind import、config import 和上述样式入口。

建议入口关系：

```css
@import "tailwindcss";
@import "./styles/tokens.css";
@import "./styles/base.css";
@import "./styles/utilities.css";
@import "./styles/components.css";
@import "./styles/markdown.css";
@import "./styles/pages.css";
```

`pages.css` 只是迁移缓冲区，不是新页面样式长期归宿。每个页面重构 PR 都应说明本次新增/保留/删除了哪些页面样式。

### 9.1.1 Token 命名建议

新 token 应采用“语义优先、少量稳定”的命名方式。页面不直接引用原始色值，也不新增页面私有色板。

| 类别 | 建议 token | 说明 |
| --- | --- | --- |
| 基础背景 | `--color-background`、`--color-foreground` | 页面背景和主文字 |
| 表面 | `--color-surface`、`--color-surface-muted`、`--color-surface-raised`、`--color-surface-overlay` | 卡片、工具栏、浮层 |
| 文本 | `--color-muted-foreground`、`--color-subtle-foreground` | 次级说明、辅助信息 |
| 边框 | `--color-border`、`--color-border-strong`、`--color-focus-ring` | 分割、容器和 focus |
| 品牌/操作 | `--color-primary`、`--color-primary-foreground`、`--color-primary-muted` | 主操作和选中状态 |
| 状态 | `--color-success`、`--color-warning`、`--color-danger`、`--color-info` | 成功、警告、失败、信息 |
| 金融数据 | `--color-gain`、`--color-loss`、`--color-neutral-market` | 涨跌、盈亏和中性行情 |
| 图表 | `--chart-1` 到 `--chart-6` | 图表序列色，避免页面硬编码 |
| 半径 | `--radius-sm`、`--radius-md`、`--radius-lg`、`--radius-xl` | 组件层级 |
| 阴影 | `--shadow-card`、`--shadow-popover`、`--shadow-dialog` | 克制使用，不承担主要层级表达 |
| 间距 | `--space-page-x`、`--space-section`、`--space-card` | 页面和卡片节奏 |

命名禁区：

- 不新增 `--home-*`、`--chat-*`、`--portfolio-*` 这类页面私有 token。
- 不新增 `--neon-*`、`--cyber-*`、`--terminal-*` 这类旧视觉语义 token。
- 不让颜色 token 表达具体业务文案，例如 `--buy-button-color`。
- 不在组件内部硬编码十六进制颜色；必须通过 token 或 Tailwind 映射引用。

### 9.2 旧前端删除原则

旧前端 UI 资产不是兼容目标，而是删除目标。重构过程中如果为了避免一次提交过大而暂时留下旧样式或旧组件引用，只能作为过渡状态；对应页面完成重构后必须立即删除，不允许形成长期兼容层。

必须删除或替换以下类别：

- 旧的强发光、强 neon、terminal 命名类。
- 页面专属按钮类，例如无法融入新 `Button` 体系的旧类。
- 重复的 `--home-*`、`--chat-*`、`--settings-*`、`--portfolio-*` token。
- 未被页面引用的旧组件工具类。
- 与新设计系统冲突的 gradient、glass、shadow 类。
- 旧页面结构中只为旧视觉服务的 wrapper、decorative layer、局部布局容器。
- 旧组件 API 中只为旧页面样式服务的 variant 和 className 约定。

### 9.3 重写与删除策略

旧 UI 删除按页面批次推进：

1. 建立新 token 和基础样式。
2. 重写通用组件并让新页面只使用新组件。
3. 每完成一个页面，立即删除该页面对应的旧样式和旧 UI wrapper。
4. 所有页面重写完成后删除 `index.css` 中的旧兼容层。
5. 用 lint、build、测试和人工页面检查确认无旧样式依赖。

### 9.4 旧样式发现与删除清单

删除旧样式前先用搜索确认引用范围。重点搜索以下关键词和模式：

| 类型 | 搜索目标 | 处理方式 |
| --- | --- | --- |
| 旧视觉命名 | `terminal`、`cyber`、`neon`、`glow`、`glass` | 新页面禁止引用；迁移后删除类和 variant |
| 页面私有 token | `--home-`、`--chat-`、`--portfolio-`、`--settings-`、`--dashboard-` | 合并到全局 token 或删除 |
| 旧按钮类 | `btn-primary`、`btn-secondary`、页面私有 `*-button` | 替换为 `Button` 组件 variant |
| 旧卡片类 | `terminal-card`、`glass-panel`、`dashboard-card` | 替换为 `Card` / `SectionCard` / layout 组件 |
| 旧装饰层 | `ParticleBackground`、decorative blur、orb、grid background | 默认删除，除非新视觉明确需要且实现克制 |
| 页面临时布局 | `home-*`、`chat-*`、`portfolio-*` class | 页面重写完成后删除 |

删除步骤：

1. 先搜索引用，确认没有被未迁移页面使用。
2. 在同一 PR 中替换页面引用和删除对应 CSS，避免留下死样式。
3. 如果暂时不能删除，必须在 PR 说明中写明依赖页面和计划删除阶段。
4. 删除后运行 lint/build，必要时补充页面测试。

### 9.5 Tailwind 使用约束

Tailwind 可以继续使用，但不能回到“页面内巨型 className 堆叠”的模式。

- 布局类、少量间距类和响应式类可以在页面使用。
- 颜色、阴影、圆角、控件高度、状态色应优先由组件 variant 和 token 承担。
- 连续超过一屏的复杂 className 组合应抽为组件或布局原语。
- 不允许通过任意值大量硬编码颜色、阴影、宽高，例如 `bg-[#...]`、`shadow-[...]`。
- 页面级特殊样式如果必须存在，应先判断是否能上升为组件能力。

## 10. 分阶段实施计划

### Phase 0：重构基线与页面盘点

目标：在开始大规模改动前明确页面清单、功能入口、测试基线、旧 UI 删除清单和必须保持的业务契约。

范围：

- 梳理所有路由、页面、弹窗、drawer、全局 banner 和 API 依赖。
- 标记必须延续的用户任务路径、业务结果和必要 `data-testid`；旧 DOM 层级和旧视觉结构不作为保留对象。
- 确认移动端、桌面端、深色模式、登录态、未登录态和用户模式开关的覆盖范围。
- 记录旧样式和旧组件的删除候选清单。

验收标准：

- 有明确页面重写清单和旧 UI 删除清单。
- 有明确测试基线和手工检查清单。
- 重构不再以旧页面 DOM 结构为约束，只以业务行为和可达性为约束。

建议产物：

- 页面迁移看板：页面、优先级、负责人、依赖组件、保留业务路径、删除样式范围。
- 旧样式清单：类名/token、引用页面、删除阶段、替代组件。
- 测试基线清单：现有单测、E2E、需要新增或改写的断言。
- 视觉基线截图：至少覆盖首页、问股、持仓、设置、登录页、移动端导航。
- 风险登记：认证、配额、支付、持仓写操作、CSV 导入、流式输出等高风险路径。

### Phase 1：新设计系统与全局骨架

目标：建立全新的 UI 基础设施，并让后续页面只依赖新体系。

范围：

- `src/styles/` 新样式目录。
- `src/index.css` 入口重组。
- 新 design tokens。
- 新 `Shell`、`SidebarNav`、移动端导航和应用背景。
- 重写 `Button`、`Card`、`Input`、`Select`、`Textarea`、`Checkbox`、`Badge`、`Alert`、`EmptyState`、`PageHeader`、`AppPage`。
- 新增或重写 `WorkspacePageLayout`、`StandardPageLayout`、`ChatWorkspaceLayout`、`AuthLayout`。

验收标准：

- 旧视觉风格在新基础组件中不再出现。
- 新页面可以不依赖旧 `terminal-card`、`glass-panel`、页面专属按钮类。
- Shell、导航、移动端 drawer、主题切换、配额入口和帮助入口可用。
- `npm run lint` 和 `npm run build` 通过。

实施顺序建议：

1. 新建 `src/styles/`，拆分 token、base、utilities、components、markdown 和临时 pages。
2. 改造 `index.css` 为样式入口，暂时保留必要旧样式但加迁移归属。
3. 重写最基础组件：`Button`、`Card`、`Input`、`Select`、`InlineAlert`、`EmptyState`。
4. 重写布局组件：`Shell`、导航、`AppPage`、`PageHeader`、移动端 drawer。
5. 用一个低风险页面试接新布局，验证 token、深色模式、响应式和通用组件 API。

阶段边界：

- 本阶段可以不完成所有页面视觉替换。
- 本阶段不应重写业务 hooks/store。
- 本阶段结束后，后续新页面不得再新增旧视觉语义。

### Phase 2：首页完整重构

目标：重写首页为新的分析工作台。

范围：

- 重写 `src/pages/HomePage.tsx` 的 JSX 结构和页面布局。
- 重写或替换 `StockAutocomplete`、`HistoryList`、`TaskPanel` 的 UI。
- 调整 `ReportSummary` 和 `ReportMarkdown` 的阅读体验。
- 删除首页迁移后不再使用的旧 home 样式。

验收标准：

- 主任务路径清晰：输入股票、选择策略、开始分析。
- 历史和任务作为辅助区域，不干扰主报告阅读。
- 空状态具备有效引导。
- 报告操作按钮层级清晰。
- 首页相关测试无回归，或按新结构同步更新测试断言。

建议 PR 范围：

- 页面结构：`HomePage` 拆为分析启动区、任务/历史 rail、报告工作区。
- 通用组件：补齐 `StockAutocomplete` 与新输入体系的视觉一致性。
- 报告阅读：统一 Markdown 样式和报告工具条。
- 删除范围：首页专属旧样式、旧按钮类、旧报告容器、旧装饰层。

手工检查路径：

- 输入合法股票并发起分析。
- 输入非法股票并查看字段错误。
- 切换策略和推送选项。
- 触发大盘复盘。
- 打开历史报告。
- 查看分析中、成功、失败和空状态。
- 移动端打开历史/任务辅助区。

### Phase 3：问股页完整重构

目标：重写问股页为 AI 投研对话工作台。

范围：

- 重写 `src/pages/ChatPage.tsx` 页面结构。
- 重写会话列表、消息流、AI 文档卡片、输入 composer 和技能选择。
- 重写 chat Markdown 样式。
- 删除迁移后不再使用的 chat 旧样式。

验收标准：

- 新建会话、切换会话、删除会话、发送消息、流式加载继续可用。
- 技能选择仍支持最多 3 个技能的限制。
- 追问上下文逻辑不受影响。
- 消息滚动和跳到底部逻辑不回归。
- AI 长回答阅读体验明显优于旧气泡式布局。

建议 PR 范围：

- 页面结构：会话栏、消息流、AI 文档卡、底部 composer。
- 技能选择：从大面积面板收敛为 chips / popover / 折叠区。
- Markdown：复用统一 `markdown.css`，区分报告阅读和聊天文档卡密度。
- 删除范围：chat 页面旧背景、旧消息气泡样式、旧技能选择样式。

手工检查路径：

- 新建会话并发送普通问题。
- 选择 1-3 个技能并发送问题。
- 尝试超过技能上限并查看提示。
- 切换历史会话。
- 删除会话并确认二次确认。
- 查看流式输出、错误重试和滚动到底部。
- 移动端打开/关闭会话栏并输入多行文本。

### Phase 4：持仓与回测完整重构

目标：重写数据密集页面为一致的数据工作台。

范围：

- `src/pages/PortfolioPage.tsx`。
- `src/pages/BacktestPage.tsx`。
- 持仓表格、指标卡、图表、风险摘要、录入和导入区域。
- 回测配置区、指标区、图表区、结果区和执行状态。
- 删除迁移后不再使用的 portfolio/backtest 旧样式。

验收标准：

- 账户切换、成本口径切换、刷新、汇率刷新可用。
- 无账户、缺价、风险降级、写保护状态展示清晰。
- 手工录入和 CSV 导入流程不回归。
- 回测参数、执行和结果展示语义不回归。
- 数据表格在窄屏下仍可横向滚动或转为可读移动端布局。

建议 PR 拆分：

- PR 4A：持仓工作台，包括账户控制、指标、持仓表、风险面板、操作区。
- PR 4B：回测工作台，包括参数配置、执行状态、结果摘要、图表和交易表。

持仓手工检查路径：

- 切换账户和成本口径。
- 刷新价格和汇率。
- 创建账户、手工录入持仓/现金/交易。
- 导入 CSV 并查看导入结果。
- 查看无账户、无持仓、缺价、汇率过期、写保护和风险降级。
- 窄屏查看持仓表和操作区。

回测手工检查路径：

- 配置策略、股票和时间范围。
- 参数非法时检查字段错误。
- 执行回测并查看执行中状态。
- 查看核心指标、图表、交易表和日志。
- 无结果、执行失败和网络错误状态。

### Phase 5：其余页面完整重写

目标：把所有剩余页面按新设计系统完整重写，消除旧页面观感。

范围：

- `SettingsPage`。
- `AccountPage`。
- `BillingPage`。
- `ApiKeysPage`。
- `OrdersPage`。
- `InvoicesPage`。
- `AdminPage`。
- `NoticesPage`。
- 登录、注册、找回密码、邮箱验证、首次引导。
- legal pages。

验收标准：

- 所有页面容器、标题、卡片、表单、提示和按钮风格一致。
- 低频页面没有旧风格残留。
- 认证、账号、商业化和后台行为保持兼容。

建议分组：

| 分组 | 页面 | 重点 |
| --- | --- | --- |
| 设置与系统 | `SettingsPage`、`NoticesPage`、`NotFoundPage` | 标准内容页、错误与空态、公开访问 |
| 账号与商业化 | `AccountPage`、`BillingPage`、`ApiKeysPage`、`OrdersPage`、`InvoicesPage` | 表格、权益、配额、支付/开票状态 |
| 管理员 | `AdminPage` | 高密度数据页、权限态、危险操作 |
| 认证与引导 | `LoginPage`、`UserAuthPage`、`ForgotPasswordPage`、`VerifyEmailPage`、`OnboardingPage` | 表单、redirect、协议、邮箱状态 |
| 公开协议 | `legal/*` | 长文阅读、公开访问、移动端可读性 |

### Phase 6：旧体系删除与最终收口

目标：删除旧 UI 体系，让新设计系统成为唯一入口。

范围：

- 删除不再使用的页面专属类。
- 删除重复 token。
- 删除旧 terminal/glass/neon 工具类。
- 删除或重命名不符合新语义的组件 variant。
- 清理不再使用的导入、测试 mock 和快照断言。
- 统一文档、截图和后续开发约束。

验收标准：

- `index.css` 不再承载大量旧页面样式。
- 新 `src/styles/` 成为样式真源。
- 没有明显未使用的旧风格类。
- 构建产物正常。
- lint、build、关键测试通过。

最终清理动作：

- 搜索并清零旧视觉关键词：`terminal`、`neon`、`glow`、`glass`、`cyber`。
- 搜索并清理页面私有 token：`--home-`、`--chat-`、`--portfolio-`、`--settings-`。
- 搜索并替换旧按钮类：`btn-primary`、`btn-secondary` 和页面私有按钮类。
- 检查 `ParticleBackground`、dashboard 旧组件和旧 wrapper 是否仍被引用。
- 检查 `index.css` 是否只保留入口职责。
- 检查截图、文档和测试是否仍描述旧 UI。

## 11. 文件影响清单

预期核心影响文件和目录：

| 文件 / 目录 | 变更类型 |
| --- | --- |
| `apps/dsa-web/src/index.css` | 改为样式入口，移除大量旧样式承载职责 |
| `apps/dsa-web/src/styles/` | 新增样式真源目录 |
| `apps/dsa-web/src/components/layout/` | 重写 Shell、Sidebar、页面布局骨架 |
| `apps/dsa-web/src/components/common/` | 重写通用组件体系 |
| `apps/dsa-web/src/components/history/` | 配合首页重写历史列表 |
| `apps/dsa-web/src/components/tasks/` | 配合首页重写任务面板 |
| `apps/dsa-web/src/components/report/` | 重写报告摘要和 Markdown 阅读体验 |
| `apps/dsa-web/src/components/dashboard/` | 清理或替换旧 dashboard 组件语义 |
| `apps/dsa-web/src/pages/HomePage.tsx` | 完整重写首页 UI 结构 |
| `apps/dsa-web/src/pages/ChatPage.tsx` | 完整重写问股页 UI 结构 |
| `apps/dsa-web/src/pages/PortfolioPage.tsx` | 完整重写持仓页 UI 结构 |
| `apps/dsa-web/src/pages/BacktestPage.tsx` | 重写为数据工作台风格 |
| `apps/dsa-web/src/pages/*` | 其他页面按新排版、新布局和新组件体系完整重写 |
| `apps/dsa-web/src/pages/__tests__/` | 按新页面结构更新测试断言 |
| `apps/dsa-web/e2e/` | 如有相关覆盖，按新用户路径更新 |

### 11.1 组件迁移矩阵

当前组件目录已经具备较多基础组件。重构不应无差别删除所有组件文件，而应逐一判断“语义是否继续成立、视觉是否必须重写、API 是否需要收敛”。

| 目录 / 组件 | 迁移方式 | 说明 |
| --- | --- | --- |
| `components/layout/Shell.tsx` | 重写 | 全局框架、滚动边界、移动端 drawer、主内容类型都要重建 |
| `components/layout/ShellHeader.tsx` | 重写或并入 Shell | 若仅服务旧移动端 header，可合并到新 Shell |
| `components/layout/SidebarNav.tsx` | 重写 | 导航分组、激活态、底部账户/配额/帮助区域整体替换 |
| `components/layout/QuotaIndicator.tsx` | 重写视觉，保留语义 | 配额展示进入低噪声产品状态，不使用强警示装饰 |
| `components/common/Button.tsx` | 重写 API 与视觉 | 成为所有页面按钮唯一入口 |
| `components/common/Card.tsx`、`SectionCard.tsx` | 重写视觉语义 | 删除 terminal/glass/gradient 类视觉变体 |
| `components/common/Input.tsx`、`Select.tsx`、`Checkbox.tsx` | 重写或补全 | 表单状态、label、description、error、focus 统一 |
| `components/common/EmptyState.tsx` | 重写 | 承接无数据、首次引导、无权限、失败恢复 |
| `components/common/InlineAlert.tsx`、`ApiErrorAlert.tsx` | 重写视觉，保留错误语义 | 支持 details、actions 和区域化错误 |
| `components/common/Drawer.tsx`、`ConfirmDialog.tsx`、`Tooltip.tsx` | 强化可访问性 | focus、Escape、aria 和移动端尺寸 |
| `components/common/Loading.tsx`、`ScrollArea.tsx` | 复用或轻改 | 重点检查是否依赖旧 token |
| `components/common/ParticleBackground.tsx` | 默认删除 | 旧装饰背景不作为新视觉基础设施 |
| `components/dashboard/*` | 替换或重命名 | 若仍有通用价值，应迁移为中性数据展示组件 |
| `components/history/*` | 配合首页重写 | 从主内容降级为辅助 rail / drawer |
| `components/tasks/*` | 配合首页重写 | 任务状态应标准化展示阶段、错误和恢复动作 |
| `components/report/*` | 重写阅读体验 | 报告摘要、详情、新闻、策略和 Markdown 使用新阅读体系 |
| `components/StockAutocomplete/*` | 重写视觉，保留搜索行为 | 与新输入体系、空态、键盘选择和错误提示对齐 |
| `components/billing/PaymentDialog.tsx` | 重写视觉，保留支付语义 | 支付二维码、状态轮询、失败/过期提示要清晰 |

### 11.2 测试影响清单

已有测试应从“验证旧 DOM 长相”迁移为“验证用户路径和业务语义”。重点文件：

| 测试文件 | 重构关注点 |
| --- | --- |
| `components/common/__tests__/Button.test.tsx` | 按新 variant、loading、disabled 和可访问属性更新 |
| `components/common/__tests__/Input.test.tsx` | 覆盖 label、description、error、disabled、focus |
| `components/StockAutocomplete/__tests__/StockAutocomplete.test.tsx` | 保持键盘选择、搜索结果、空态和错误态 |
| `components/history/__tests__/HistoryList.test.tsx` | 按辅助 rail / drawer 的新结构更新 |
| `components/dashboard/__tests__/DashboardStateBlock.test.tsx` | 若组件迁移为新状态组件，同步改名或更新断言 |
| `components/layout/__tests__/Shell.test.tsx` | 覆盖桌面/移动端导航、滚动边界和公开路由 |
| `components/layout/__tests__/SidebarNav.test.tsx` | 覆盖导航分组、激活态、权限入口和配额区域 |
| `pages/__tests__/HomePage.test.tsx` | 验证分析入口、历史、任务、报告和错误状态 |
| `pages/__tests__/ChatPage.test.tsx` | 验证会话、技能、发送、流式、滚动 |
| `pages/__tests__/PortfolioPage.test.tsx` | 验证账户、持仓表、导入、缺价/写保护等状态 |
| `pages/__tests__/BacktestPage.test.tsx` | 验证参数、执行状态、指标和结果表 |
| `pages/__tests__/SettingsPage.test.tsx` | 验证配置分组、保存、连接测试和错误展示 |
| `pages/__tests__/LoginPage.test.tsx` | 验证登录、redirect、错误和用户模式兼容 |
| `e2e/` | 如覆盖核心路径，按新导航和新主任务入口更新 |

## 12. 测试与验证计划

前端改动默认验证：

```bash
cd apps/dsa-web
npm run lint
npm run build
```

涉及核心页面时追加：

```bash
cd apps/dsa-web
npm run test
```

如修改页面结构导致测试断言失效，应同步更新对应测试。重点关注：

- 首页分析入口、历史选择、报告打开。
- 问股页发送消息、会话切换、技能选择、滚动行为。
- 持仓页账户切换、创建账户、CSV 导入、表格展示。
- 认证状态下路由跳转。
- 移动端导航 drawer。
- 深色模式可读性。

可选补充验证：

```bash
cd apps/dsa-web
npm run test:smoke
```

### 12.1 阶段验证矩阵

| 阶段 | 必跑 | 建议追加 | 手工检查重点 |
| --- | --- | --- | --- |
| Phase 0 | 不强制代码验证 | 文档链接和文件名核对 | 页面清单、删除清单、测试清单是否完整 |
| Phase 1 | `npm run lint`、`npm run build` | 通用组件测试 | Shell、导航、主题、移动端 drawer、基础组件状态 |
| Phase 2 | `npm run lint`、`npm run build`、`npm run test` | 首页相关测试定向执行 | 输入股票、任务、历史、报告、空态/错误态 |
| Phase 3 | `npm run lint`、`npm run build`、`npm run test` | Chat 页面测试定向执行 | 会话、技能、流式、滚动、移动端 composer |
| Phase 4 | `npm run lint`、`npm run build`、`npm run test` | 持仓/回测测试定向执行 | 表格、图表、CSV、写保护、缺价、执行状态 |
| Phase 5 | `npm run lint`、`npm run build`、`npm run test` | 认证/账号/商业化测试 | 表单、redirect、支付、订单、发票、管理员权限 |
| Phase 6 | `npm run lint`、`npm run build`、`npm run test` | `npm run test:smoke` | 旧样式清零、截图和文档不再描述旧 UI |

### 12.2 人工验收设备与视口

至少覆盖以下视口：

- 桌面宽屏：`1440px` 以上，检查工作台分栏和宽屏信息密度。
- 笔记本：`1280px` 左右，检查导航和主内容是否挤压。
- 平板：`768px-1024px`，检查辅助 rail、表格和 drawer。
- 手机：`375px-430px`，检查主任务入口、底部输入、弹层和横向滚动。

至少覆盖以下模式：

- 未登录访问受保护页面。
- 未登录访问公告和 legal 页面。
- 用户模式开启/关闭。
- 管理员入口可见/不可见。
- 深色模式。
- 网络错误或 API 返回错误。
- 配额不足或订阅过期。

## 13. 风险与控制措施

| 风险 | 说明 | 控制措施 |
| --- | --- | --- |
| 样式影响范围过大 | `index.css` 被多页面共享 | 先建立新样式真源，再按页面重写并删除旧类 |
| 页面测试失效 | DOM 结构和 className 变化 | 保留关键 `data-testid`，同步更新测试 |
| 移动端回归 | Shell、首页、问股页都是复杂布局 | 每个阶段检查窄屏布局和 drawer |
| 深色模式不可读 | token 改动会影响所有页面 | 同步维护 `.dark` 变量，避免硬编码颜色 |
| 业务行为误改 | 页面内包含大量状态逻辑 | 先改布局和组件，不重写 hooks/store |
| 重构长期残留两套体系 | 旧样式和新样式并存时间过长 | 每个页面重写完成后删除对应旧样式和旧 UI wrapper |
| 视觉不统一继续发生 | 后续页面继续局部堆 className | 强化通用组件和页面模板，禁止新增平行样式体系 |
| 可访问性倒退 | 新弹层、导航、表单如果只重视觉可能丢失键盘路径 | 每个基础组件测试 focus、aria 和 Escape |
| 图表和 Markdown 性能下降 | 新阅读体验和图表可能增加渲染成本 | 长报告、流式输出和图表容器做局部渲染控制 |
| 商业化路径误伤 | 账号、订单、发票、支付页面涉及用户权益 | 保持 API 契约和状态语义，支付/订单路径单独手工验收 |

## 13.1 PR 提交检查清单

每个重构 PR 建议在描述中附以下信息：

- 改动页面和组件范围。
- 本 PR 删除了哪些旧样式、旧 token、旧 wrapper 或旧 variant。
- 哪些旧样式因依赖未迁移页面而暂时保留，以及计划在哪个阶段删除。
- 保持不变的业务契约和 API。
- 已执行的命令：`npm run lint`、`npm run build`、`npm run test` 或说明未执行原因。
- 手工验收路径和视口。
- 已知风险和回滚方式。

## 14. 非目标

本次重构不包含：

- 后端 API 调整。
- 数据库 schema 调整。
- 认证、配额、订单、发票、支付、通知等业务逻辑改动。
- 默认不新增大型 UI 组件库依赖；如后续确需引入，必须单独评估体积、主题可控性和迁移成本。
- 大规模重写业务状态管理。
- 大规模重写 API client。
- 改变部署方式。
- 改变桌面端打包链路。

## 15. 推荐执行顺序

推荐按以下顺序推进：

1. 先完成 Phase 0，明确页面清单、测试基线和删除目标。
2. 再完成 Phase 1，建立新设计系统、样式目录和全局 Shell。
3. 然后完成 Phase 2，让首页率先成为新体验样板。
4. 接着完成 Phase 3 和 Phase 4，覆盖问股、持仓、回测三个复杂页面。
5. 再完成 Phase 5，重写剩余页面。
6. 最后完成 Phase 6，删除旧 UI 体系和遗留样式。

如果需要减少单次 PR 风险，可以拆成以下改动批次：

- PR 1：重构基线、样式目录、新设计系统、Shell、Sidebar、通用组件。
- PR 2：首页完整重构。
- PR 3：问股页完整重构。
- PR 4：持仓和回测完整重构。
- PR 5：其他页面完整重写。
- PR 6：旧样式、旧组件语义和兼容层删除。

## 16. 成功标准

完成后应满足：

- 首屏观感与旧版明显不同，达到完整产品级新界面，而不是旧页面换肤。
- 首页主任务路径明确，用户无需理解复杂面板即可开始分析。
- 问股页更像 AI 投研助手，长回答更适合阅读。
- 持仓页能快速传达组合状态和风险重点。
- 通用组件和布局组件能覆盖大多数页面场景。
- 旧 terminal/glass/neon/dashboard 混杂风格基本消失。
- `src/styles/` 成为样式真源，`index.css` 不再是巨型样式堆叠文件。
- 新增页面时优先组合现有布局和组件，而不是继续新增页面专属样式。
- 前端 lint、build、关键测试通过。
