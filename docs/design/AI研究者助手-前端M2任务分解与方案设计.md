# AI 研究者助手 · 前端 M2 任务分解与方案设计

> 版本: v1.1 · 起草日期: 2026-09-12 · 评审通过: 2026-09-12 · 适用范围: 前端 M2（能力补齐与工程化）
> v1.1（2026-09-13）：跟随 SDP v1.1 §5.2 勘误，阶段 F 共同回归集补入 A5/A12，A 编号定义以 PRD §11 为唯一事实源
> 上游依据: 《软件开发计划》§3 M2、《前端详细设计》§7/§9/§10/§11.1-11.4/§12.6/§13/§16/§17、《后端详细设计》v2.0 §4.2/§15.2、《后端契约草案》v0.1 §4/§6、PRD §11 验收清单
> 目标: 把 M1 单页 demo 升级为"可被真实用户试用的最小完整产品"的前端形态：单入口意图路由、实时透明看板、结构化可溯源报告、最小用户介入闭环。

---

## 1. 背景与现状

### 1.1 M1 已就绪基线（2026-09-12 双方回归关闭，dev `502e32b`）

- 页面：Login、AppShell、项目列表、项目任务列表、五步向导（M1 裁剪版）、指挥舱（六阶段最简时间线）、报告只读页（marked + DOMPurify 渲染 markdown）。
- 服务层：手写 M1 冻结契约客户端（auth/projects/runs/reports 共 11 个 REST 端点）、HttpClient（幂等键 + 401 单飞刷新）、RealtimeClient（WS 心跳/10s 握手超时/指数退避重连）、useRunStream（六阶段映射 + live 补帧 + 4s 兜底轮询）。
- Mock 网关：Vite 插件，REST + WS 同端口，happy_path 单一剧本；`VITE_MOCK=gateway/off` 一键切换。
- 工程：Vue3.5 + Vite5.4 + TS5.6 strict、18 个自研基础组件、Pinia 4 store、ESLint 零警告门禁、GitHub Actions 远端绿灯。
- 联调新增韧性基线：connecting/retrying 双态提示、paused 澄清挂起正确呈现（不信任事件名、信任 payload.status）。

### 1.2 M2 缺口（对照《前端详细设计》§17 M2 行）

| 缺口 | 现状 | M2 目标 |
|---|---|---|
| 意图路由单入口 | HomeView 占位卡片；向导必须手动进入 | 首页单输入框 → `/intent/classify` → chat/research/uncertain 分流 + 确认层 + 撤回 |
| 闲聊会话区 | AssistantView 占位 | 独立闲聊面板，与研究项目数据完全隔离（A14） |
| 实时看板 | 仅六阶段时间线 + token 快照 | 子问题进度、证据流（元数据/剔除）、冲突提示、实时成本卡（≤3s） |
| 用户介入 | 仅 paused 只读引导 | 澄清回答 resume、暂停/继续、追加追问、剔除证据（统一受控通道） |
| 报告溯源 | markdown 整页渲染，引用为普通链接 | blocks 结构化引擎 + 论断角标点击回溯 + 四类区块 + 局限汇总（A2/A3） |
| 实时客户端 | 3 事件、页面级销毁 | 全量事件路由、lastEventId 去重、跨页频道引用计数、指令/ACK |
| 韧性 | M1 最小实现 | 反馈单 9.3 三项缺口（网络错误误判登出、错误文案归一、自动重试） |
| 埋点 | telemetry 模块空壳 | A8 关键指标可采集（溯源打开率、看板介入率等） |

### 1.3 关键事实与约束

1. **后端 M2 尚未开发**：`feature/backend-m2-intent-router` 为空分支；M2 段 OpenAPI 冻结契约（openapi-m2）不存在。前端延续 M0/M1 成功模式——**mock 先行、契约类型手写、真实联调按后端交付批次切换**。
2. **M2 后端交付批次**（《后端详细设计》§15.2；编号口径以 SDP §3 为唯一事实源）：M2-1 意图路由 → M2-4 看板数据接口 → M2-5 用户介入 → M2-3 实时成本 → M2-2 批判收敛/分歧 API → M2-6 信源元数据抽取 / M2-7 结构化报告与数据点级溯源 → M2-9 OpenAPI 导出。前端工作包按此顺序安排联调对接点。
3. **不超范围硬约束**：裁决面板 VerdictPanel/分歧工作台（M3/M4）、子问题增删排序（M3）、成本升级档位操作（M4）、报告批注/@/导出/分享（M4）、命令面板/模板库（M3）一律不在 M2 实现。冲突在 M2 只"呈现与提示"，不提供裁决操作入口。
4. WS 鉴权 M2 计划由 query token 切换为 `Sec-WebSocket-Protocol: bearer, <jwt>` 子协议（协议名 `bearer`，契约草案 §5.3 定版）；前端传输层做成可配置，**与后端同一切换，不保留双兜底**。

---

## 2. 目标与非目标

### 2.1 目标（M2 必交付）

1. HomeView 单入口意图分流三态（chat/research/uncertain）+ 超时降级 + 确认层 + 强制旁路；research 预选模板/档位透传向导。
2. AssistantView 闲聊会话最小版，数据不落项目；首页/确认层可"改回闲聊"。
3. 指挥舱升级为实时看板：子问题进度、证据流（含 M2-6 信源元数据展示）、冲突红点提示、成本卡（70%/90% 阈值，≤3s）。
4. HITL 最小闭环：澄清卡回答 → resume；暂停/继续；追加追问；剔除证据（REST + WS 指令双通道，ACK/Toast/幂等）。
5. 报告页结构化 v1：blocks 四类型渲染、CitationMarker 角标 → SourcePanel 原文回溯、LimitationSummary、ConflictBlock 行内呈现。
6. RealtimeClient/useRunStream 全量化：M2 事件全集路由、event_id 幂等去重、跨页频道引用计数与延迟关闭、重连快照补齐。
7. Mock 网关 M2 扩展（端点 + 事件 + demo 剧本），前端全程可独立开发验收。
8. 反馈单 9.3 三项韧性缺口闭环；A8 埋点最小集可出数。
9. 全部产出通过 lint/typecheck/build 门禁与真实后端分批联调。

### 2.2 非目标（M2 不做）

- 分歧裁决表单（VerdictPanel）、分歧工作台页、打回重审、标存疑重生成。
- 子问题计划的编辑/排序/替换（契约预留 M3 打开）。
- 报告批注/@、Word/PDF 导出、只读分享链接、报告筛选"只看已交叉验证"（M2 仅保留"只看分歧"）。
- 成本升级/降级操作面板、预算预警设置；M2 成本卡只读展示（对应 SDP M2-5"仅内部"口径）。
- 命令面板、模板库、深色模式新增功能（机制已有，不做新 QA 范围）。
- 闲聊多会话历史树、会话管理（M2 仅当前会话消息流；历史持久化待后端契约定稿）。
- OpenAPI 自动生成客户端切换（依赖后端 M2-9；M2 保持手写类型，预留生成目录与替换清单）。

---

## 3. 工作包分解（WP-8 ~ WP-18，依赖排序）

> 命名承接 M1 的 WP-1~7。每个 WP 单独分支 `feature/frontend-m2-*`，独立可验收后 squash 合入 dev。
> 阶段 A/B/C/D 可在 mock 上并行程度高；阶段 E 为横切，建议 A 之后随各业务包落地。

### 阶段 A：契约地基与 Mock 扩展（不依赖后端）

#### WP-8 M2 契约类型与 API 客户端

- 手写 TS 类型（snake → camel 映射沿用 M1 口径）：`IntentClassifyResult`、Stage/SubQuestion/Conflict 列表项、Evidence（含 source_type/source_level/credibility/published_at/domain）、CostSnapshot、结构化 Report（outline/blocks/claims/citations）、HumanInput/resume/intervene 请求体、TelemetryBatch。
- 新增 `services/api/`：intent.ts、dashboard.ts（stages/sub-questions/evidence 分页/conflicts/cost snapshot）、interventions.ts（pause/resume/cancel/intervene）、保留 reports.ts 扩展 citations。
- 枚举联合类型集中到 `types/domain.ts`（《前端详细设计》§10.7 全量枚举），i18n 补中文映射与 Badge 语义色。
- 验收：typecheck 通过；与《后端详细设计》§4.2 字段逐一对齐并在文件头注释标注出处；M2-9 后生成客户端的替换清单以 README 小节沉淀。

#### WP-9 RealtimeClient 全量化与 useRunStream 状态扩展

- 事件联合类型扩展为 §9.2 全集：stage.finished/failed、sub_question.created/started/finished、evidence.fetched、interrupt.requested、conflict.detected、token.usage.update、cost.warning、report.finished。
- WsChannel：event_id 去重集合（每 run FIFO 2000）、频道引用计数（多页面订阅共享、归零延迟 5s 断开）、指令发送 + `intervene.ack/intervene.error` 的 request_id 关联（§9.5）。
- useRunStream 状态扩为 §7.3 完整模型：subQuestions/evidence/conflicts/cost；rAF 批量合并证据增量；重连补齐 = GET run + GET cost/snapshot + 关键帧重拉；模块级 LRU 缓存保留。
- WS 鉴权传输层抽象：query/子协议两种注入方式可配，默认值随后端契约切换（mock 与真实同口径）。
- 验收：mock 下断连/重连/去重/指令 ACK 四类脚本场景通过；M1 既有六阶段行为不回退。

#### WP-10 Mock 网关 M2 扩展与 demo 剧本

- 新增 REST：`POST /intent/classify`（按问题文本启发式返回三态，支持脚本指定）、`GET /runs/{id}/stages|sub-questions|evidence|conflicts|cost/snapshot`、`POST pause/resume/intervene/cancel`、`GET /reports/{id}/citations`、`POST /telemetry/batch`。
- WS 新增 M2 全部事件广播；evidence 按 500ms 节流长发；token 按 1s 推送；cost.warning 在 70%/90% 触发。
- 新增 fixture `demo_full`（M0 方案原定被 M1 裁剪，M2 补回）：澄清挂起→resume 续跑→子问题并行→证据流（含一条可剔除）→冲突 detected→成本预警→结构化报告。
- 新增结构化报告样例（blocks ≥ 12 个、四类型齐全、claims/citations ≥ 10 条、含 1 个 dispute block、信源含 A/B/C/D 分级）。
- 验收：curl + 浏览器演示路径走通 demo_full 全部分支；happy_path 保持通过。

### 阶段 B：意图路由与闲聊（对接后端 M2-1）

#### WP-11 HomeView 单入口与意图分流

- 三列布局按 §11.1（左欢迎/快捷入口、中进行中研究卡片、右小贴士；推荐模板位 M3 前隐藏）。
- 意图状态机：输入 → classify（2s 超时；骨架"识别中"微态不阻塞）→ chat 跳 /assistant；research 展开确认层（模板/档位预选，可改、可"改回闲聊"、确认跳 `/wizard?template_id=&tier=`）；uncertain 默认 research + 显式不确定提示 + 撤回；接口失败/超时保守降级 research 并说明。
- 强制旁路：空输入占位与输入区菜单提供"强制闲聊/强制研究"。
- 进行中研究卡片：读活跃 run（数据源见 §6 风险 R3），仅订阅状态帧，点击进指挥舱。
- 向导衔接：WizardView 接收 query 预选，step1/step2 显示"已预选，可修改"；M2 不新增向导步骤（预读确认 step4 的成本预估依赖契约 §6.6 可空字段，无值时显示"启动后在看板查看"）。
- 验收：mock 三态 + 超时 + 强制旁路全覆盖；A11/A12/A13 交互可演示（召回率指标归后端评估，前端只保证分流与撤回正确）。

#### WP-12 AssistantView 闲聊面板

- 单列对话流 + 底部输入；消息气泡、发送中/失败态、失败重发；明确"不提供溯源组件、不产生项目数据"。
- 传输适配：契约形态待后端 M2-1 明确（普通 HTTP 或独立 SSE），先在 services 层定义 `ChatClient` 接口，mock 以 SSE 流式返回，真实对接时只换实现。
- 导航隔离：不经项目路由、不写 project/run store；会话态仅组件内存（刷新即清空，与"历史持久化待契约"一致）。
- 验收：从首页 chat 分流与"改回闲聊"均可进入；面板内无任何项目数据入口（A14）。

### 阶段 C：实时看板与用户介入（对接后端 M2-2/3/4/5）

#### WP-13 看板数据编排（composables 层）

- `useRunStream` 接入 WP-9 全量态：子问题进度派生、证据增量合并 key 稳定、冲突列表、cost 实时值；新增 `useEvidenceList`（分页 + 全文懒加载）。
- 重连补齐与轮询策略梳理：M1 的 4s 全量轮询在 M2 细化为"通道 retrying 时才轮询 run 快照 + live 后补帧"，避免高频重复拉取。
- 验收：demo_full 下各列表与事件时序一致；断网恢复后无重复行、无丢帧。

#### WP-14 CockpitView 实时看板实装

- 双列布局（§11.3）：左 StageTimeline + SubQuestionPlan（只读，含 m/n 与 evidence_short 态）+ 冲突提示列表（红点 + 行内 ConflictBlock 摘要，点击只展开详情，不提供裁决）；右当前阶段描述 + 证据流 + CostMeter。
- 业务组件（§10.1 M2 项）：EvidenceCard（紧凑行/hover 元数据/点击懒加载全文）、SourceBadge（域名/类型/可信分级色编码）、CostMeter（used/budget 进度条，70% 提示/90% danger，断连"数据可能滞后"降级文案）、ConflictBlock。
- 顶栏：暂停（模态确认）；取消按钮 M2 是否开放以后端 M2-5 能力为准，能力未交付则不预造入口。
- 验收：看板刷新用户视角 ≤3s（事件到渲染）；阈值变色、冲突提示、证据元数据展示在 demo_full 全可见。

#### WP-15 HITL 介入闭环

- ClarificationCard：按 `interrupt.requested.payload.questions[]` 渲染 2-4 题（选项 + recommended 默认 + expires_in 超时提示），提交 `POST /runs/{id}/resume`（HumanInput.clarifications）；超时只读标注"已按默认假设"。
- 暂停/继续：`pause` + `resume`；暂停超 15 分钟"确认再续"弹窗。
- 追加追问：阶段3 行内输入 → `intervene {ask_followup}`；剔除证据：证据行 ✕ → `intervene {exclude_evidence}`，本地置灰隐藏 + 可恢复列。
- 统一 InterventionDrawer（复用 M0 RightDrawer 单例）、WS 指令与 REST 双通道等价、request_id/Idempotency-Key 幂等、成功 Toast、`INTERVENE_NOT_ALLOWED` 错误码文案。
- 验收：demo_full 四类介入全部成功/失败路径可见；双击不重复提交；M1 paused 只读引导不回退。

### 阶段 D：结构化报告与数据点溯源（对接后端 M2-7；M2-6 信源元数据在阶段 C 证据流消费）

#### WP-16 报告 blocks 引擎与溯源交互

- 数据层 `useReportBlocks`：GET reports 详情 + citations，构建 block/claim/evidence 双向索引（§10.2）；终稿渲染**只允许**结构化模型，角标禁止硬编码。
- 渲染器四类型：conclusion（正文 + 行内 CitationMarker 组 + confidence 中文标签：单一来源/多源印证/推断）、evidence（可折叠原文片段）、dispute（ConflictBlock 内联警告 + 双方立场，"查看分歧详情"M2 仅弹只读抽屉）、limitation。
- CitationMarker 三态（§10.3）：hover 200ms Popover 迷你 SourceBadge；click 右侧 SourcePanel 抽屉定位原文；键盘 Tab/Enter/方向键可达。
- LimitationSummary 报告末尾汇总；ReportChrome 工具条 M2 仅返回 + "只看分歧"筛选（灰化未命中 block，不删除）。
- 双轨切换：生成中保留 M1 markdown 预览（流式光标、100ms flush）；真链 M2-7 不发 report.finished 帧，run.finished(status=succeeded) 后拉取终稿超集，按 blocks 是否非空切 blocks（mock demo_full 保留 report.finished 帧仅模拟用）；M2 不实现 SSE report.chunk（M1 契约已移除 SSE，生成中以 run 状态 + 轮询/WS 为准，若后端 M2 恢复报告流再按 §9.2 挂接，列入对接确认）。
- 验收：样例报告 ≥10 个论断角标全部可点击回溯到 URL + 原文片段（A2 抽样口径）；四类区块完整（A3）；推断结论不以事实呈现；首帧 ≤2s（文本先出、角标懒挂）。

### 阶段 E：横切韧性与可观测

#### WP-17 联调韧性三件套（反馈单 9.3）

1. session 恢复区分网络错误与 401：refresh 请求网络层失败（Failed to fetch/5xx/超时）保留会话并提示"无法连接服务器"，仅 401/403 清会话跳登录。
2. HttpClient 错误归一：补 `Failed to fetch` → ApiError(network)；500 默认技术文案映射为用户可读中文（i18n errors.* 扩充），trace_id 可展开。
3. 错误态退避自动重试：UiErrorState 支持自动重试模式（1/2/4s，上限 3 次后转手动），先接入项目列表/看板/报告三个高频页。
- 验收：后端停服时不再静默登出；三类故障均有中文提示且能自动或手动恢复。

#### WP-18 Telemetry 埋点最小集（A8）

- 复用 M0 telemetry 模块接 `POST /telemetry/batch`（批量 + 离线上报队列）；M2 事件：`report.citation.open`、`cockpit.intervene`（区分 pause/resume/followup/exclude/clarify）、`intent.classify`（intent/耗时/降级原因）、`report.view`。
- 埋点只走 services 层，不含 token/问题原文等敏感内容（props 仅记录 id 与分类型字段）。
- 验收：mock 端点可收齐四类事件并出简单计数；真实端点未就绪时队列不报错、不阻塞页面。

### 阶段 F：联调与门禁（贯穿）

- 每个 WP 自带 mock 验收；后端契约分批冻结后按 WP-11→13/14→15→14(cost)→16 顺序真实联调，每个对接点产出联调记录（沿用 M1 反馈单机制）。
- M2-9 OpenAPI 导出就绪后，评估切换生成客户端（独立 WP，不在本方案编号内，约 0.5-1 人日）。
- 远端 CI 全程保持绿灯；M2 结束时双方对照 SDP M2 六条验收准则与 §5.2 归属 M2 的 A2/A3/A5/A8/A11/A12/A13/A14 共同回归（A12 召回率由后端离线评估举证，前端承担其余 7 项的交互/展示/埋点侧验证）。

---

## 4. 依赖关系与里程碑节奏建议

```text
WP-8 契约类型 ──┬─ WP-9 实时全量化 ── WP-13 看板编排 ── WP-14 看板实装 ── WP-15 HITL
               └─ WP-10 Mock 扩展 ─┘
WP-8 ── WP-11 首页意图 ── WP-12 闲聊面板
WP-8 ── WP-16 报告 blocks（可与 B/C 并行，依赖 WP-10 样例）
WP-17 韧性、WP-18 埋点：WP-8 后随时插入，建议随 WP-11/WP-14/WP-16 同批走
```

- 真实联调阻塞点：WP-11/12 等 M2-1 冻结；WP-14 等 M2-3/4（M2-6 信源元数据随 evidence 字段补齐在本包核对，不另设对接点）；WP-15 等 M2-5；WP-16 等 M2-7 报告 schema 与 citations 冻结。阻塞期间全部在 mock 上推进，不停工。
- 建议合入顺序：A（8→9→10）→ B（11→12）→ C（13→14→15）→ D（16）→ E（17/18 分散合入），每个 WP 一次评审。

## 5. 文件结构（新增/改造预估）

```text
frontend/src/
├── services/
│   ├── api/ intent.ts dashboard.ts interventions.ts（新）；reports.ts/types.ts（改）
│   ├── realtime/ realtime.ts types.ts（全量事件/ACK/引用计数）
│   ├── chat/ chat.ts（新，ChatClient 接口 + 传输适配）
│   └── mock/ router.ts realtime.ts store.ts（改）；fixtures/demo_full.ts（新）
├── composables/
│   ├── useRunStream.ts（全量态）、useEvidenceList.ts（新）
│   ├── useIntent.ts、useChat.ts、useReportBlocks.ts（新）
├── components/business/（新目录，§10 业务组件）
│   ├── StageTimeline/ SubQuestionPlan/ EvidenceCard/ SourceBadge/ CostMeter/
│   ├── ConflictBlock/ ClarificationCard/ InterventionDrawer/
│   ├── CitationMarker/ SourcePanel/ LimitationSummary/ ReportChrome/
├── views/
│   ├── home/HomeView.vue（实装）、assistant/AssistantView.vue（实装）
│   cockpit/CockpitView.vue（双列看板）、report/ReportView.vue（blocks 引擎）
│   wizard/WizardView.vue（query 预选衔接）
└── types/domain.ts（新，枚举联合集中）
```

> M1 的指挥舱时间线目前内联在 CockpitView，WP-14 时抽为 components/business/StageTimeline，属于搬移不改写；业务组件不直接 import services，经 composables/actions 透传（§10.1 约束）。

## 6. 风险与对策

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | 后端 M2 契约未冻结，字段可能漂移 | 返工 | mock-first；类型文件标注契约出处；每批后端冻结后做一次字段对齐评审，差异走联调反馈单不私下兼容 |
| R2 | 闲聊传输形态（HTTP/SSE/独立通道）未定 | WP-12 阻塞 | services 层定 ChatClient 接口隔离；M2-1 评审会必须确认，列为前端给后端的首要对接问题 |
| R3 | 首页"进行中研究"需要 run 列表端点，M1 仅有单 run 查询与 localStorage 索引 | WP-11 活跃卡片无数据源 | 请后端在 M2-4 明确 `GET /runs?status=` 或团队/项目维度列表端点；未交付前活跃卡片沿用本地索引并标注"仅本设备" |
| R4 | 高频事件导致渲染压力 | 看板卡顿 | 服务端节流（500ms/1s）+ 前端 rAF 合并 + 行级 key 更新；证据全文懒加载，不进 WS payload |
| R5 | 后端 M2 若不恢复 report.chunk 流，"边生成边读"体验缺口 | 预期差异 | M2 以 run 状态驱动"生成中"态 + finished 切 blocks；不自行伪造流式；写入联调确认项 |
| R6 | WS 鉴权切换子协议时机不一致 | 实时全断 | 传输层单开关配置；与后端约定同一分支窗口切换；mock 网关同步实现子协议握手 |
| R7 | 结构化报告强约束依赖后端真实产出 blocks | A2/A3 无法真实验收 | WP-16 先用样例 fixture 开发；联调以 M2-7 冻结 schema 为准做字段核对，markdown 渲染仅保留在生成中预览 |
| R8 | M2 范围膨胀（裁决/导出等被提前塞入） | 周期失控 | §2.2 非目标清单入评审检查项；M3/M4 入口一律不预埋按钮，仅代码结构可预留 |

## 7. 验收标准（M2 结束时）

**功能（对照 SDP M2 六条 + PRD）**

1. 首页输入闲聊类问题 → 跳助手面板直答，不创建 run；研究类 → 确认层 → 向导预填 → 六阶段流水线；不确定默认研究且可撤回；断网/超时降级有提示（A11/A12/A13/A14 交互侧）。
2. 看板实时显示当前阶段、子问题 m/n、已采集证据源数量与元数据、token 用量（事件到渲染 ≤3s）；70%/90% 成本预警可见（A5）。
3. 澄清/暂停继续/追问/剔除四类介入操作经受控通道生效并有 ACK/Toast；幂等不重复。
4. 终稿报告四类区块完整；抽样 ≥10 个论断角标可点击回溯到原始 URL 与片段；不可调和分歧在报告内显式呈现（A2/A3）。
5. 后端停服不静默登出；网络故障有中文提示与自动/手动恢复路径。
6. 埋点四类事件可批量采集出数（A8 前端侧）。

**工程**

- ESLint 零警告、vue-tsc strict、生产构建全通过；CI 绿灯。
- mock 网关 happy_path 与 demo_full 两剧本均可完整重放。
- M1 全部既有页面与交互无回退（回归清单沿用 M1 关单口径）。

## 8. 待评审/与后端确认清单

1. 闲聊传输形态（R2）：普通 HTTP 还是 SSE 流？是否复用 `/intent/classify` 后的会话端点？
2. `GET /runs` 列表/查询端点（R3）是否进入 M2-4，字段是否含项目维度与活跃状态过滤。
3. WS 子协议鉴权（R6）计划在哪个后端工作包切换，前端同步窗口。
4. M2 是否恢复 report.chunk 类报告流事件（R5），还是终稿一次性拉 blocks。
5. `/intent/classify` 响应是否在 M2 即带 `estimated_token_budget/estimated_cost`（契约草案 §6.6 可空字段），决定向导预读确认步是否显示估算。
6. `interrupt.requested` 澄清超时（expires_in_seconds）到期后服务端是否推状态事件，前端只做只读标注还是需要事件驱动关闭卡片。
7. 冲突在 M2 的呈现深度：前端按"只读呈现+提示"实现，裁决入口留 M3，请确认与后端 M2-2 verdict API 已就绪但无 UI 的状态不冲突。

---

## 修订记录

| 版本 | 日期 | 变更项 |
|---|---|---|
| v0.1 | 2026-09-12 | 草案：M2 前端现状盘点、WP-8~18 任务分解、mock-first 联调节奏、风险与待确认清单 |
| v1.0 | 2026-09-12 | 评审通过，作为前端 M2 开发基线；修订文件结构树字符 |
| v1.1 | 2026-09-13 | 跟随 SDP v1.1 §5.2 验收项映射勘误：阶段 F 共同回归集由 A2/A3/A8/A11/A13/A14 更正为 A2/A3/A5/A8/A11/A12/A13/A14（A12 由后端评估举证），§7 第 2 条补标 A5；不改变 WP 范围 |
