# AI 研究者助手 · 前端详细设计

> **文档性质**：技术详细设计文档，承接[架构设计概要](./AI研究者助手-架构设计概要.md)中「前端详细设计」的详细化位置。
> 上游文档：架构设计概要 · [交互设计规范(DS)](./AI研究者助手-交互设计规范.md) · [PRD(SRS)](./AI研究者助手-软件需求规格说明书.md) · [后端详细设计](./AI研究者助手-后端详细设计.md) · [SDP](./AI研究者助手-软件开发计划.md) · 现有 HTML 原型(`prototype/`)。
> 版本：v0.91（评审修订版）· 起草日期：2026-09-09
>
> **范围**：覆盖 M1–M6 全里程碑的前端工程与页面设计；功能点以里程碑标注（M1/M2/M3/…），未到期的能力在章节内以「预留」呈现。
>
> **变更原则**：本文件调整组件 API、路由、状态/事件模型时，需评估对[后端详细设计](./AI研究者助手-后端详细设计.md)契约与[交互设计规范](./AI研究者助手-交互设计规范.md)的影响；纯样式调整无需升版。

---

## 目录

1. [概述](#1-概述)
2. [前端总体架构与技术基线](#2-前端总体架构与技术基线)
3. [工程化与脚手架](#3-工程化与脚手架)
4. [应用壳与路由](#4-应用壳与路由)
5. [设计系统与主题机制](#5-设计系统与主题机制)
6. [基础组件库](#6-基础组件库)
7. [状态管理与数据流](#7-状态管理与数据流)
8. [服务与契约层](#8-服务与契约层)
9. [实时通信详细设计](#9-实时通信详细设计)
10. [业务组件与溯源体系](#10-业务组件与溯源体系)
11. [页面详细设计](#11-页面详细设计)
12. [第二梯队页面规格](#12-第二梯队页面规格)
13. [用户介入(HITL)统一交互设计](#13-用户介入hitl统一交互设计)
14. [鉴权、权限与多租户前端模型](#14-鉴权权限与多租户前端模型)
15. [可访问性与体验基线](#15-可访问性与体验基线)
16. [埋点与前端可观测](#16-埋点与前端可观测)
17. [里程碑落地地图](#17-里程碑落地地图)
18. [附录](#18-附录)

---

## 1. 概述

### 1.1 目标

为「AI 研究者助手」的 Web 前端提供可落地实现的详细设计，确保：

1. 支撑 6 阶段研究流水线的端到端可视化（澄清 → 分解 → 检索 → 标准化 → 批判收敛 → 报告）
2. 指挥舱实时看板 ≤ 3s 用户视角刷新，报告页首帧 ≤ 2s
3. 数据点级溯源贯穿报告阅读体验，角标回溯、分歧显式呈现、局限区块为强约束，不允许纯文本报告绕过
4. 用户介入（澄清/追问/剔除/打回/裁决/取消）可操作、可审计、不可绕过编排层
5. 全流程设计与 DS 的 token 体系、工业中性视觉、WCAG 2.1 AA 基线一致

### 1.2 上游文档冲突的处理基线

本文档以 **DS（交互设计规范）+ 架构概要 §4.2 + 现有 6 页原型** 作为页面模块的权威来源。PRD（SRS）版本较早，未定义「首页 / 知识库 / 分歧工作台」等页面视图，凡涉及与 PRD 文字不一致之处，本文件按以下规则处理：

- 页面与模块清单：以 DS、架构概要 §4.2、原型导航（01 首页 / 02 指挥舱 / 03 报告 / 04 发起研究 / 05 分歧工作台 v1.1 / 06 知识库 v1.1）为准。
- PRD 明确存在、但 DS/原型未展开的视图（助手面板、模板库、成员/角色、命令面板等）：以 PRD 模块 G / 功能 #1、#9、#10 文字为准，本文件补充「页面规格级」设计。
- 冲突点统一在本文件对应章节添加「与 PRD 差异注释」，不回改 PRD。

### 1.3 读者

- 前端工程师：组件库、页面模块、状态/事件模型、实时层、契约层的实现依据
- 后端工程师：前端对 API/WS/SSE 的消费方式、本文档起草的前端视角契约草案（见 §18.1）
- 产品 / 设计师：里程碑能力窗口与视觉约束的落地口径
- 测试 / QA：验收项 A1-A14 的前端埋点与交互验证口径（见 §16）

### 1.4 里程碑对齐说明

本文档按 SDP 的 M0–M6 划功能落地窗口。前端侧的里程碑主线如下（详见 §17 落地地图）：

| 里程碑 | 前端主线 |
|---|---|
| M0 | 脚手架、工程化、设计 token 迁移、基础组件库骨架 |
| M1 | 单页可跑链路：发起向导最简版 + 指挥舱最简版 + 报告最简呈现（markdown 只读） |
| M2 | 意图路由 + 独立助手面板 + 指挥舱实时看板 + 数据点级溯源（报告结构化渲染 v1） |
| M3 | MVP 功能全集：完整向导、报告四区块与筛选、成本展示、深色模式、命令面板骨架 |
| M4 | 体验打磨：实时成本强化（≤3s）、报告基础协作（批注/@/标存疑）、分歧工作台（v1.1）、证据时间线/可信分级可视化、导出/分享入口、项目内成员与设置 |
| M5 | 知识库页、数据血缘呈现（私域 vs 公域标注）、数据源级权限可见性 |
| M6 | 运营化：账户/团队管理完善、SLA 指标页、埋点看板 |

---

## 2. 前端总体架构与技术基线

### 2.1 技术选型落地（已确认）

| 项 | 决策 | 备注 |
|---|---|---|
| 框架 | Vue 3（Composition API + `<script setup>`） | 全站启用组合式写法 |
| 构建 | Vite 5+ | dev 代理 + 产物分包 |
| 路由 | Vue Router 4 | 懒加载 + 导航守卫 |
| 状态 | Pinia（仅持久化实体与全局态） | 实时数据不进全局 store（见 §7） |
| 语言 | TypeScript（strict） | 业务类型由 OpenAPI 生成 + 手写事件类型合并 |
| HTTP | 生成客户端 `typescript-fetch` + 自封装适配 | 见 §8.2 |
| 实时 | 自研 RealtimeClient（WS + SSE） | 见 §9 |
| 样式 | 纯 CSS 变量 + SFC scoped | 见 §5 |
| 深色 | `html[data-theme]`，浅色/深色/系统跟随 | 首版即支持 |
| 组件 | 自研轻量组件库 + Floating UI（浮层定位） | 见 §6 |
| 文案 | 轻量文案模块，不引 vue-i18n | 见 §8.6 |
| 埋点 | 自研 telemetry 模块 | 见 §16 |
| 图表 | 自研 SVG/CSS 小组件；复杂场景按需 ECharts | M4+，见 §10.1（M4 深化） |

### 2.2 前后端契约基线（引用后端详细设计）

前端消费契约以[后端详细设计](./AI研究者助手-后端详细设计.md)为权威，本文件只补充前端侧消费约束与缺失契约草案：

- 基路径 `/api/v1`；REST 返回 `application/json`；错误统一 RFC 7807 `application/problem+json`（`type/title/status/detail/instance/code/trace_id`），前端按 `code` 分支处理。
- 字段命名 snake_case；实体 ID 带前缀（`run_`/`ev_`/`conf_`/`rep_` 等）且为服务端 ULID。
- 时间戳：REST 字段为 ISO 8601 UTC（`2026-09-09T12:00:00Z`）；事件 envelope 的 `ts` 为 epoch 毫秒整数；SSE 的 `id` 为事件 ID。
- 分页统一 `{ items, total, page, page_size, has_more }`，查询参数 `page` / `page_size` / `cursor`。
- 鉴权：`Authorization: Bearer <access_token>`（JWT 30 分钟）+ `refresh_token`（7 天）；`logout` 撤销 refresh。
- 幂等：POST 写接口支持 `Idempotency-Key`（UUIDv4），前端重试需复用同一 key。

### 2.3 分层架构

```
┌────────────────────────────────────────────────────────────────┐
│ 视图层 views/          页面容器（路由级），组装业务组件与交互    │
├────────────────────────────────────────────────────────────────┤
│ 业务组件层 components/  领域组件（StageTimeline、EvidenceCard…）  │
├────────────────────────────────────────────────────────────────┤
│ 组合层 composables/    领域逻辑（useRunStream、useWizard…）      │
├────────────────────────────────────────────────────────────────┤
│ 状态层 stores/         全局态（会话/团队/项目/路由级实体）        │
├────────────────────────────────────────────────────────────────┤
│ 服务层 services/       HTTP/WS/SSE 客户端、事件解码、mock 网关   │
├────────────────────────────────────────────────────────────────┤
│ 基础层 ui/ + tokens/   基础组件库、design tokens、工具函数        │
└────────────────────────────────────────────────────────────────┘
```

依赖方向自上而下单向；业务组件不得直接 import `services/` 的传输实现，统一经 `composables/` 暴露（便于 mock 网关替换，见 §8.5）。

### 2.4 目录结构（工程骨架）

```
frontend/
├─ public/
├─ src/
│  ├─ main.ts
│  ├─ App.vue
│  ├─ router/
│  │  ├─ index.ts          # 路由表 + 守卫装配
│  │  └─ guards.ts         # auth / 租户 / 项目边界守卫
│  ├─ layouts/
│  │  └─ AppShell.vue      # 顶栏+左侧栏+内容区+右侧详情栏 布局壳
│  ├─ views/               # 路由级页面（一级目录对应页面）
│  │  ├─ home/             # 首页 + 单入口 + 意图路由
│  │  ├─ wizard/           # 发起研究 5 步向导
│  │  ├─ cockpit/          # 指挥舱
│  │  ├─ report/           # 报告阅读（生成预览 + 终稿）
│  │  ├─ disputes/         # 分歧工作台（v1.1 / M4）
│  │  ├─ knowledge/        # 知识库（v1.1 / M5）
│  │  ├─ project/          # 项目列表 + 项目内 任务/成员/设置
│  │  ├─ assistant/        # 全局助手面板（闲聊）
│  │  ├─ templates/        # 模板库（M3+）
│  │  ├─ account/          # 账户/订阅/团队管理
│  │  └─ auth/             # 登录/回调
│  ├─ components/
│  │  ├─ ui/               # 基础组件（§6）
│  │  ├─ business/         # 业务组件（§10）
│  │  └─ common/           # 跨页通用（AvatarStack、EmptyState…）
│  ├─ composables/
│  │  ├─ runs/             # useRunStream / useRunList / useCost 等
│  │  ├─ report/           # useReportDraft / useReportBlocks
│  │  ├─ wizard/           # useWizardStep
│  │  └─ misc/             # useDarkMode / useBreakpoint / useSticky…
│  ├─ stores/
│  │  ├─ session.ts        # 会话/登录态/用户
│  │  ├─ team.ts           # 当前团队/成员缓存
│  │  ├─ project.ts        # 当前项目/项目列表（持久化实体）
│  │  └─ ui.ts             # 全局 UI 态（命令面板开关、主题偏好）
│  ├─ services/
│  │  ├─ http/             # HttpClient 适配 + 错误映射
│  │  ├─ api/              # openapi 生成客户端（build 时生成）
│  │  ├─ realtime/         # RealtimeClient(WS/SSE) + 事件路由
│  │  ├─ telemetry/        # 埋点模块
│  │  ├─ i18n/             # 文案模块（§8.6）
│  │  └─ mock/             # mock 网关 + 研究模拟器 fixtures（dev only）
│  ├─ styles/
│  │  ├─ tokens.css        # 唯一 token 源（自 prototype 迁移）
│  │  ├─ base.css          # reset + 排版 + 滚动条/焦点环
│  │  ├─ theme.css         # [data-theme] 深色覆盖的机制骨架
│  │  └─ utilities.css     # 少量布局工具类
│  ├─ types/
│  │  ├─ openapi.d.ts      # 生成类型再导出
│  │  ├─ realtime.ts       # WS/SSE 事件类型与 envelope
│  │  └─ domain.ts         # 前端领域类型补丁（枚举联合等）
│  └─ utils/               # formatDate / id / throttle / rafBatch…
├─ vite.config.ts
├─ tsconfig.json
├─ package.json
└─ .env.development / .env.production
```

### 2.5 版本化功能标注约定

文档中每个能力条目标注里程碑窗口，采用统一标签：

- `[M1]` … `[M6]`：该能力首次完整可用窗口
- `[预留 Mx+]`：仅演进预留，设计占位不展开
- `[v1.1]`：对应原型中标注的 v1.1 能力（与里程碑映射见 §17）

---

## 3. 工程化与脚手架

### 3.1 依赖策略

原则：**组件自研、少而稳的运行时依赖**；工具链依赖（生成、lint）充分。

| 依赖 | 用途 | 说明 |
|---|---|---|
| `vue` / `vue-router` / `pinia` | 框架 | — |
| `@floating-ui/dom` | 浮层定位 | Popover/Tooltip/Drawer 定位底座 |
| `typescript-fetch` 生成产物 | API 客户端 | 后端 OpenAPI 经 openapi-generator 生成，仅类型消费（不引入 `openapi-fetch` 手写方案） |
| `marked`（按需）| 生成预览的 markdown 渲染 | M1 报告最简呈现 |
| `echarts`（按需，M4+）| 复杂分析图 | 动态 import，不进主包 |
| `dayjs`（或原生 Intl）| 日期格式化 | ISO8601 → 本地展示 |

禁止引入：UI 组件库全家桶、vue-i18n、重型图表 SDK 进主包、未经评估的编辑器内核（见 §10.6）。

### 3.2 构建与产物分包

- 路由级懒加载 + 动态 `import()`。
- 手动分包：`echarts`、`marked` 等大依赖独立 chunk；产物按 route 生成。
- 预算门槛：主包 gzip 目标 ≤ 260KB（不含按需 chunk）；报告页首帧 ≤ 2s（见 §16.3）。
- 环境变量：`.env.development` 提供 `VITE_API_BASE`、`VITE_MOCK=gateway|off`；生产仅 `VITE_API_BASE`。
- dev server：`/api`、`/ws`、`/sse` 代理到本地 mock 网关（§8.5）。

### 3.3 类型生成与契约一致性

- 后端 OpenAPI 由 CI 产出 `openapi.json`；前端通过 `openapi-generator-cli` 生成 `typescript-fetch` 客户端到 `src/services/api/`（产物入仓，便于审 diff）。
- 生成产物之上封 `services/http` 适配层（统一错误映射、鉴权注入、幂等键、重试），业务代码不直接触碰生成客户端。
- WS/SSE 事件类型无 OpenAPI 描述，由 `src/types/realtime.ts` 手写（envelope + 事件表），与后端详细设计 §8.2 对照维护。
- 前后端契约变更走 PR/评审；生成 diff 太大时核对是否误改 schema。

### 3.4 代码规范与质量门禁

- ESLint（vue3 插件 + TS strict）+ Prettier（中文注释不参与格式化回退，仅走字符串保护）。
- 提交前 lint + typecheck；MR 门禁含构建。
- 无专项单测要求（按项目规则，M4 起若引入可视化复杂组件再评估），质量以 lint/类型/构建 + 人工验收 + 埋点回归为准。

---

## 4. 应用壳与路由

### 4.1 AppShell 布局壳

布局继承 DS §3「顶栏 + 双侧栏」，单例布局壳包裹所有业务路由：

```
┌──────────────────────────────────────────────────────────────┐
│ 顶栏 [Logo][项目][模板][知识库][账户][Cmd+K][+ 新建研究]      │
├──────────┬──────────────────────────────┬────────────────────┤
│ 项目内    │ 主内容区 <router-view>        │ 详情侧栏(按需展开)  │
│ 导航      │                              │ RightDrawer 挂载点  │
├──────────┴──────────────────────────────┴────────────────────┤
│ 全局浮层挂载点：Modal / Toast / CommandPalette（助手面板为独立路由，见 §12.6）  │
└──────────────────────────────────────────────────────────────┘
```

实现要点：

- `AppShell.vue` 只负责结构：顶栏（`TopBar`）、左侧栏（全局导航 or 项目内导航，取决于路由元 `meta.nav=global|project`）、`<router-view v-slot>` 内容区、右侧 `RightDrawer` 抽象（报告溯源/介入操作共用的抽屉容器，见 §13.1）。
- 浮层型能力（命令面板、Toast、Modal）通过组合式挂载点置于壳层，页面内不重复实现；全局助手面板为独立路由页面（§12.6），不在壳层重复实现抽屉形态。
- 顶栏入口可见性按 §14 权限与里程碑（如[模板]M3+、[知识库]M5/v1.1）。

### 4.2 路由表

| 路径 | 视图 | meta | 版本 |
|---|---|---|---|
| `/auth/login` | Login | 公开 | M1 |
| `/auth/sso/callback` | SSOCallback | 公开 | M3+ 预留 |
| `/home` | HomeView | 需要鉴权 | M2（入口主体） |
| `/assistant` | AssistantView（闲聊） | 需要鉴权 | M2 |
| `/projects` | ProjectListView | 需要鉴权 | M1（骨架）/ M3 |
| `/projects/:projectId/tasks` | ProjectTasksView | 项目边界 | M1 |
| `/projects/:projectId/runs/:runId/cockpit` | CockpitView | 项目边界 | M1（最简）/M2/M3/M4 |
| `/projects/:projectId/runs/:runId/report` | ReportView | 项目边界 | M2（结构化 v1） |
| `/runs/:runId/report` | ReportView（报告独立阅读别名） | 分享只读（M4+） | M4 预留 |
| `/wizard` | WizardView | 需要鉴权，可选 query `template_id` | M1 |
| `/templates` | TemplateLibraryView | 需要鉴权 | M3 |
| `/projects/:projectId/runs/:runId/disputes` | DisputesView | 项目边界 | v1.1 / M4 |
| `/knowledge` | KnowledgeView | 需要鉴权 | v1.1 / M5 |
| `/account`、`/account/team`、`/account/settings` | AccountView | 需要鉴权 | M1 骨架/M4/M6 |
| `/command` | 无（命令面板为浮层，不走路由） | — | M3 |

说明：

- 报告独立阅读别名 `/runs/:runId/report` 为 M4 只读分享链接预留，不进入项目导航。
- 指挥舱与报告的主路径挂在项目下，符合"研究归属项目、非成员不可见"模型。
- 分歧工作台无全局独立入口，统一由报告内 ConflictBlock「查看分歧详情」与指挥舱冲突列表跳转到项目上下文路由。

### 4.3 导航守卫

- `requiresAuth`：未登录跳 `/auth/login` 并携带 `redirect`。
- 会话刷新静默续期失败且无有效 refresh → 登出跳登录。
- 项目边界守卫：路由带 `:projectId` 时，从 project store 校验可见性；后端对越权统一返回 404（`RUN_NOT_FOUND` 语义），前端**不猜测 403**，一律以资源详情接口结果为准（404 展示"资源不存在或无权访问"）。
- 默认落地：`/` 重定向目标——M1 为 `/projects`，M2 首页启用后切到 `/home`（版本开关）；已带 `redirect` 的登录回跳优先。

### 4.4 离线与重连（壳级）

- `navigator.onLine` + 业务心跳结合；离线 → 全屏插画 + 重连按钮（DS §5.6 无网络态）。
- 恢复在线：自动重连 WS；拉取一次 `GET /runs/{id}/cost/snapshot` 补齐（对应后端详细设计 §8.3 断连补齐）。
- 报告页离线时不阻塞本地滚动阅读，仅禁用实时能力与操作按钮。

---

## 5. 设计系统与主题机制

### 5.1 Token 单一事实源

`src/styles/tokens.css` 由 `prototype/styles/tokens.css` 迁移而来，是颜色/字号/间距/圆角/阴影/动效的**唯一事实源**。所有组件样式只允许引用 CSS 变量，禁止硬编码色值/字号。

用 CSS `@layer` 治理层叠顺序：

```css
@layer tokens, base, components, utilities;

/* tokens.css 中，浅色默认 + 深色覆盖 */
:root,
[data-theme="light"] { /* 全部 --brand-* / --neutral-* / --success-* / 语义 token ... */ }
[data-theme="dark"]  { /* 深色模式覆盖同名变量 */ }
```

- 深浅双套值沿用 DS §2.1–§2.5 的色板（含信源语义色 `--source-primary/secondary/private/disputed`）。
- 阴影 token 仅用于浮层；卡片层级一律由 1px 边框 + 密度承担（DS 关键约束）。

### 5.2 主题切换与首屏防闪

`useDarkMode` 组合式封装：偏好来源优先级 `手动选择 > 系统(prefers-color-scheme) > 默认浅色`；持久化到 `localStorage:theme`。

- 切换实现：设置 `document.documentElement.dataset.theme = 'light' | 'dark'`，同时持久化 `themeMode: 'light'|'dark'|'system'`。
- **防闪**：在 `index.html` 内联一段脚本，在应用挂载前按 `themeMode` 或系统偏好先行写入 `data-theme`（参考系统主题的标准 pattern）。
- 监听系统变化仅在 `themeMode === 'system'` 时生效，避免覆盖用户显式选择。
- 版本口径：深浅主题机制随 M0（tokens 迁移）即可用；全页面双主题视觉/对比度 QA 收口在 M3（见 §17 M3 行），两者不混为一次交付。

### 5.3 动效与无障碍基线

- 动效时长/缓动引用 `--motion-*` 变量。
- **必须**尊重 `prefers-reduced-motion: reduce`：命中时过渡/动画时长归 0（DS §2.6）。实现为一个全局 CSS 覆盖 + 动画类禁用。
- 光标闪烁类动效（报告流式光标）频率上限 1 次/秒。
- 全局焦点环：`:focus-visible` 使用 `--brand-500` 外发光 2px；所有键盘可达组件显式定义。

### 5.4 Z-Index 与浮层层级约定

| 层 | 值 | 元素 |
|---|---|---|
| toast | 1200 | Toast |
| modal/dialog | 1100 | Dialog 遮罩+面板 |
| drawer | 1000 | 右侧抽屉 |
| popover/dropdown | 900 | 悬浮卡片、下拉 |
| tooltip | 950 | 工具提示 |
| sticky 头 | 100 | 顶栏、sticky 工具栏 |
| 内容层 | auto | 常规内容 |

浮层组件统一 `teleport` 到 `body`，降低 `overflow/transform` 上下文干扰。

---

## 6. 基础组件库

### 6.1 组件清单与交付版本

| 组件 | 关键 props/行为 | 版本 |
|---|---|---|
| `UiButton` | `variant=primary/secondary/danger/ghost`、`size=sm/md/lg`、`loading`、`disabled`、`icon` | M0 |
| `UiInput` / `UiTextarea` | `modelValue`、`error`、`label`、`hint`、`autofocus` | M0 |
| （无独立 UiSelect） | 下拉选择由 `UiDropdown` 承担（浮层列表 + 键盘导航；带搜索的模板选择器在其上封装），M0 起即不单列 UiSelect 组件 | M0 |
| `UiTabs` | 下划线样式，受控/非受控 | M0 |
| `UiDialog` | 模态，`size`，遮罩点击/ESC 关闭，焦点困于面板 | M0 |
| `UiDrawer` | 右侧抽屉（`RightDrawer` 抽象复用），`--shadow-drawer` | M0 |
| `UiPopover` / `UiDropdown` | Floating UI 定位、自动翻转、ESC 关闭 | M0 |
| `UiTooltip` | 400ms 延迟，纯文本或富内容插槽 | M0 |
| `UiToast` | 顶部消息，4s 自动消失、手动关闭、`type` | M0 |
| `UiCheckbox` / `UiRadio` / `UiSwitch` | 表单受控组件 | M0 |
| `UiSlider` | 档位/预算选择 | M0 |
| `UiBadge` / `UiTag` | 状态/信源/里程碑标签 | M0 |
| `UiCard` | 边框卡片（不用阴影区分层级） | M0 |
| `UiSkeleton` / `UiEmpty` / `UiErrorState` | 加载≤3s 骨架屏、空状态、错误+重试 | M0 |
| `AvatarStack` | 协作者头像堆叠（M1 规划，实际未实现，顺延至 M3/M4 协作能力窗口，不占 M2 范围） | M3 |
| `Kbd` | 快捷键提示（命令面板） | M3 |

### 6.2 通用组件约定（对全部基础组件生效）

- **命名**：文件 `UiXxx.vue`，`<script setup lang="ts">` 全量 props 显式声明；事件以 `update:modelValue` / 语义化事件发出。
- **样式**：引用 §5 token；类名 `--scoped` 由 SFC 保证；不产生全局样式污染（浮层 teleport 内仍用 scoped + 挂 data 属主 class）。
- **可访问性**：按钮/选项支持 `Enter/Space`；浮层支持 `ESC` 与焦点循环（Dialog 用 `focus-trap` 自研小实现）；`aria-label` 由业务组件按 DS §7.1 提供。
- **禁用态**：视觉（对比度）与行为（不透发事件）双禁用，不与 `loading` 混用。
- **尺寸体系**：以 DS 字号/间距 token 为唯一输入；组件不自行发明尺寸层级。

### 6.3 示例：UiButton 骨架（实现口径）

```ts
// UiButton.vue（骨架示意，实际随 M0 落地）
<script setup lang="ts">
type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(defineProps<{
  variant?: Variant     // 视觉类型
  size?: Size           // 尺寸
  loading?: boolean     // 加载态：禁用点击并显示内置 loading 图标
  disabled?: boolean    // 禁用态
  icon?: string         // 前缀图标名（图标统一 svg 组件管理，禁止 emoji 图标）
  nativeType?: 'button' | 'submit'
}>(), { variant: 'primary', size: 'md', disabled: false })

defineEmits<{ (e: 'click', ev: MouseEvent): void }>()
</script>

<template>
  <button
    class="u-btn"
    :class="[`u-btn--${variant}`, `u-btn--${size}`]"
    :disabled="props.disabled || props.loading"
    :type="nativeType"
  >
    <slot name="icon" />
    <slot />
  </button>
</template>
```

---

## 7. 状态管理与数据流

### 7.1 状态边界原则

| 状态类别 | 载体 | 例子 |
|---|---|---|
| 全局态 / 会话态 | Pinia store | 登录用户、当前团队、主题偏好、命令面板开关 |
| 持久化实体 | Pinia store（谨慎缓存） | 项目列表、任务列表（低频，进项目页级再决定） |
| 实时/运行态 | 组合式 + 局部状态（不进 Pinia） | run 状态机、证据池增量、token 实时值、报告流文本 |
| 纯 UI 态 | 组件局部 | 抽屉开合、筛选选中、Tab 激活 |

核心结论（已确认决策）：**高频实时事件不进全局 store**，避免全局响应式数据被 500ms 级事件打爆。实时数据统一由「事件驱动的 composable」持有（§7.3），跨页恢复靠模块级缓存，而不是把整个 run 域对象挂到 Pinia。

### 7.2 Pinia Store 划分

| store | 职责 | 关键 state | 说明 |
|---|---|---|---|
| `session` | 登录态 | `accessToken/refreshToken/user/roles/permissions` | token 仅存内存 + localStorage（仅 refresh）；动作：login/refresh/logout |
| `team` | 当前租户 | `currentTeam/members(缓存)/role` | 成员列表管理员专用接口读取 |
| `project` | 项目/任务实体 | `projects(分页缓存)/currentProject` | 项目级权限边界守卫的数据源 |
| `ui` | 全局 UI 偏好 | `themeMode/commandPaletteOpen/assistantOpen` | 跨页共享的轻量 UI 态 |

store 一律使用 setup 语法，action 内只做同步状态修改，异步副作用放 composable 或页面调用方。

### 7.3 实时运行态：`useRunStream(runId)`

指挥舱/分歧/报告都围绕同一个 run 领域对象展开，设计为唯一 composable 数据源：

```ts
// composables/runs/useRunStream.ts（骨架）
export function useRunStream(runId: string) {
  // 模块级缓存：离开页面再回来时，先展示最近一帧，再增量追平
  const cache = getRunCache(runId)          // Map<runId, 最近状态帧>
  const state = reactive(cache ?? {
    run: null,          // run 实体 + current_stage/status
    stages: [],         // 6 阶段记录
    subQuestions: [],
    evidence: [],       // 证据池（增量合并，可能分页懒加载）
    conflicts: [],
    cost: { used: 0, budget: 0 },
    stream: 'idle',     // idle | connecting | live | retrying | paused
    lastEventId: '',    // 断线补传起点
  })

  // 内部：连接 RealtimeClient 频道、注册事件处理（§9 事件路由表）
  // 对外暴露：connect/disconnect/refresh/介入操作封装(intervene/pause/resume/cancel)
  return { state, connect, disconnect, actions }
}
```

设计约束：

- 同一 run 在同一时刻只建立一个 WS 频道（引用计数共享），跨页面（指挥舱→报告→分歧）切换不重建连接，只在无订阅者后延迟断开。
- `evidence` 支持服务端 500ms 节流推送；前端对增量做 `rAF` 批量渲染。
- 断线重连后：先 `GET /runs/{id}` 重置实体帧，再按 `lastEventId/ts` 去重续传（§9.3）。
- 页面销毁仅解绑订阅，保留最近状态帧于缓存（LRU 上限若干 run），返回时无缝续看。

### 7.4 报告页数据流（双轨）

报告页分两条独立数据通道（§11.4）：

1. **流式预览**：由 `useReportDraft(runId)` 消费 SSE `report.chunk` 的 `delta/position`，维护只读文本缓冲，渲染层每 N 帧 flush 到视图（防抖 100ms，避免逐 token 重渲染）。
2. **结构化终稿**：由 `useReportBlocks(reportId)` 拉取 `GET /reports/{report_id}` 的 JSON 结构，构建 blocks/claims/citations 索引（claim 级查找表），是角标回溯/筛选/批注锚点的唯一数据源。

两条通道通过 report `status`（`draft/final`）切换；`draft→final` 时用 `report.finished` 事件触发从 draft 模式无损切换到 blocks 模式（保留滚动位置到最近可映射区块）。

### 7.5 缓存与失效

- REST 缓存仅缓存低频查询（项目/模板/成员），带 `version` 字段；写操作成功后对相关实体做本地合并或失效重拉。
- `POST /runs` 成功后，立即在项目任务列表与首页"进行中"插入该 run（乐观占位状态 `pending`）。
- 权限变化（成员变更/团队切换）→ 清空 project 缓存 + 强制重取。

---

## 8. 服务与契约层

### 8.1 模块结构

```
services/http/    # HttpClient 适配（错误/鉴权/幂等/重试）
services/api/     # openapi 生成客户端（构建生成，勿手改）
services/realtime/# RealtimeClient：WS 频道 + SSE 流 + 事件路由
services/telemetry/# track() 埋点
services/i18n/    # 文案模块
services/mock/    # mock 网关客户端 + fixtures（dev only）
```

### 8.2 HttpClient 与鉴权

统一封装要点：

- **注入**：请求前附加 `Authorization: Bearer <access_token>`；SSO 场景走 cookie 方案则按后端演进调整。
- **静默续期（single-flight）**：401 时若存在 refresh_token，串行化地刷新（同窗口并发请求只触发一次 refresh，其余等待），成功后重放原请求一次；refresh 失败 → 登出。
- **幂等键**：对 `POST /runs`（发起研究）、`POST /conflicts/{id}/verdict` 等会触发重试的写请求生成 `Idempotency-Key: <uuid>`，重试（网络错误/超时）复用同一 key。
- **错误归一**：把 RFC 7807 body 归一为 `ApiError { code, title, detail, traceId }`；`code` 用于分支（§8.6 文案映射）。
- **取消**：页面离开/组件卸载时 AbortController 取消未完成的列表请求；写请求不自动取消（避免半提交）。
- **追责透传**：读取响应头/体中的 `trace_id` 写入错误 Toast 供用户反馈粘贴。

### 8.3 WS 客户端

- 连接端点：`ws(s)://<origin>/api/v1/ws/runs/{run_id}/stream`；鉴权经子协议或查询参数（以后端实现为准），token 刷新时需重连。
- 消息模型：服务端推送 = §2.2 envelope（`v/event_id/ts/run_id/stage/type/payload`）；客户端发送 = `{ v:'1.0', type, request_id, payload }`。
- 心跳：服务端每 30s `ping`，客户端回 `pong`（裸 JSON，无 envelope），60s 无 pong 服务端断开。
- ACK：`intervene.ack` / `intervene.error`（携带 `request_id`），调用方据此落 Toast 或失败态。

### 8.4 SSE 客户端

注意：原生 `EventSource` **不能携带 `Authorization` 头**，因此报告流使用「基于 fetch + ReadableStream」的自研轻量 SSE 客户端：

```ts
// services/realtime/sse.ts（骨架）
export async function openReportStream(runId: string, handlers: {
  onChunk(ev: ReportChunkEvent): void    // report.chunk：{delta, position}
  onReplace(ev: ReportReplaceEvent): void // report.replace（M4 标存疑重生成）
  onDone(ev: { report_id: string; status: string }): void
  onError(err: ApiError): void
}) { /* fetch with Authorization, 读 stream，按 '\n\n' 分帧解析 SSE */ }
```

SSE 事件帧解析：支持 `event:`、`id:`、`data:` 字段；非 `done` 事件的 `data` 按 envelope JSON 解析（以后端实现为准，见后端详细设计 §4.4.1 与 §8.4 的内部差异提示，联调时以实际行为收敛）。

### 8.5 Mock 网关与研究模拟器（dev only）

M0/M1 前后端并行期间的联调基线：

- **形态**：Vite dev 插件拉起一个本地 Node 网关进程，同一端口提供 `REST(/api/v1/*)`、`WS(/api/v1/ws/runs/{run_id}/stream)`、`SSE(/api/v1/runs/{run_id}/report/stream)` 三套端点，前端零改造切换。
- **研究模拟器**：`services/mock/fixtures/` 下以「指令脚本」描述一次研究的时序（阶段开始→子问题→证据条→冲突→澄清→报告流→完成，可配延时与故障注入），网关按脚本推进并广播与生产同构的 envelope，从而真实验证前端：SSE 流式渲染、WS 心跳/重连/去重、成本节流、澄清挂起与 resume。
- **开关**：`.env.development` 的 `VITE_MOCK=gateway|off`；类型仍由生成产物保证，切生产只改环境变量。
- **复用**：同一套 fixtures 也可驱动浏览器自动化验收与演示 demo（M1 demo / M3 种子用户演示）。

### 8.6 文案与错误映射（轻量文案模块）

- `services/i18n/zh-CN.ts`：导出各模块命名空间文案对象（`home.*`、`cockpit.*`、`report.*`、`errors.*`…），组件通过 `t()` 包装读取，文案统一集中。
- 后端错误码→前端中文文案映射集中在 `errors.*`，未知码回退 `errors.unknown` 并附 `code + trace_id`。
- 数据结构按模块组织、key 语义化，为将来接 vue-i18n 只替换读取层保留空间；不引入框架依赖。

---

## 9. 实时通信详细设计

### 9.1 RealtimeClient 总体

一个 `RealtimeClient` 单例持有所有 WS 频道与 SSE 流，负责：连接生命周期、鉴权重连、心跳、事件解码与路由、断线补齐、节流。业务层经 `useRunStream`/`useReportDraft` 使用，不直接触碰 socket。

```
RealtimeClient
 ├─ Channel(runId)          # WS 频道：引用计数，handler map
 │    type → handler[]      # §9.2 事件路由表
 ├─ SseStream(runId, kind)  # report 流
 ├─ 连接状态机             # §9.3
 └─ 事件总线（进程内）     # 供 telemetry / 跨页通知消费
```

### 9.2 事件路由表（服务端→前端）

envelope 校验通过后按 `type` 分发；`stage` 用于快速过滤（未订阅该 run 时直接丢弃）。payload 形态以后端详细设计 §6.5.x 实际发布为准（两处已知差异见 §18.2）。

| type | 节流 | 前端处理 | 目的地 |
|---|---|---|---|
| `stage.started` / `stage.finished` / `stage.failed` | 即时 | 更新阶段状态机；失败时顶部错误横幅 + 重试入口（`stage.failed` 且可恢复） | 指挥舱时间线 |
| `sub_question.created` / `.started` / `.finished` | 即时 | 更新子问题列表/进度 | 指挥舱子问题区 |
| `evidence.fetched` | 500ms | 增量合入证据池（rAF 批量渲染）；校验 `excluded_by_user` | 指挥舱证据流 |
| `interrupt.requested` | 即时 | 触发澄清/裁决等待态（payload 以 `questions[]` 为准，见 §11.2/§11.5） | 介入抽屉 |
| `conflict.detected` | 即时 | 合入冲突列表 + 报告角标红点 | 指挥舱 + 报告 |
| `token.usage.update` | 1s | 更新 `cost {used, budget, model_breakdown}` | 指挥舱成本卡 |
| `cost.warning` | 即时 | 右下角警告卡（70% 提示 / 90% danger） | 指挥舱 |
| `report.chunk` | 即时 | 转发给报告预览通道（若本页订阅） | 报告流式预览 |
| `report.finished` | 即时 | 触发 draft→final 切换 + 全局通知 | 报告页 |
| `run.finished` | 即时 | 全局 toast/通知、首页"进行中"更新 | 全局 |

### 9.3 连接生命周期与断线补齐

```
idle → connecting → live ⇄ retrying（指数退避 1s/2s/4s/…上限 30s + 抖动）
live → 心跳丢失 / 网络离线 → retrying
live → 页面可见性 hidden 且无关键订阅 → paused（延迟关闭）
retrying 成功 → 补齐流程
```

**补齐流程（重连后必做）**：

1. `GET /runs/{run_id}` 重置 run 实体帧（status/current_stage/成本快照 `GET /runs/{run_id}/cost/snapshot`）。
2. 注册新的 handler，并在回调内以 `lastEventId`（本地缓存最新 `event_id`）做**幂等去重**：已见 `event_id` 直接丢弃；按 `ts` 兜底乱序。
3. 本地缓存缺失的关键帧（子问题/冲突）重新 `GET`，不做逐事件回放。

去重集合按 run 维护，容量上限（如最近 2000 event_id，FIFO 淘汰），避免无限增长。

### 9.4 节流与渲染合并策略

| 场景 | 策略 |
|---|---|
| 证据流增量 | 服务端已 500ms 节流；前端再经 `rAF` 合并 DOM 更新，不做整表重渲染（列表 key 稳定 + 行级 update） |
| token 计数 | 服务端 1s 节流；前端进度条 CSS transition 平滑过渡 |
| 报告流文本 | 缓冲 100ms 后 flush 追加（渲染整段而非逐 token） |
| 子问题/阶段 | 即时但为低频，正常响应式更新 |

指挥舱证据池支持「懒加载全文/摘要」：事件只带 `snippet`，点开卡片再按需拉详情（避免大 payload 拥塞 WS）。

### 9.5 客户端指令与 ACK 关联

客户端指令（`intervene`/`cancel`）统一：

```ts
// 指令发送 + ACK 关联骨架
const requestId = createUlid()
send({ v: '1.0', type: 'intervene', request_id: requestId, payload: {...} })
// 等待 type === 'intervene.ack' 且 request_id === requestId
//   或 'intervene.error'（如 code=INTERVENE_NOT_ALLOWED）→ 文案映射 + Toast
```

同时保留 REST 等价路径（`POST /runs/{id}/intervene`）作为 WS 不可用时的降级手段；两条路径行为一致。

---

## 10. 业务组件与溯源体系

### 10.1 组件清单总览

| 组件 | 用途 | 主要输入 | 版本 |
|---|---|---|---|
| `StageTimeline` | 指挥舱左侧 6 阶段时间线 | `run`、`stages`、`onSelect` | M1 |
| `EvidenceCard` | 证据流卡片（紧凑行 + hover 展开 + 剔除） | `evidence`、`callbacks` | M1 |
| `SourceBadge` | 信源角标（域名/级别/可信分级/私域标记） | `source` | M2 |
| `CitationMarker` | 报告内联溯源角标 `[n]` | `claim`、`citations` | M2 |
| `SourcePanel` | 溯源抽屉内容（来源清单+元数据+原文片段） | `evidenceIds` | M2 |
| `ConflictBlock` | 冲突/分歧内联警告块 | `conflict`、`onVerdict` | M2/M3 |
| `CostMeter` | 成本/Token 进度卡片 | `cost`、`onAction` | M2（最简）/M3（强化） |
| `TemplateCard` | 模板选择卡片 | `template`、`onPreview` | M1 |
| `ClarificationCard` | 阶段1 澄清问题卡（选项+推荐默认） | `questions[]`、`onSubmit` | M1/M2 |
| `SubQuestionPlan` | 子问题计划卡片列表（勾选/排序/确认） | `subQuestions`、`editable` | M2 |
| `InterventionDrawer` | 通用介入操作抽屉（追问/剔除/打回） | `run`、`interventionType` | M2/M3/M4 |
| `VerdictPanel` | 分歧裁决面板（选边/理由/忽略） | `conflict`、`onSubmit` | M3/v1.1 |
| `AnnotationThread` / `Mention` | 报告批注线程 / @提及（M4） | `block/claim`、`members` | M4 [v1.1] |
| `EvidenceTimeline` / `CredibilityLegend` | 证据时间线 / 可信分级可视化（SVG 自研） | `evidence[]` | M4 |
| `LimitationSummary` | 报告"研究局限与未决问题"汇总区块 | `report` | M2 |
| `ReportChrome` | 报告页工具条（分享/导出/筛选/返回） | `report`、`actions` | M2/M4 |

业务组件一律从 `composables`/props 拿数据，不直接发 WS/REST 指令；需要调用介入/裁决动作时经页面注入的 `actions` 透传（便于审计与埋点统一）。

### 10.2 溯源体系数据模型（前端消费视图）

```
Report (blocks)
 ├─ Block { id, type: conclusion|evidence|dispute|limitation, claim_id?, text, confidence }
 │     └─ Claims: { claim_id, text, citations: Citation[] }
 │           └─ Citation { evidence_id, marker:'[1]', snippet }
 │                 └─ Evidence { id, title, url, domain, source_type, source_level,
 │                               credibility: A|B|C|D, source_private, fetched_at, excluded_by_user }
```

- 前端建立 `claim_id → blocks/段落` 与 `evidence_id → citation 位置` 双向查找表，供角标悬停/抽屉/筛选三态联动。
- 报告页**强约束**：正文区块必须由上述结构化模型渲染，角标与引用不可由富文本硬编码——不允许纯文本绕过（对应 PRD 模块 F 约束、验收 A2）。

### 10.3 CitationMarker 交互状态机

| 输入 | 行为 | 输出 |
|---|---|---|
| hover | 200ms 后 Popover：标题 + 域名 + 1 行原文 | `SourceBadge` 迷你版 |
| click | 右侧 `SourcePanel` 固定展开（RightDrawer），滚动定位该证据 | 抽屉开 |
| 键盘（Tab→Enter/Space） | 同上；方向键在 citation 间切换 | 抽屉 + 焦点管理 |
| 报告筛选激活 | 未命中筛选项的 marker 变灰（不删除，保留上下文） | class 切换 |

点击埋点 `report.citation.open`（§16）。

### 10.4 报告锚点与选区定位（M4 批注底座）

锚点统一指向**结构化单元**（`block_id` 或 `block_id + claim_id` + 可选字符偏移），不与纯文本 offset 绑定，避免后续报告重生成（`report.replace`）后锚点失效：

```ts
// utils/report-anchor.ts（骨架）
export type ReportAnchor =
  | { kind: 'block'; blockId: string }
  | { kind: 'claim'; claimId: string; range?: [startChar, endChar] } // range 相对 claim.text
```

- 批量「标注选中文本为批注」时，先把 `Selection` 收敛到最近的 claim 文本范围内，做字符范围归一（忽略 markdown 标记符），生成 `claim` 锚点。
- `report.replace`（标存疑重生成）后按 `claimId` 重新映射；映射失败降级为 block 级并提示"批注位置可能已漂移"。
- 该锚点即批注/@ 契约草案的核心字段（§18.1）。

### 10.5 分歧/冲突视觉呈现

- `ConflictBlock`（行内）使用 `--danger-500` 描边 + 感叹号图标；文本给出双方立场与证据；提供「查看分歧详情」按钮（M3 收敛于报告内，v1.1 收敛到分歧工作台）。
- 顶部筛选：**只看分歧 / 只看已交叉验证**（MVP 至少实现"只看分歧"）。
- 报告末尾 `LimitationSummary` 汇总不可调和分歧、未决问题（含原因类型：数据缺失/源冲突/模型无法判断）。
- 信源等级差异按可信分级排序 + 颜色编码，不强求共识（DS §5.3）。

### 10.6 报告流式预览渲染（M1 最简/M2 升级）

- M1：`marked` 渲染 markdown 文本流（只读，无交互）。
- M2+：预览与终稿并存，预览保留「生成中」光标（闪烁 ≤1 次/秒）与追加 flush（§9.4）；终稿切换即进入结构化 blocks 渲染，禁止在预览态叠加溯源交互（避免双实现漂移）。

### 10.7 业务状态/枚举映射（与后端对齐）

前端在 `types/domain.ts` 声明联合类型并保留严格校验；展示用 `UiBadge` 语义色映射：

- Run：`pending|running|paused|succeeded|failed|cancelled`
- Stage：`clarify|decompose|retrieve|standardize|critique|report`，状态 `pending|running|succeeded|failed|skipped`（含 `attempt`）
- SubQuestion：`pending|queued|running|succeeded|failed|evidence_short`
- Conflict：`detected|awaiting_human|resolved|abandoned`，severity `low|medium|high`
- Evidence：`source_type=official_doc|news|community|search|internal`；`source_level=primary|secondary|tertiary`；`credibility=A|B|C|D`；`excluded_by_user`
- Tier：`quick|standard|deep|extreme`（子问题上限 3/5/8/12 的 UI 提示）

---

## 11. 页面详细设计

> 核心 6 页面详写。每个页面按统一小节：目标与布局、状态机/数据、区块与组件、事件订阅、空错载态、里程碑标注。布局视觉约束以 DS §4 与原型为基准，本文件只补充实现侧细节。

### 11.1 首页 HomeView `[M2 主体 / M1 占位]`

**目标**：用户落地入口 + 进行中研究状态总览 + 知识沉淀入口；承载意图路由单入口。

**布局**（DS §4.1 三列）：左=欢迎语+快捷新建+最近活动；中=进行中研究状态卡片（实时）；右=推荐模板（M3 起）+公告/小贴士。空状态：示例报告 + 主按钮"开始你的第一次研究"。

**单入口意图路由状态机**：

```
输入问题 → [POST /intent/classify]（≤2s 反馈，加骨架/占位提示）
    ├─ chat → 跳转 /assistant（闲聊，不落项目）
    ├─ research → 展开确认层："将启动深度研究" [选择模板/档位] [改回闲聊] [确认→/wizard?template_id=&tier=（预填，可在向导内修改）]
    ├─ uncertain → 默认走 research 路径 + 显式"识别不确定，已按深度研究准备" + 撤回选项
    └─ 接口失败/超时 → 降级走 research + 显式说明（保守策略，可"改回闲聊"）
```

- 判别等待 ≤2s；超过则进入"识别中"微态并允许继续（不阻塞）。
- 强制路径：提供"强制闲聊/强制研究"（模块 G 手动绕过），见命令面板与空输入占位旁路。
- 确认层所选模板/档位经 query 预填向导（step1/step2 显示"已预选，可修改"），避免二次选择；改回闲聊可随时从确认层或向导顶部返回。
- 与 PRD 差异注释：PRD 未定义首页视图，此页按 DS/原型设计，入口承担模块 G 单入口职责。

**进行中研究卡片**：订阅仍在 `pending/running/paused` 的 run；卡片显示标题、当前阶段、进度、成本占比（M2+）；点击进指挥舱。数据：`GET /runs?...` 分页 + 进入页面时对活跃 run 建立轻量订阅（仅读状态帧，不做证据全量）。

### 11.2 发起研究向导 WizardView `[M1 骨架 / M2 完整 / M3 全量]`

**布局**：5 步分页向导（DS §4.2），步进器 + 内容区 + 底部操作条；支持保存草稿（草稿存本地 `localStorage`，M1 可只存结构）。

| 步 | 字段/控件 | 校验与交互 | 数据去向 |
|---|---|---|---|
| 1 选择模板 | 模板卡片网格（顶部高频+推荐），抽屉预览样例 | 单选必选；默认通用模板 | `template_id` |
| 2 设置参数 | 档位（快速/标准/深度/极致，Slider + 档位卡）、约束 | 展示每档子问题上限/预算预估 | `tier`、约束条件 |
| 3 描述问题 | 多行输入 + 关键词标签 | 非空校验；长度提示；研究关键词预填 | `question` |
| 4 预读确认 | 系统对问题解读 + 预期范围 + 成本预估（只读；classify 无预估值时标注"启动后在看板查看"，数据源待确认见 §18.3 #7） | 依赖步骤 1-3；可返回修改 | 展示态 |
| 5 启动 | 启动按钮（防重复提交 + Idempotency-Key） | 提交 `POST /runs` 202 → 立即跳指挥舱 | `POST /runs` |

- 提交成功后乐观在任务列表插入 `pending` run；跳转 `/projects/:projectId/runs/:runId/cockpit`。
- 子问题编辑/排序的完整 gate 见 §18.3 待确认项——MVP 以指挥舱只读展示 + 介入抽屉追加追问为主，与后端 intervene 通道能力对齐（不做前端臆造的子问题增删接口）。
- 重复提交防护：启动按钮 3s 内禁用 + `Idempotency-Key`；服务端重复返回首次响应。

### 11.3 指挥舱 CockpitView `[M1 最简 / M2 实时 / M3 全量 / M4 强化]`

**目标**：研究执行实时看板，用户介入主战场；≤3s 用户视角刷新。

**布局**（双列 + 顶栏动作，DS §4.3）：

```
顶栏：[返回项目] 标题 | 状态：阶段 x/6 · 子问题 m/n · 已用时长 | [暂停][导出 M4][分享 M4]
左列：StageTimeline(6阶段) + 子问题列表/进度 + 冲突提示列表
右列：当前阶段详情（进度 + 实时输出/描述） + 证据流 + 成本卡 CostMeter
介入态：阶段1 澄清卡 / 阶段5 裁决等待 / 阶段3 暂停追问 → 复用 RightDrawer/行内区块
```

**状态机（run.status × 介入事件）驱动呈现**：

| 后端信号 | 前端呈现 | 可执行动作 |
|---|---|---|
| `stage.failed`（可恢复） | 顶部错误横幅 + 失败原因；后端有重试通道则给"重试"，否则展示"系统自动回溯 / 稍后恢复"提示 | 重试（通道待确认，见 §18.2 #9） |
| `interrupt.requested`（stage=clarify） | 介入抽屉 ClarificationCard（2-4 题 + 推荐默认 + 超时提示） | 回答→`resume {answers}` |
| 阶段3 检索中 | 子问题卡片进度 + heartbeat 摘要（不暴露原文） | 暂停→追加追问 |
| `conflict.detected`（awaiting_human） | 左列冲突条目红点 + 行内 ConflictBlock | 进裁决（报告/分歧工作台） |
| `cost.warning` | CostMeter 变黄/变红 + 右下角警告卡 | 升级档位(M4)/停止 |
| 暂停超 15 分钟 | 自动保持暂停态 + "确认再续"弹窗 | 继续/放弃 |
| `evidence.fetched` 后 `excluded_by_user` | 剔除后从池中隐藏（可恢复列） | 剔除/恢复 |

**证据流**：紧凑行（标题/域名/1 行摘要）；hover 展开元数据（域名/发布时间/类型/可信分级）+ ✕ 剔除；点击行打开懒加载全文详情（Popover 或抽屉）。`intervene {type:'exclude_evidence'}` 剔除，本地置灰+隐藏，事件回到 `GET /runs/{id}/evidence` 保持一致。

**成本卡**：进度条（used/budget）；70% 提示、90% danger（`cost.warning`）；断连时展示最近快照并标注"数据可能滞后"；接口失败展示"实时数据中断"降级文案（不阻塞研究）。`token.usage.update` 1s 节流写入。

**介入动作集**（顶栏/行内入口，动作经 §13 统一）：

- 暂停（软暂停：`POST /runs/{id}/pause`；顶栏按钮，模态确认）
- 取消（硬中断：`POST /runs/{id}/cancel` 或 WS `cancel`；二次确认模态，提示中间态清理语义）
- 追问：`intervene {type:'ask_followup', payload:{sub_question_id, question}}`
- 剔除证据：`intervene {type:'exclude_evidence', payload:{evidence_id}}`
- 打回重审（M4）：`intervene {type:'revert_stage', payload:{stage, reason}}`

**与 PRD 差异注释**：PRD 无"取消"操作与第 6 阶段取消；本设计采用后端详细设计已定义的 `pause/cancel` 能力；阶段6 报告生成中如需中断沿用 `cancel`（保留已推送片段为草稿语义见后端文档）。

---

### 11.4 报告阅读页 ReportView `[M1 最简 / M2 结构化 / M4 协作]`

**目标**：可信报告的双轨阅读（边生成边读 → 终稿可溯源交互）；报告协作（批注/@/标存疑）M4 起。

**页面结构**：

```
顶栏/工具条(ReportChrome)：[返回] 标题 | 元数据头(研究对象/时间范围/档位) | [筛选：只看分歧|只看已交叉验证] [分享 M4][导出 M4][⋯]
正文区：左=章节目录（Outlines，可收起）；中=blocks 正文（单列）
右侧覆盖：SourcePanel / 介入抽屉 / 批注线程统一为 RightDrawer 覆盖层（按需开，不占用正文列）
底部：信源索引区（完整溯源索引，可滚动）+ LimitationSummary（M2+）
```

**数据流（双轨，§7.4）**：

- 生成中：`useReportDraft` 订阅 SSE；无终稿时不展示角标与筛选（简化实现），仅"实时输出 + 光标"；顶部提供"报告生成中"状态条。
- `report.finished` → 切换 `useReportBlocks`：拉取 `GET /reports/{id}` 与 `GET /reports/{id}/citations`，渲染 blocks 视图并高亮迁移点；保留阅读进度（尝试滚动到最近章节）。
- `status=superseded`（M4 `report.replace`）→ 顶栏提示"内容已更新（标存疑后重生成）"，锚点重映射（§10.4）。

**区块渲染**（blocks 引擎）：

| block.type | 渲染 | 交互 |
|---|---|---|
| conclusion | 正文段落 + 结尾行内 `CitationMarker` 组 | 角标回溯；confidence 提示（single_source/cross_verified/inferred） |
| evidence | 引用区块（可折叠展开原文片段） | 展开原文 / 打开 SourcePanel |
| dispute | `ConflictBlock` 内联警告 + 双方证据 | "查看分歧详情" → 分歧工作台或裁决抽屉 |
| limitation | 局限/未决问题列表 | 纯展示 |

- 论断带 `confidence`：推断（inferred）只标"推断"，禁止按"事实"呈现；`single_source` 不做交叉验证暗示；缺失元数据标"来源信息不全"。
- 报告首帧 ≤2s：区块先渲染文本（citation 懒加载补齐），骨架屏 ≤3s 策略（DS §5.6）。

**批注与@（M4 [v1.1]）**：工具栏 `[+]` 进入批注模式 → 用户选中文本/块 → `AnnotationThread` 生成锚点（§10.4）并写回（契约草案见 §18.1）；`@` 提及项目成员（`AvatarStack` 展示在线态，站内通知由后端通知服务承接）。MVP（M3 前）不出现该入口。

**分享/导出（M4）**：分享生成只读链接（路由别名 `/runs/:id/report`，受分享 token/权限约束，后端未定待补）；导出走 `POST /reports/{id}/export`（格式待后端确认，前端在工具条占位并在导出完成前禁用重复点击）。

**与 PRD/后端差异注释**：PRD 无批注概念；架构概要实体将 Annotation/Mention 标注 v1.1，后端详细设计暂无对应 API——本页按 M4 预留，契约草案见 §18.1。

### 11.5 分歧工作台 DisputesView `[v1.1 / M4]`

**目标**：多智能体批判收敛分歧的集中处理视图（原型 05，v1.1），MVP（M3）分歧裁决收敛于报告内与指挥舱，M4 深化为工作台。

**布局**（三栏）：

```
左：分歧列表（claim 摘要 + severity/status 过滤：detected/awaiting_human/resolved）
中：选定分歧详情 —— 双方证据对比（Evidence A vs Evidence B 并排）+ 原文片段 + 元数据
右：VerdictPanel 裁决面板（choice：evidence_a | evidence_b | both | reject + reason + 忽略标记）+ 置信度/分级可视化
```

**动作流**：

- 裁决提交：`POST /conflicts/{id}/verdict {choice, reason, additional_note?}`；成功后状态置 resolved 并从等待列表移除。
- 批量裁决后恢复 run：调用 `POST /runs/{id}/resume`（裁决作为 human_input 的 awaiting_human 分支由后端解析，见后端详细设计 §6.6）；若全部冲突仍未裁决则保持 await 态。
- 打回重审（M4）：`intervene {type:'revert_stage'}` 带原因，写入审计。

**数据来源**：`GET /runs/{run_id}/conflicts` + `GET /conflicts/{id}`（含双方证据）；订阅 `conflict.detected` 增量。与报告内 ConflictBlock 共用同一裁决 action（组件复用）。

### 11.6 知识库 KnowledgeView `[v1.1 / M5]`

**目标**：报告与私域材料沉淀为可复用资产（原型 06，v1.1；DS 导航"知识库 M5 起"）。

**布局**（左右两栏）：

```
左：文档树/分类（按来源类型 evidence|report_section|external_doc|user_note、上传时间、可见范围）
右：文档预览 + 元数据 + 操作（分享到研究 / 编辑可见范围 / 删除 / 导出引用记录）
顶部：语义混合检索框（复用后端 /knowledge/search，schema 见 §18.1 待确认）
```

**关键交互**：

- 检索结果高亮命中片段 + 来源标签（公域/私域 + 可信分级）；命中项支持"加入新研究"（跳 /wizard 带入该文档上下文，对应"与知识库联动"）。
- 权限可见性（M5）：文档可见范围 `private|team|project`，依据当前用户/项目边界过滤；私域材料 SourceBadge 加"内部"水印标识（数据血缘呈现，模块 M5-2）。
- 报告沉淀：终稿报告（含 blocks 与 citations）在 M5 自动/手动沉淀为知识条目，重用时保留溯源链。

**与 PRD 差异注释**：PRD 无独立知识库页面（只有"知识沉淀入口"）；本页按原型与架构概要 v1.1 设计，纳入 M5 窗口。

---

## 12. 第二梯队页面规格

> 本组页面按「页面规格级」描述（目标/布局/关键交互/状态与组件清单/里程碑），不展开到 props 级；实现到对应里程碑时在本文档对应节扩展。

### 12.1 项目列表 ProjectListView `[M1 骨架 / M3]`

- 目标：多项目入口（对应 DS 顶栏「项目」）。
- 布局：项目卡片列表 + 状态/时间/成员筛选 + 顶部「新建项目」。
- 交互：卡片显示项目名、最近活动、研究数、成员数；新建项目弹 `UiDialog`（名称+默认模板+默认档位），成功后进任务列表。
- 空状态：引导创建首个项目（示例卡片）。
- 数据：`GET /projects`（分页/筛选 status/owner）。

### 12.2 项目内任务列表 ProjectTasksView `[M1]`

- 目标：项目下所有研究任务列表（DS §3.3「任务」导航项）。
- 布局：列表（标题、当前阶段、状态 Badge、创建时间、进度条）+ 状态筛选 + 「+ 发起研究」。
- 交互：点击行进指挥舱；运行中任务显示轻量实时状态（订阅 run 状态帧，非全量）。
- 数据：`GET /projects/{id}/runs` + 乐观插入（§7.5）。

### 12.3 成员与角色 / 项目设置 `[M1 骨架 / M4]`

- 成员页：成员列表 + 角色标签 + 邀请（管理员）/ 移除（owner/admin）。
- 设置页（M4）：项目级参数、模板绑定、档位默认上限展示（只读提示，管理员可改由运营后台承接）。
- 权限口径见 §14.3；角色模型与 PRD「研究者/管理员/审批人」的差异见 §18.3。

### 12.4 模板库 TemplateLibraryView `[M3]`

- 目标：浏览与复用模板（DS 顶栏「模板」，MVP 模板少时可隐于发起流程）。
- 布局：模板卡片网格 + 场景标签过滤 + 搜索；卡片「查看报告样例」抽屉预览。
- 数据：`GET /templates`、`GET /templates/{id}`（含报告骨架 outline）；管理员可建（`POST /templates`）。
- 交互：点击「使用此模板」→ 跳 `/wizard?template_id=`。

### 12.5 命令面板 CommandPalette `[M3，浮层非路由]`

- 触发：全局 `Cmd/Ctrl+K`；布局：顶部搜索框 + 分类命令列表。
- 命令集：新建研究、跳转项目、跳转模板、查看历史研究、账户设置、强制研究/强制闲聊。
- 行为：模糊匹配 + 上下键 + Enter；ESC 关闭；Kbd 提示高亮；`aria-dialog` + 焦点圈。
- 实现：壳层挂载，由 `ui` store 控制开关。

### 12.6 全局助手面板 AssistantView `[M2]`

- 目标：闲聊专属会话区，与研究项目**区隔**（PRD 模块 G / A14）。
- 布局：单列对话流 + 底部输入；会话树/历史（归属本人，不进项目）。
- 数据：意图路由判为 chat 后跳入；REST/SSE 通用模型流（闲聊非 6 阶段流水线；若后端以通用 LLM 直答，走普通 HTTP 或独立 SSE）。
- 约束：该面板内回答不提供报告溯源组件、不产生项目数据；「改回闲聊」从研究确认层进入此处。

### 12.7 账户与订阅 AccountView `[M1 骨架 / M4 / M6]`

- 账户：个人信息、密码、会话管理（登出全部设备）。
- 订阅/用量（M4）：档位 token 上限可视化、用量历史、预算预警设置。
- 团队管理（M6/管理员）：`GET /users?team_id` 成员、角色变更、审计日志查看（`GET /audit`，schema 待确认见 §18.1）。

---

## 13. 用户介入(HITL)统一交互设计

### 13.1 统一容器与原则

- 介入操作统一承载在 **RightDrawer（介入抽屉）** 或行内定位卡片（依据上下文密度），由壳层 `RightDrawer` 抽象统一管理（动画/ESC/焦点/滚动锁定）。
- `RightDrawer` 为**单例覆盖层**：同一时刻只展示一种内容（SourcePanel / 介入抽屉 / 批注线程 / 详情），新内容请求时替换旧内容，由壳层维护"打开来源"返回栈，避免同屏多抽屉互相抢占。
- 所有介入动作**必须**经由编排层受控通道（REST `intervene/resume/verdict/cancel/pause` 或等价 WS 指令），用户不可直接修改 prompt/Agent 状态（权限边界，DS §5.2/协作 §10.5）。
- 每个介入操作成功 → Toast + 记录审计（展示层不额外提供审计表单；管理员经 /audit 查看）。
- 统一幂等：WS 指令带 `request_id`，REST 写带 `Idempotency-Key`，双击不重复提交。

### 13.2 介入场景 → UI 通道映射

| 阶段 | 场景 | 触发 | 请求形态 | UI 容器/组件 | 版本 |
|---|---|---|---|---|---|
| 启动前 | 改回闲聊（撤回） | 确认层 | 跳 /assistant | 确认层内联 | M2 |
| 1 | 澄清回答 | `interrupt.requested` | `resume {answers}` | ClarificationCard in drawer | M2 |
| 1 | 澄清超时 | 服务端默认假设 | 仅提示（只读标注"已按默认假设"） | 状态条 | M2 |
| 3 | 暂停/继续 | 顶栏按钮 | `pause` / `resume`（请求体以 §18.2 #4 确认后为准，不预造 action 字段） | 模态确认 | M2 |
| 3 | 追加追问方向 | 阶段节点「+ 追问」 | `intervene {ask_followup}` | 内联输入 | M2 |
| 3 | 剔除证据 | 证据行 hover ✕ | `intervene {exclude_evidence}` | 行内 + Toast | M2 |
| 5 | 打回重审 | 阶段节点「↺ 打回」 | `intervene {revert_stage}` | 模态输入原因 | M4 |
| 5 | 裁决冲突 | 冲突块「去裁决」 | `POST /conflicts/{id}/verdict` 或 `resume` | VerdictPanel | M3/M4 |
| 6 | 取消生成 | 顶栏「停止/取消」 | `cancel`（二次确认） | 模态 | M3（依后端能力） |
| 报告 | 标存疑（触发补查/重生成） | 论断角标旁「⚠ 存疑」 | 契约草案（M4 预留，`POST /reports/{id}/doubt` 待后端） | 行内按钮 | M4 预留 |
| 报告 | 批注 / @ 提及 | 工具栏 [+] | 契约草案（§18.1） | AnnotationThread | M4 |

### 13.3 操作可见性与权限矩阵

基于后端角色 `owner|admin|researcher|reviewer` + 项目成员边界（PRD 角色口径差异见 §18.3）：

| 操作 | owner | admin | researcher（成员） | reviewer（成员） |
|---|---|---|---|---|
| 发起研究 / 追问 / 剔除 / 暂停 / 取消 | 允许 | 允许 | 允许 | 仅查看 |
| 裁决 / 打回重审 / 标存疑 | 允许 | 允许 | 允许 | 仅查看 |
| 批注 / @ 成员（M4） | 允许 | 允许 | 允许 | 允许（批注） |
| 项目外数据访问 | 越权按 404 处理（前端不猜 403） | — | — | — |

前端只做「入口显隐 + 按钮禁用」的弱约束，强约束由后端强制执行；后端返回 `INTERVENE_NOT_ALLOWED` 等错误时前端统一 Toast 该 code 的文案映射。

---

## 14. 鉴权、权限与多租户前端模型

### 14.1 会话生命周期

```
未登录 → Login（email+password）→ POST /auth/login → 得到 access(30min)+refresh(7d)
  ├─ 内存持有 access；refresh 落 localStorage（键名约定），access 不落盘
  ├─ 401 → single-flight refresh（§8.2）→ 重放请求
  ├─ refresh 失败/被撤销 → 清会话 → 跳登录（保留 redirect）
  ├─ 登出 → POST /auth/logout + 清本地
  └─ 页面关闭/跨标签 → 以 localStorage refresh 静默恢复（可选）
```

- access 过期前后置刷新：在每请求头或定时器（略低于 30min）触发静默续期，降低 401 频率。
- 会话过期弹一次性提示（不清空已开页面数据），用户可即时重登。
- M3+ SSO：登录页提供 SSO 入口；`/auth/sso/callback` 完成 code 换 token 后回跳 `redirect`。

### 14.2 token 安全约束

- 明文 token 仅允许存在于内存与 refresh 存储；禁止写入日志/埋点/URL query（WS 鉴权如后端仅支持 query，需后端切换子协议方式时记录为阻塞项）。
- 生产环境建议由后端配置 CSP/合理 cookie 策略；前端不实现自研加密存储。

### 14.3 权限模型落地

- 数据来源：登录响应 `user.role` + JWT 解码（`team`/`role`，仅作弱缓存）+ 项目详情/成员接口。
- 前端分三类控制：**路由可达**（§4.3）、**入口显隐**（顶栏/项目导航）、**按钮级**（介入操作 §13.3）；一律弱约束 + 后端强校验。
- 多租户隔离体验：任何越权统一按 404 展示"资源不存在或无权访问"，不区分 403/404，避免泄露资源存在性。
- 团队切换/离开 → 清空 project 与 run 缓存（§7.5）。

### 14.4 XSS 与内容安全（前端职责）

- 报告正文、证据 snippet、AI 生成的 markdown 均视为不可信内容：`v-html` 仅允许经过白名单净化（净化函数只放行 DS 允许的少量标签/属性）或统一走结构化渲染器（默认推荐，报告终稿不落 `v-html`）。
- 外链统一 `rel="noopener noreferrer"` + 域名判断后在 UI 标注外链。
- 用户输入（问题/批注/裁决理由）经 Vue 默认插值转义；长度与服务端一致性校验。

---

## 15. 可访问性与体验基线

### 15.1 与验收挂钩的基线

| 维度 | 基线 | 验证口径 |
|---|---|---|
| 文本对比度 | WCAG 2.1 AA（正文≥4.5:1） | 深浅两套主题各验一次；信源语义色在两种主题可用性 |
| 键盘操作 | 全交互元素可达；角标/冲突块/阶段节点键盘可开（DS §7.1） | 抽查报告页与指挥舱 |
| 焦点可见 | `:focus-visible` 品牌外发光 2px | 全站 |
| 动效 | `prefers-reduced-motion` 时长归零 | 系统开启减少动效复测关键页面 |
| 语义结构 | `header/main/nav/aside/article` 正确 | 报告/指挥舱语义化抽查 |
| 实时更新播报 | 阶段推进/run 终态用 `aria-live="polite"` 摘要播报；证据流不逐条打扰 | 指挥舱 |
| 屏幕阅读器 | 角标/冲突/阶段节点提供 `aria-label` 文本 | 报告 + 时间线 |

### 15.2 关键页键盘模型

- 报告：`Tab` 遍历角标 → `Enter/Space` 开 SourcePanel；方向键在 citation 之间切换；`Esc` 关闭抽屉并回焦点。
- 命令面板：`Up/Down` 选择、`Enter` 执行、`Esc` 关闭。
- 介入抽屉：焦点进入首控件；`Esc` 取消；提交后焦点回触发按钮。

---

## 16. 埋点与前端可观测

### 16.1 Telemetry 模块设计

`services/telemetry` 提供 `track(event, props?)`；事件统一 schema：`{ event, ts, run_id?, page, props }`。MVP 先本地汇聚（`localStorage` 分页缓冲 + 到达阈值批量上报），dev 可 console 预览；上报端点草案见 §18.1（`POST /api/v1/telemetry/batch`，待后端确认）。埋点不得阻塞主链路（失败静默 + 丢批可接受）。

### 16.2 事件目录（对齐 PRD A8 / 体验指标）

| 埋点 | 归属指标 | 触发时机 |
|---|---|---|
| `run.created` / `run.finished.ok` / `run.finished.fail` | 报告生成成功率（前端侧观察） | POST /runs 成功；收到 `run.finished` |
| `cockpit.view.stage_*`（阶段推进帧差） | 看板刷新延迟 ≤3s | 相邻两帧 stage 事件→渲染时间戳差 |
| `cockpit.intervene.{pause,ask_followup,exclude_evidence,revert_stage,cancel}` | 看板介入率 | 各类介入动作触发成功/失败 |
| `verdict.submit` | 裁决采纳/忽略率 | 提交裁决成功 |
| `report.citation.open` | 溯源可回溯率（交互侧） | 点击角标开抽屉 |
| `report.filter.{dispute,cross_verified}` | 报告功能使用 | 切换筛选 |
| `wizard.{step_change,submit,dropout}` | 发起转化 | 向导交互 |
| `assistant.open` | 闲聊分流观察 | 进入助手面板 |
| `page.{route}.{first_frame}` | 首帧 ≤2s 性能预算 | PerformanceObserver/自定义时间戳 |

刷新延迟实现提示：在 WS/SSE 解码处记录服务端 `ts`，在 `requestAnimationFrame` 提交渲染后记录本地渲染完成时间，二者差值聚合到指标（含重连/断线事件不计入）。

### 16.3 性能预算（纳入 MR 门禁提示）

| 项 | 预算 | 测量 |
|---|---|---|
| 主包 gzip | ≤260KB | 构建产物告警 |
| 报告页首帧 | ≤2s | FCP/自定义 |
| 指挥舱状态刷新 | ≤3s（用户视角） | 埋点 P95 |
| 证据行增入 | 不整表重渲染 | 组件级审计 |
| 报告终稿 blocks 首屏 | 文本先于 citations 呈现 | 时间戳埋点 |

---

## 17. 里程碑落地地图

与 SDP 对齐的前端交付清单；SDP 里程碑验收对应的 PRD A 项沿用 SDP §5.2 口径。

| 里程碑 | 前端交付物 | 对应章节 | 与验收/上下游对齐 |
|---|---|---|---|
| M0 | 脚手架/CI/规范、tokens.css 迁移、@layer 主题机制、基础组件库（§6 表） | §3、§5、§6 | SDP M0-1；可起 dev、lint/type/build 通过 |
| M0-M1 | mock 网关 + 研究模拟器 + fixtures | §8.5 | 前后端并行前提；M1 demo 复用 |
| M1 | 登录页、AppShell/路由壳、项目任务列表（骨架）、发起向导最简、指挥舱最简、报告最简（markdown 流） | §4、§11.2-11.4、§12.2 | SDP M1-5；PRD A1（最小闭环） |
| M2 | 首页单入口 + 意图路由 + 独立助手面板、指挥舱实时看板（StageTimeline/证据流/冲突提示）、报告结构化 v1（blocks+角标+局限）、澄清/追问/剔除介入、成本最简 | §11.1-11.4、§13 | PRD A2/A3/A5/A8/A11/A13/A14 由前端交互与埋点承担；A12 召回率由后端离线评估举证（M2 首次通过集以 SDP §5.2 为准：A2、A3、A5、A8、A11、A12、A13、A14） |
| M3 | MVP 全量：向导完整（含预读确认）、报告四区块 + 分歧筛选 + VerdictPanel（报告内）、成本分档可视化 + 降级/扩容提示、模板库、命令面板骨架、深色模式全量 QA | §10、§11、§12.4-12.5 | PRD A4/A6/A7/A9/A10；SDP M3 内测 |
| M4 | 体验打磨：实时成本强化（≤3s）、报告批注/@/标存疑（契约草案联调）、证据时间线/可信分级 SVG、导出/分享入口、分歧工作台（v1.1）、项目内成员/设置页、介入动作补全（打回/标存疑） | §11.4-11.5、§12.3、§13、§18.1 | 无新增 PRD A 项（A1-A14 为 MVP 清单，不含导出/体验增强）；以 SDP §3 M4 自有验收准则考核 |
| M5 | 知识库页（检索/沉淀/联动）、数据血缘呈现（内部 vs 外部 + 私域水印）、数据源级权限可见性、连接器配置 UI（规格级） | §11.6、§12 | 无新增 PRD A 项（私域属 PRD V2 范围）；以 SDP §3 M5 自有验收准则考核 |
| M6 | 账户/团队/订阅、审计查看页、运营指标页、埋点看板就绪、SLA 状态提示 | §12.7、§16 | SDP M6-2/3；PRD A1-A14 全量回归 + PRR |

**落地顺序依赖提示**：M2 的意图路由/助手面板依赖 `POST /intent/classify`（后端 M2-1）；M3 报告分歧筛选依赖结构化终稿字段（后端 M2-7/M3）；M4 批注/导出依赖 §18.1 契约草案评审通过（后端无现成 API）。

---

## 18. 附录

### 18.1 前端视角契约草案（已并入后端契约草案 v0.1）

> **状态更新（2026-09-09）**：本节原作为"前端起草、待后端评审"的契约草案。契约草案评审通过后已合并为[后端契约草案](./AI研究者助手-后端契约草案.md)（v0.1）§7/§9/§11/§13/§14/§18.1，本节保留作为"前端示例样态与字段语义映射说明"（snake_case 契约字段 → 前端 camelCase TS 字段），不再作为唯一权威。所有契约字段以[后端契约草案](./AI研究者助手-后端契约草案.md)为准，前端 TS 客户端按草案定义生成类型后映射 camelCase。

**报告批注（M4 [v1.1]）**

```jsonc
// POST /api/v1/reports/{report_id}/annotations
{
  // 契约字段一律 snake_case；§10.4 前端内部 ReportAnchor 为 TS camelCase，仅映射层差异
  "anchor": { "kind": "claim", "claim_id": "clm_...", "range": [12, 48] },
  "content_md": "这里的数据口径需要复核。",
  "mentioned_user_ids": ["usr_..."]        // @提及（可选，进入 mentions 记录）
}
// 200 → { "annotation_id": "ant_...", "anchor": {...}, "content_md": "...", "created_by": "usr_...", "created_at": "..." }
// GET  /api/v1/reports/{report_id}/annotations?block_id=&claim_id=   // 按锚点范围过滤
// DELETE /api/v1/annotations/{annotation_id}
```

**标存疑触发重评估（M4 预留）**

```jsonc
// POST /api/v1/reports/{report_id}/claims/{claim_id}/doubt
{ "reason": "数据来源时效存疑", "note": "" }
// 语义：通知 Reporter 重新评估该 claim → SSE report.replace 回流新版（后端负责实现语义）
```

**报告导出（M4）**

```jsonc
// POST /api/v1/reports/{report_id}/export  →  建议返回任务/文件形态：
//  { "export_id": "exp_...", "status": "queued|ready|failed", "file_url": "s3://...", "format": "pdf|docx" }
// 前端轮询 GET /exports/{export_id} 或等 SSE/站内通知；format 与样式模板由后端评审确认。
```

**知识库检索（M5）**

```jsonc
// POST /api/v1/knowledge/search
{
  "query": "新能源汽车出海政策", "filters": { "types": ["evidence"], "visibility": ["project"] },
  "page": 1, "page_size": 20
}
// → { "items": [{ "knowledge_item_id": "ki_...", "title": "...", "content_excerpt": "...",
//                 "source_type": "...", "source_private": false, "credibility": "B",
//                 "highlights": [{ "start": 10, "end": 30 }], "score": 0.87 }],
//     "total": 5, "has_more": false }
```

**审计条目（M6/管理员）**

```jsonc
// GET /api/v1/audit?target_type=run&target_id=run_...&page=1
// → { "items": [{ "audit_id": "audit_...", "user_id": "usr_...", "action": "intervene.exclude_evidence",
//                 "target_type": "run", "target_id": "run_...", "payload": { "evidence_id": "ev_..." },
//                 "created_at": "..." }], "total": 1, "has_more": false }
```

**埋点批量上报（M2 起，可后置）**

```jsonc
// POST /api/v1/telemetry/batch
{ "events": [{ "event": "report.citation.open", "ts": 1736486400123, "run_id": "run_...", "page": "report", "props": {} }] }
```

### 18.2 已知文档内部差异与联调确认清单（找后端核对）

**状态更新（2026-09-09，契约草案 v0.1 评审通过后合并）**：以下 9 项已与[后端契约草案](./AI研究者助手-后端契约草案.md)（v0.1）定版对齐，前端 TS 客户端按草案定义生成 snake_case 字段后映射为 camelCase，不再保留前端侧的"差异兜底"分支。

| # | 事项 | 草案定版位置 | 前端处理 |
|---|---|---|---|
| 1 | `interrupt.requested` payload | 草案 §4.1：reason/questions[]/defaults/expires_in_seconds | §5 澄清卡按 `questions[]` + `recommended` 渲染 |
| 2 | `cost.warning` payload | 草案 §4.2：level/used/budget/ratio | §4 成本卡按 ratio 驱动进度条 |
| 3 | SSE `report/stream` data | 草案 §4.3：data 为完整 envelope JSON | §8 报告流解析按统一 envelope 处理 |
| 4 | `/auth/refresh`、`/runs/{id}/pause`、`cancel` | 草案 §5.1/§6.1 | §13 会话模块按 refresh+logout 接入；§11 暂停/取消 UI 按 reason/note 接入 |
| 5 | verdict `additional_note` | 草案 §6.3：choice/reason 必填，additional_note 仅展示 | §5 裁决表单字段与语义锁定 |
| 6 | WS 鉴权通道 | 草案 §5.3：Sec-WebSocket-Protocol `bearer, <jwt>`（协议名固定 `bearer`，服务端回显 `bearer`） | §14.2 WS 客户端按子协议注入 token，URL 不带 token |
| 7 | 批注/@/导出/knowledge-search/audit schema | 草案 §7/§9/§11/§13/§18.1 | §6 批注/导出/§10 知识库/§15 审计页按 schema 接入 |
| 8 | 通知服务端点 | 草案 §12 | §12 通知中心按 kind/link 接入 |
| 9 | stage 失败重试/回溯通道 | 草案 §6.4：intervene.action 增补 `retry_stage`（待产品确认） | §11.3 失败入口不预造按钮；后端提供 `retry_stage` 时再开放 UI |

### 18.3 产品/范围待确认项

**状态更新（2026-09-09）**：

| # | 事项 | 草案定版位置 | 前端处理 |
|---|---|---|---|
| 1 | 子问题编辑/排序 gate | 草案 §6.5：预留 `POST /runs/{id}/subquestions/replace`，M1-M2 只读，M3 打开 | §4 发起研究向导 step3 子问题列表：M3 前只读、M3 起可拖拽编辑 |
| 2 | "取消"口径 | 草案 §6.1：硬中断 + 保留草稿 | §11 取消 UI 文案按"取消并保留草稿"实现 |
| 3 | 角色模型 | 草案 §16：owner/admin/researcher/reviewer 四角色 | §13 权限组件按后端枚举映射，PRD 角色留映射表 |
| 4 | 知识库/分歧工作台定档 | 草案 §17.1：分歧 M4、知识库 M5 | 维持草案定档 |
| 5 | 批注/@ 是否并入 M4 | 草案 §17.1：批注/@ 归 M4 | §6 批注 UI 按 M4 排期 |
| 6 | 首页空状态样例报告 | — | 由产品提供样例内容 |
| 7 | 启动前成本预估值 | 草案 §6.6：扩展 `/intent/classify` 响应增加 `estimated_token_budget` / `estimated_cost`（推荐） | §4 向导 step4 启动前展示估算值（不发起 run，仅展示） |

### 18.4 术语表

| 术语 | 含义 |
|---|---|
| blocks | 报告结构化渲染单元（conclusion/evidence/dispute/limitation） |
| claim | 论断：报告中带事实/数据点的声明，绑定 citations |
| CitationMarker | 行内溯源角标 |
| envelope | WS/SSE 统一事件封装（v/event_id/ts/run_id/stage/type/payload） |
| HITL | Human-in-the-Loop，用户介入 |
| RightDrawer | 壳层右侧抽屉抽象容器 |
| run | 一次研究运行 |

---

## 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.93 | 2026-09-13 | 跟随 SDP v1.1 勘误同步修正 §17 里程碑落地地图：M2 首次通过集改为 A2/A3/A5/A8/A11/A12/A13/A14（补 A5/A12，A12 为后端评估口径），M3 改为 A4/A6/A7/A9/A10，M4/M5 不再挂 A 项，M6 改为 A1-A14 全量回归 + PRR；A 编号定义以 PRD §11 为唯一事实源，映射以 SDP §5.2 为准 |
| v0.92 | 2026-09-09 | 与[后端契约草案](./AI研究者助手-后端契约草案.md) v0.1 对齐：§18.1 标注已并入、§18.2 九项差异标记为"已对齐"并指向草案定版位置、§18.3 七项产品待确认项按草案 §6.5/§6.6/§16/§17.1 落地；前端 snake_case 契约映射 camelCase TS 不再保留差异兜底 |
| v0.91 | 2026-09-09 | 评审修订：修复 P1/P2（组件示例、openapi 依赖、Assistant 形态冲突、stage 重试假设、路由参数与默认落地、成本预读数据源、RightDrawer 互斥、契约草案字段 snake_case），补 §18.2 #9 与 §18.3 #7 |
| v0.9 | 2026-09-09 | 起草稿：全范围单文档初稿，待里程碑评审与后端契约核对后升 v1.0 |

**下一步**：

1. 按[后端契约草案 §17 OpenAPI 落地路径](./AI研究者助手-后端契约草案.md#17-里程碑窗口与-openapi-落地)合并 OpenAPI 规范，前端客户端重新生成；
2. M1 sprint 内完成 §13 会话模块（refresh/logout/WS 子协议）+ §5 澄清卡 + §4 成本卡（按草案 §4.1/§4.2/§4.3 形态）；
3. M4 起补批注/@、报告导出、只读分享、通知中心相关 UI；
4. M3 起打开子问题编辑/排序 gate（草案 §6.5）；
5. 本文件升级 v1.0 时，补齐每页 props/事件级细则到需要实现的前置里程碑。

