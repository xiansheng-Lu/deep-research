# AI 研究者助手 · 前端 M0 收尾方案

> 版本: v1.0 · 生效日期: 2026-09-10 · 适用范围: 前端 M0 剩余交付物
> 上游依据: 《前端详细设计》§6、§8.5、§17 与《后端契约草案》v0.1
> 目标: 在 M0 周期内补齐"基础组件库 + mock 网关 + 研究模拟器 + fixtures"，使前后端可在同一契约下并行联调，并为 M1 最小闭环 demo 提供可复用基线。

---

## 1. 背景与现状

### 1.1 已就绪（2026-09-09 完成，提交 74eb8d8 / 1d706d7 / b057f06 / 30ef977）

- 工程化：Vue3.5 + Vite5.4 + TS5.6 + pnpm + ESLint9 + Prettier3，dev/build/typecheck 全部通过。
- 主题机制：tokens.css（自 prototype 迁移）+ base/theme/utilities 三层 + `[data-theme]` 切换 + 反闪策略。
- 应用骨架：main.ts / App.vue / AppShell / 嵌套路由 / 5 个 Pinia store / 4 个 service 模块 / 实时事件 envelope 类型。
- 基础组件库：**仅 2/17**（UiButton、UiCard）。

### 1.2 缺口（M0 未完成）

按《前端详细设计》§6.1 + §17 M0 落地表，存在三块缺口：

| 缺口 | 数量/范围 | 阻塞关系 |
|---|---|---|
| 基础组件库剩余 | 11 个 M0 组件（UiInput/UiTextarea/UiSelect/UiTabs/UiDialog/UiDrawer/UiPopover/UiDropdown/UiTooltip/UiToast/UiCheckbox/UiRadio/UiSwitch/UiSlider/UiBadge/UiTag/UiSkeleton/UiEmpty/UiErrorState，其中 UiCard 已实现） | 阻塞 M1 任何带表单/弹层/状态反馈的页面 |
| Mock 网关 | `/api/v1/*` REST + WS `/api/v1/ws/runs/{run_id}/stream` + SSE `/api/v1/runs/{run_id}/report/stream` 三端同端口 | 阻塞 M1 demo 与并行联调 |
| 研究模拟器 + fixtures | 一份完整研究剧本（M1 demo 级别）+ 一个最简无中断剧本（happy path） | 阻塞实时链路验证（断线补齐/节流/重连/澄清挂起/resume） |

### 1.3 决策依据

- "补 M0 收尾"是路线 1，明确为本期方案。
- "复用 fixtures 驱动自动化验收 + 演示 demo"（§8.5）要求 fixtures 同时满足运行时回放与脚本驱动两层用途，决定采用「指令脚本 + 节点解释器」而不是硬编码时序。
- §6.2 通用约定（命名/scoped/可访问性/禁用态/尺寸体系 token 化）是所有组件的硬约束，本方案据此收敛。

---

## 2. 目标与非目标

### 2.1 目标（本期必交付）

1. 完成 §6 表中全部 M0 基础组件（共 13 个，含 UiButton/UiCard），覆盖率 100%。
2. 完成 Mock 网关：REST 基础端点 + WS 实时频道 + SSE 报告流，端点集合覆盖 M1 闭环最小需求。
3. 完成研究模拟器：以指令脚本描述一次研究全过程，网关按脚本推进，envelope 与生产同构。
4. 至少 2 份 fixtures：happy_path（无中断）与 demo（含澄清/冲突/成本预警/报告流）。
5. 全部产出通过 typecheck/build 双门禁，组件通过 Storybook 不可用情况下的"演示页 + 视觉验收"双轨验收。

### 2.2 非目标（本期不做）

- M1 业务页面（向导/指挥舱/报告）实际接入数据 — 留待 M1。
- 真实后端接口联调 — 等后端 M1-1 接口契约落地。
- 组件 Storybook 化（未在 SDP/设计文档要求中），改为"演示页 + Tokens Showcase"集中验收。
- M1+ 组件（AvatarStack / Kbd）。
- 浮层动画过渡（仅满足基本进入/退出，复杂动效 M4 体验打磨期再做）。

---

## 3. 范围与组件清单

### 3.1 基础组件交付清单（13 个）

分组落实，按依赖顺序开发（无依赖 → 浮层底座 → 表单 → 反馈）：

| 顺序 | 组件 | 类型 | 关键依赖 | 关键 props/约束 |
|---|---|---|---|---|
| 1 | UiInput | 表单 | — | `modelValue`、`type`、`label`、`hint`、`error`、`autofocus`、`disabled`、`size` |
| 2 | UiTextarea | 表单 | UiInput 样式 | `modelValue`、`rows`、`label`、`hint`、`error`、`autosize` |
| 3 | UiCheckbox | 表单 | — | `modelValue`、`label`、`indeterminate`、`disabled` |
| 4 | UiRadio | 表单 | UiCheckbox 视觉基线 | `modelValue`、`value`、`label`、`name`、`disabled` |
| 5 | UiSwitch | 表单 | UiCheckbox 视觉基线 | `modelValue`、`label`、`disabled` |
| 6 | UiSlider | 表单 | — | `modelValue`、`min`、`max`、`step`、`ticks[]`、`disabled`（档位/预算） |
| 7 | UiBadge | 反馈 | — | `variant: neutral/info/success/warn/danger`、`size: sm/md` |
| 8 | UiTag | 反馈 | UiBadge 基线 | `closable`、`icon?`（信源/里程碑标签） |
| 9 | UiSkeleton | 反馈 | — | `width`、`height`、`radius`、`count`（加载 ≤3s） |
| 10 | UiEmpty | 反馈 | — | `title`、`hint`、`icon?`、默认主操作插槽 |
| 11 | UiErrorState | 反馈 | — | `error: ApiError`、`onRetry`、`onBack` |
| 12 | UiTabs | 导航 | — | `modelValue`、`tabs: {key,label,disabled}[]`，下划线样式，受控/非受控 |
| 13 | UiToast | 反馈 | UiDialog 关闭逻辑 | 顶部消息、`type: info/success/warn/danger`、`duration=4000ms`、`closable`；全局单例 |
| 14 | UiDialog | 浮层底座 | — | `v-model`、`size`、`title`、`closeOnMask`、`closeOnEsc`、焦点困于面板 |
| 15 | UiDrawer | 浮层底座 | UiDialog | 右侧抽屉、`size`、复用 `RightDrawer` 抽象、`--shadow-drawer` |
| 16 | UiPopover | 浮层 | @floating-ui/dom | `placement`、`trigger`、`offset`、自动翻转、ESC 关闭 |
| 17 | UiDropdown | 浮层 | UiPopover | `items: {key,label,disabled,danger}[]`，键盘导航 |
| 18 | UiTooltip | 浮层 | @floating-ui/dom | `content`、`placement`、`delay=400ms`，支持纯文本/富内容插槽 |

> 表中合计 18 项（含 UiButton/UiCard 已存在）；实现节奏按 4 个里程碑切片交付。

### 3.2 Mock 网关端点覆盖

按 M1 最小闭环所需接口，参考《架构设计概要》§8 与《后端契约草案》端点表，落地以下端点（M1 demo 期间可用）：

| 类别 | 端点 | 用途 | M0 收尾覆盖 |
|---|---|---|---|
| 健康 | `GET /api/v1/health` | 心跳/页面探活 | 必 |
| 鉴权 | `POST /api/v1/auth/login` | dev 模式登录 | 必 |
| 鉴权 | `POST /api/v1/auth/refresh` | 刷新 access | 必 |
| 鉴权 | `GET /api/v1/auth/me` | 当前用户 | 必 |
| 项目 | `GET /api/v1/projects` | 列表 | 必 |
| 项目 | `POST /api/v1/projects` | 创建 | 必 |
| 项目 | `GET /api/v1/projects/{project_id}/tasks` | 任务列表 | 必 |
| 运行 | `POST /api/v1/projects/{project_id}/runs` | 发起 run | 必 |
| 运行 | `GET /api/v1/runs/{run_id}` | run 实体 | 必 |
| 运行 | `POST /api/v1/runs/{run_id}/pause\|resume\|cancel` | 控制 | 必 |
| 运行 | `POST /api/v1/runs/{run_id}/sub-questions/plan` | 确认计划 | 必 |
| 分歧 | `GET /api/v1/runs/{run_id}/conflicts` | 分歧列表 | 必 |
| 分歧 | `POST /api/v1/conflicts/{conflict_id}/verdict` | 裁决 | 必 |
| 报告 | `GET /api/v1/runs/{run_id}/report` | 结构化终稿 | 必 |
| 实时 | `WS /api/v1/ws/runs/{run_id}/stream` | 实时频道 | 必 |
| 实时 | `SSE /api/v1/runs/{run_id}/report/stream` | 报告流 | 必 |
| 模板 | `GET /api/v1/templates` | 模板列表 | 必 |
| 模板 | `GET /api/v1/templates/{template_id}` | 模板详情 | 必 |
| 埋点 | `POST /api/v1/telemetry/batch` | 埋点上报 | 必 |
| 团队 | `GET /api/v1/teams/current` | 当前团队 | 必 |

未覆盖端点（M4+ 报告批注/分享、knowledge/audit/intent/connectors 等）本期不接入，由真实后端在对应里程碑提供。

### 3.3 Fixtures 剧本清单

| Fixture | 时长（默认） | 关键节点 | 验收目标 |
|---|---|---|---|
| `happy_path` | ~25s | 6 阶段顺序推进 → 报告流 → run 完成 | 端到端链路通畅；SSE 流式渲染；WS 心跳；成本节流 |
| `demo_full` | ~60s | 同上 + 1 次澄清挂起/恢复 + 1 个冲突裁决 + 1 次成本预警 | 澄清/裁决/预警三类介入 UI 全部触发；断线补齐 |

后续 M3 可补充 `failure_recovery`、`high_throughput` 等剧本。

---

## 4. 架构设计

### 4.1 组件分层与依赖

```
components/ui/（本方案落地）
├── form/    UiInput UiTextarea UiCheckbox UiRadio UiSwitch UiSlider
├── feedback/UiBadge UiTag UiSkeleton UiEmpty UiErrorState UiToast
├── overlay/ UiDialog UiDrawer UiPopover UiDropdown UiTooltip
├── nav/     UiTabs
└── base/    UiButton（已有） UiCard（已有）
```

导出统一从 `@/components/ui/index.ts` 聚合，views 仅导入聚合入口，禁止跨目录相对路径。

### 4.2 浮层底座抽象

`UiDialog` / `UiDrawer` 共享一份 `useFocusTrap`（focus-trap 自研小实现，符合 §6.2 约束）。`UiPopover` / `UiTooltip` / `UiDropdown` 全部走 `@floating-ui/dom` 的 `computePosition` + `autoUpdate`：

- 默认 placement：top/bottom/left/right + 8 个对齐变体。
- 翻转策略：首选 + flip + shift；避让容器视口边界。
- offset：sm=4 / md=8（统一 token：`--space-1`/`--space-2`）。

`RightDrawer` 作为 `UiDrawer` 的导出别名，供后续 wizard/disputes 等复用。

### 4.3 Mock 网关架构

形态遵循 §8.5：以 **Vite dev 插件**形式内嵌在 Vite dev server 同一端口，前端零改造切换。

```
frontend/
├── src/services/mock/
│   ├── gateway.ts        # Vite 插件入口：拦截 /api、/ws、/sse 路由
│   ├── router.ts         # REST 路由分发
│   ├── realtime.ts       # WS 频道管理 + 事件广播
│   ├── sse.ts            # SSE 流管理
│   ├── store.ts          # 内存态：users/projects/runs/conflicts/reports
│   ├── seed.ts           # fixtures 数据种子（项目/任务/模板）
│   ├── script/           # 指令脚本解释器
│   │   ├── runner.ts     # 节点调度 + 延时 + 故障注入
│   │   ├── envelope.ts   # 生产同构 envelope 构造
│   │   └── types.ts      # ScriptNode = Sequence|Emit|Parallel|Wait|Interrupt|Complete
│   └── fixtures/
│       ├── happy_path.ts
│       └── demo_full.ts
└── vite.config.ts        # 通过 plugins 引入 mock 网关（仅 dev）
```

技术栈选型：

- **WS**：`ws` 包（成熟、轻量）。
- **SSE**：直接走 Node `http.ServerResponse` 写入 `text/event-stream`，避免引入额外依赖。
- **REST**：原生 `http.createServer` 复用，由 Vite 插件 `configureServer` 接管 `server.middlewares`。
- **脚本语言**：TypeScript DSL（节点联合类型 + Builder 辅助函数），拒绝自研字符串脚本，保持静态可校验。
- **状态持久化**：仅内存（dev only）。关闭 dev server 即丢弃，匹配 §8.5 约束。

### 4.4 前端与服务边界

按 §3 依赖方向约束：业务组件 → composables → services/api（自动生成客户端）。Mock 网关仅在 services 层之下提供"传输替身"，保证 composables 不感知。

- dev 模式开关：`.env.development` 的 `VITE_MOCK=gateway`（`off` 即不挂载网关，指向真实后端）。`vite.config.ts` 已就绪，仅需把 mock 插件挂入 `plugins` 即可启用。
- 类型生成：本期不接 openapi-generator（无后端 OpenAPI 产物），由前端手写 `types/openapi.d.ts` 占位契约；后端 M1-1 接口契约定稿后切换。

### 4.5 RealtimeClient 衔接

§9 RealtimeClient 在本期仅做最小可用版本：

- 单 WS 频道建立、heartbeat（30s ping/pong 帧）、断线指数退避重连。
- 事件解码：按 `types/realtime.ts` envelope 联合类型分支。
- 引用计数共享：保留设计槽位，run 引用归零延迟 5s 断连（避免指挥舱/报告切换时反复建立）。
- `useRunStream` 提供最小骨架（state + connect + disconnect），业务字段（stages/evidence/cost）按 §7.3 形态预留但不实现完整订阅路由（M2 详做）。

---

## 5. 实现路线

按四阶段推进，每阶段产出可单独验证 + 可回退 + 可分支合并。

### 阶段 A：组件库基线（无依赖组） 约 3 PR

- A1：表单基线 6 件（UiInput/UiTextarea/UiCheckbox/UiRadio/UiSwitch/UiSlider）
- A2：反馈基线 6 件（UiBadge/UiTag/UiSkeleton/UiEmpty/UiErrorState/UiToast）
- A3：导航 1 件（UiTabs）

验收：`pnpm typecheck && pnpm build` 通过；`/dev/ui-showcase` 演示页静态展示各组件。

### 阶段 B：浮层底座 + RealtimeClient 最小骨架 约 2 PR

- B1：UiDialog + UiDrawer + useFocusTrap
- B2：UiPopover/UiDropdown/UiTooltip + RealtimeClient 最小骨架（含 WS 心跳/重连）

验收：演示页触发遮罩点击关闭/ESC 关闭/焦点循环；WS 频道与 mock 网关握手成功。

### 阶段 C：Mock 网关 + Fixtures 约 2 PR

- C1：REST + WS + SSE 三端基础通道 + seed 数据 + happy_path 剧本
- C2：demo_full 剧本（含澄清/冲突/成本预警）+ 端点表 100% 覆盖

验收：

- 用 curl 走通 `POST /runs` → WS 收 `stage.started` → SSE 收 `report.chunk` → `run.finished`。
- 浏览器端 `/dev/run-demo` 路径能直接观察前端渲染与上述事件对应。
- typecheck/build 通过。

### 阶段 D：CI + 验收门禁 约 1 PR

- D1：GitHub Actions 工作流（lint / typecheck / build）+ artifacts 上传

验收：PR 触发工作流绿灯；前端 M0 完整闭环。

---

## 6. 接口与契约要点

### 6.1 与后端契约草案对齐

- 错误统一 RFC 7807（type/title/status/detail/instance/code/trace_id）。
- snake_case ↔ camelCase 映射沿用 §18.1 既定方案。
- envelope `{ts, run_id, event_id, type, payload}`，与 `types/realtime.ts` ServerEvent 联合类型严格一致。
- 分页 `{items, total, page, page_size, has_more}`。
- `Idempotency-Key` 支持（POST 创建类端点）。

### 6.2 前端内部约定

- 组件 props 显式声明、事件 `update:modelValue` / 语义化命名。
- scoped 样式 + data 属主 class（避免浮层 teleport 后样式丢失）。
- 全局禁用 emoji 图标；如需图标，预留 `icon` 插槽。
- 文案统一通过 `services/i18n/zh-CN.ts` 的 `t()` 读取（含 `ui.*`、`errors.*`、`validation.*` 命名空间扩展）。
- 错误映射：错误码 → `errors.{code}` 文案，未知回退 `errors.unknown` 并附 `code + trace_id`。

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| WS/SSE 在 Vite dev middleware 与 HMR 共存时连接抖动 | 实时演示断流 | 使用 `upgrade` 事件处理 WS，heartbeat 30s + 指数退避；演示页显式提供"重连"按钮 |
| 浮层组件在 Teleport 后 scoped 样式失效 | 弹层样式丢失 | 采用 `data-` 属主 class + `:global` 仅作用于 attribute selector；演示页用所有弹层组件做端到端验证 |
| Fixtures 时序漂移导致"事件先于 REST 返回" | 体验卡顿 | 节点解释器在 `Emit` 前插入 `Wait` 节点最小 50ms；REST 响应在脚本就绪后才暴露 |
| M0 周期内浮层 + 表单组件同时落地，回归面大 | 单 PR 改动爆炸 | 按 A/B/C/D 切 4 段分支（feature/frontend-ui-* 与 feature/frontend-mock-*），分阶段合入 dev |
| `tsconfig` strict + 组件 props 强类型 props 易踩 unknown 边界 | 类型反复 | 统一 `defineProps<{...}>()` 显式声明 + `withDefaults`；禁止 `defineProps<any>` 兜底 |
| 浮层组件与 base.css reset 互相覆盖 | 样式串扰 | 浮层统一 Teleport 到 `body` 下，加 `[data-floating]` 属主 scope；base.css 复位边界只作用文档根 |
| Mock 网关性能/内存 | 长跑 dev 卡顿 | fixtures 单 run 时长 ≤ 90s；run 完成后 30s 回收内存对象 |

---

## 8. 交付物与文件结构

新增/修改清单（相对 M0 已提交状态）：

```
frontend/
├── src/components/ui/
│   ├── form/{UiInput,UiTextarea,UiCheckbox,UiRadio,UiSwitch,UiSlider}.vue
│   ├── feedback/{UiBadge,UiTag,UiSkeleton,UiEmpty,UiErrorState,UiToast}.vue
│   ├── overlay/{UiDialog,UiDrawer,UiPopover,UiDropdown,UiTooltip}.vue
│   ├── nav/UiTabs.vue
│   ├── focus-trap.ts
│   └── index.ts                                # 聚合导出
├── src/views/dev/
│   ├── UiShowcaseView.vue                      # 静态组件演示
│   └── RunDemoView.vue                         # mock 网关端到端演示
├── src/services/realtime/
│   ├── RealtimeClient.ts
│   └── envelope.ts                             # 与 types/realtime.ts 共用类型
├── src/services/mock/
│   ├── gateway.ts                              # Vite 插件入口
│   ├── router.ts
│   ├── realtime.ts
│   ├── sse.ts
│   ├── store.ts
│   ├── seed.ts
│   ├── script/{runner,envelope,types}.ts
│   └── fixtures/{happy_path,demo_full}.ts
├── src/composables/runs/useRunStream.ts        # 最小骨架
└── vite.config.ts                              # 挂载 mock 插件
.github/workflows/frontend-ci.yml               # lint/typecheck/build
```

依赖增量（仅 devDependencies）：

- `ws` ^8（mock 网关 WS 服务）
- `@types/ws` ^8

runtime 不引入新依赖，符合 §3.1 "组件自研、少而稳"。

---

## 9. 验收标准

### 9.1 组件维度

- 13 个 M0 组件全部实现，props/事件符合 §6.1 表。
- 演示页 `/dev/ui-showcase` 覆盖每个组件的 default + 全部 variant + size + disabled + error 五种状态。
- typecheck/build 通过；ESLint 0 警告（沿用 M0 既有门禁）。

### 9.2 Mock 网关维度

- 端点表（§3.2）20 个端点 100% 实现且与契约字段一致。
- WS 频道：握手 + heartbeat + 事件路由（与 `types/realtime.ts` 联合类型一致）。
- SSE 流：`report.chunk` 按 Δ 推送，前端能在演示页观察到流式渲染。
- 至少 2 个 fixtures 全部可重放。

### 9.3 端到端维度

- `pnpm dev` 后 `/dev/run-demo` 路径一键跑通：
  - happy_path：发起 run → 6 阶段推进 → 报告流 → 完成（成本实时刷新）。
  - demo_full：发起 run → 阶段澄清弹窗 → 确认/裁决 → 冲突裁决 → 报告流 → 完成。
- 触发 401 / 404 / 422 至少各 1 次（脚本化故障注入），前端 toast/页面 error state 反馈正确。

### 9.4 工程维度

- 全量 typecheck/build 通过。
- 全量 ESLint 通过（沿用 `pnpm lint --max-warnings 0`）。
- 文档：本方案文档按 §9.4 提交随代码演进原则，存于 `docs/design/`，不绑定某次 PR。

---

## 10. 后续衔接

- M1：基于本方案落地，向导/指挥舱/报告三页面接入 mock 网关与 fixtures 跑通最小闭环 demo（PRD A1）。
- M2：RealtimeClient 全量实现，fixtures 增加 `intent_routing` / `cost_throttle` 等剧本。
- M3：UI Showcase 升级为可视化 Storybook（若团队决议）；fixtures 加入失败恢复剧本。
- 后端 M1-1 真实契约落地后，前端切到 OpenAPI 生成客户端（§3.1），mock 网关退化为 dev fallback。

---

## 修订记录

| 版本 | 日期 | 变更项 |
|---|---|---|
| v1.0 | 2026-09-10 | 首次发布，覆盖 M0 剩余：13 个基础组件 + mock 网关 + 研究模拟器 + 2 份 fixtures + 最小 RealtimeClient 骨架 + CI 门禁 |