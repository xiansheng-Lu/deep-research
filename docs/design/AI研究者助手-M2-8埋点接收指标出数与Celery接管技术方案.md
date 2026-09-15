# AI 研究者助手 · M2-8 埋点接收/指标出数与 Celery 接管长任务阶段技术方案

- 版本：v1.0 实现冻结（2026-09-15；M2-8a 门禁证据见 §13.3，M2-8b 门禁证据见 §23.3，a/b 两段均已落地）
- 范围：
  - **M2-8a 埋点接收与指标出数**：前端 Telemetry 批量接收端点（`POST /telemetry/batch`，WP-18 已在发数、当前 404 静默失败）、`telemetry_events` 表与 0006 迁移、A8 三指标聚合出数端点（`GET /telemetry/metrics`）。
  - **M2-8b Celery 接管长任务 + 跨进程事件 + 孤儿 run 清扫**：研究/恢复执行从 API 进程内 `asyncio.create_task` 迁出到独立 worker；Redis 承载跨进程 RealtimeHub 扇出与在途 run 租约/控制信号；启动期孤儿 run 清扫（收敛 M2-5 挂账的无协程 pause 409）。
- 上游依据：PRD §11 验收 A8、§7 数据与指标（埋点区分指标与采集）；《软件开发计划》§3 M2 硬指标「一次研究全流程有完整审计日志可回放；溯源可回溯率、看板介入率、报告生成成功率埋点可出数」、§5.2「A8←埋点最小集（前端 WP-18/后端 M2-8）」；《后端详细设计》§15.2「M2-8 审计与决策留档后端（A8，配合前端 telemetry）/Celery 接管长任务」、§3.2「workers：Celery 实例 + 3 任务占位 → 任务与 orchestrator 接线」、§3.3「workers 复用 orchestrator，不得引用 api」；《后端契约草案》§14 Telemetry；前端详设 §16.1/WP-18；registry.py 模块注释「跨进程恢复/多 worker、注册租约 + 消息信号在 M2-8 替换」；HANDOFF §6 下一步「Celery 接管长任务、孤儿 run 清扫（M2-5 pause 对无协程孤儿 409 的治理挂在此）」
- 编号口径：M2-8a 与 M2-8b 同属 SDP §3 的 M2-8 一个里程碑；a 先行（前端已在等待、零部署变更），b 随后（引入 Redis/worker 部署与跨进程机制），各自独立可回归、独立交接，不改变里程碑编号口径
- 契约冻结基线：openapi-m2-7（25 路径）。a 段产出 openapi-m2-8（27 路径，+2 telemetry 端点）；b 段零新增 REST/WS 契约路径（执行位置对前端透明），不产新快照

## 1. 目标与范围

### 1.0 两段总览

本里程碑按依赖与风险分两段，共用同一方案文档与 0006 之后的迁移序列，但各自独立 T 序列、独立门禁、独立交接：

- **M2-8a（埋点接收与指标出数）**：无部署/架构变更，前端已在等待，先行闭环；
- **M2-8b（Celery 接管长任务 + 跨进程事件 + 孤儿清扫）**：引入独立 worker 进程与 Redis 控制/事件面，改造运行时调度，在 a 段完成后启动。

### 1.1 M2-8a 目标与范围

前端 WP-18 已实装本地队列批量上报（localStorage 持久化、5 条阈值、10s 兜底、keepalive 补发、失败静默），但 `POST /api/v1/telemetry/batch` 在 openapi-m2-7 与实时服务中均不存在，事件全部丢弃。同时 A8 要求的三项目标指标目前只能手工查库、无统一口径与出口。8a 交付：

1. **批量接收端点**：按《后端契约草案》§14 冻结规格实现 `POST /api/v1/telemetry/batch`——登录用户、扁平标量 props 隐私白名单、批/条两级校验、限流与稳定采样、过载返 204 不阻塞。
2. **事件落库**：新增 `telemetry_events` 追加表（0006 迁移），服务端注入 user_id/team_id 与入库时间，不更新不删除。
3. **指标出数端点**：`GET /api/v1/telemetry/metrics` 按统一时间窗口径聚合 A8 三指标（报告生成成功率、看板介入率、溯源可回溯率）与意图降级率，返回标量 JSON 供内部试用观察。
4. **openapi-m2-8 累积快照**（27 路径）；WS 零变化；前端运行代码零改动。

### 1.2 M2-8a 非目标（明确不做）

- **审计列表 API（契约草案 §13）**：M6 管理员能力；指标端点只出聚合标量，不提供事件明细查询/导出。
- **商业指标（激活/留存/租户研究量/付费转化）与按租户维度下钻**：PRD §7 商业指标属 V1+ 运营阶段；8a 指标为全局口径。
- **前端新增/改造埋点事件**：WP-18 现有 3 个事件即全部接入面；服务端不做事件名白名单（允许前端后续扩展事件零后端发版），但对事件结构与 props 严格校验。
- **分布式限流、事件去重（30s 高频折叠）、OLAP 数仓/物化视图**：内部试用单副本 + 小数据量，8a 用进程内限流与在线聚合足够；**8b 引入 Redis 后限流计数迁到 Redis**（见 §14），去重按契约 §14 仍不做。
- **在埋点通道做业务校验或业务反写**：run_id 不做归属查询、不据埋点事件改任何业务状态。
- **历史数据回填**：telemetry_events 从上线起收集，不补算丢弃期事件。

### 1.3 M2-8b 目标与范围

当前研究执行是 API 进程内 `asyncio.create_task(run_research_async / resume_research_async)`，RealtimeHub 是进程内 asyncio 队列，RunRegistry 是进程内协程句柄单例。这在单 uvicorn 副本下工作，但任务生命周期绑定 Web 进程：部署重启/崩溃即丢失在途驱动（checkpoint 在 PG 但无人续跑）、无法横向扩副本（WS 事件与控制信号跨进程不通）、产生 M2-5 挂账的 running 孤儿。8b 交付：

1. **独立 worker 执行**：研究首跑与 HITL 恢复改由常驻 worker 进程执行（Celery 任务外壳承载现有 asyncio executor）；API 进程只投递任务、不再直接驱动图。
2. **跨进程实时事件**：RealtimeHub 增加 Redis Pub/Sub 后端——worker publish 的事件经 Redis 扇出到所有 API 副本的 WS 连接；API 副本本地订阅按现有 channel 语义投递，前端 WS 协议零变化。
3. **跨进程在途控制**：RunRegistry 的「协程句柄 + task.cancel()」替换为 **Redis 在途租约 + 控制信号键**：pause/resume/cancel 端点在 API 进程写 DB 状态（M2-5 乐观翻转不变）+ Redis 控制键；worker 轮询/订阅信号在超步边界协作停止，恢复 M2-5 的 ask_followup 受理窗口时序保证（debug task 节点开阶段即开窗）。
4. **孤儿 run 清扫**：worker 启动（与可选周期）时，把「DB=running 但无有效租约且非挂起」的 run 收敛为终态：能从 checkpoint 判定挂起的转 paused 待人工恢复，其余按失败终态处理并补发终态帧/成本帧；收敛后 M2-5 的「running 但无协程」pause 409 不再发生（孤儿被清扫而非长期滞留）。
5. **部署与接线**：docker-compose.dev.yml 的 Redis 已在（7-alpine）；新增 worker 进程启动方式（pyproject 已有 `deep-research-worker` 入口）、联调手册 worker/保活说明；联调最小栈是否纳入 Redis 给出明确结论。

### 1.4 M2-8b 非目标（明确不做）

- **多 worker 水平扩展下的同 run 并发执行（分布式锁抢占调度）**：8b 保证「单 run 同一时刻仅一个 worker 驱动」（租约互斥），但不做任务在多个 worker 间的负载均衡编排与 work-stealing；worker 副本数按内部试用固定（建议 1 个 research worker + 可扩 API 副本）。
- **替换 LangGraph/PostgresSaver checkpoint 机制**：HITL 恢复仍以 PG checkpoint 为唯一事实源；8b 只换「谁来驱动」与「信号怎么跨进程」。
- **WS 鉴权子协议切换、WS 心跳重设计**：维持 query token 现状（契约 §5.3 切换另议）；Redis Hub 只换后端传输。
- **Celery beat 周期业务任务（定时报告/数据清理管线）、report/ingestion 队列实装**：8b 只接通 research（+resume）执行；celery_app 里 report/ingestion 占位任务保持占位，不借本包实现 M4/M5 能力。孤儿清扫用 worker 启动钩子（+ 可选低频 beat 仅扫 run，评审项），不引入完整 beat 编排。
- **结果回写走 Celery result backend**：终态仍以 DB run/report 行为准（前端轮询/WS 不读 Celery result）；Redis result backend 仅用于任务可观测，不作为业务状态。
- **优雅下线之外的任务抢占迁移（draining 后在另一 worker 续跑半成品超步）**：8b 依赖超步边界落库 + checkpoint，worker 被 kill 时在途超步由重启孤儿清扫/恢复处理，不做超步中途热迁移。

## 2. 现状盘点（M2-7 关闭基线）

### 2.1 已具备（复用，不重复造）

| 能力 | 位置 | 现状 |
| --- | --- | --- |
| 前端上报侧 | `frontend/src/services/telemetry/telemetry.ts`、`api/telemetry.ts` | WP-18 已实装：统一 schema `{event, ts, run_id?, page?, props?}`、本地队列/批量/keepalive；一批上限 5 条（远低于服务端 200 上限），**等待真实端点** |
| 前端实有事件 | useIntent.ts / CockpitView.vue / useReportBlocks.ts | ① `intent.classify`（无 run_id：intent/source/degraded/duration_ms/degrade_reason）② `cockpit.intervene`（run_id：action∈pause/resume/followup/exclude/clarify、result、error_code）③ `report.citation.open`（run_id：evidence_id）。props 均为标量分类字段，无自由文本 |
| 端点契约 | 《后端契约草案》§14 | 路径/204/批≤200 条≤64KB/event 正则/ts ±7 天/422 `TELEMETRY_BATCH_INVALID`/429 `TELEMETRY_RATE_LIMITED`/登录用户/采样限流已定版 |
| 介入事实表 | `run_interventions`（M2-5） | ask_followup/exclude_evidence，status applied/rejected；介入率分子数据源 |
| 操作审计表 | `audit_entries` + AuditLogger | pause/resume（RUN_PAUSE/RUN_RESUME）、intervene.* 已落库；介入率分子数据源 |
| run 终态 | `research_runs.status` | pending/running/paused/succeeded/failed/cancelled + started_at/finished_at；成功率分母数据源 |
| 溯源审计 | `reports.content_json.citation_audit`（M2-7） | final 报告含 `numeric_claim_binding_rate`（恒等式保证 1.0）；溯源率机器口径数据源 |
| 鉴权/错误体系 | `app/api/deps.py` CurrentUser、`core/exceptions.py` | 登录依赖与 AppError 体系可直接挂新错误码 |
| 路由聚合 | `app/api/v1/__init__.py` | 加一个 telemetry 子路由即可；audit 仍为占位（M6） |
| Celery 骨架（8b） | `app/workers/celery_app.py`、`workers/tasks/research.py` 占位 | Celery 工厂已配 broker/backend（`celery[redis]`/`redis[hiredis]` 在依赖锁）、`task_acks_late/prefetch=1/reject_on_worker_lost` 已设、`deep-research-worker` 入口已在 pyproject；research 任务仅返回占位 dict |
| 执行器（8b） | `orchestrator/executor.py` | `run_research_async`/`resume_research_async` 全 asyncio（astream 驱动）、`_drive_to_terminal`/`_StopHolder` 已把「取消协作」抽离为 DB 状态轮询；checkpoint PostgresSaver 已落 PG |
| 在途控制（8b） | `orchestrator/registry.py` | 进程内 RunRegistry（register/reserve/request_stop=task.cancel）；模块注释明确「跨进程恢复/多 worker、注册租约 + 消息信号在 M2-8 替换」 |
| 事件总线（8b） | `realtime/hub.py` | 进程内 RealtimeHub（dict[channel, list[Queue]]）；模块注释「跨进程广播通过 Redis Pub/Sub」 |
| Redis（8b） | `docker-compose.dev.yml` | redis:7-alpine 已在开发栈（6379）；**docker-compose.min.yml 当前不含 Redis**（注释明言不经过 Celery），8b 联调栈需补 |
| 调度入口（8b） | `api/v1/runs.py` L154/L274 | 首跑与恢复均为请求处理器内 `asyncio.create_task(...)`；恢复走 runs_control.reserve + 条件 UPDATE 后 create_task |
| 孤儿现状（8b） | `services/runs_control.py` L70-73 | running 但 registry 无在途句柄时 pause 直接 409 RUN_NOT_PAUSABLE，注释挂账「孤儿治理在 M2-8」 |

### 2.2 缺口（本包要补）

**8a：**

1. 无接收端点：实时服务与冻结契约均无 `/telemetry/batch`，前端事件全丢（真链 console warn 已取证）。
2. 无事件表：`telemetry_events` 不存在；埋点与业务审计不同源（audit_entries 是服务端动作流水，前端交互观测无处落）。
3. 无统一指标出口：A8 三指标只能手工拼 SQL、口径分散（成功率/介入率分母无定义、溯源率只有 M2-7 机器口径）。
4. 无限流设施：app 无 slowapi/限流中间件；8a 先补进程内最小限流器（不引第三方依赖）。

**8b：**

5. 任务与 Web 进程同生命周期：uvicorn 重启/崩溃后在途 run 失去驱动（checkpoint 在但不续跑），形成 running/paused 孤儿。
6. 进程内 Hub/Registry 无法跨副本：worker 进程发出的 WS 事件到不了持有连接的 API 进程；pause/cancel 在 API 进程取不到 worker 的协程句柄，`task.cancel()` 失效。
7. Celery 同步任务模型与全 asyncio executor 不匹配：占位 research 任务是同步 `def`，直接 await 异步执行器需要在 worker 内自持事件循环（§14.2 裁决外壳 vs 纯 asyncio worker）。
8. 无租约/清扫机制：DB 无「run 由哪个执行者持有、心跳到何时」的字段，无法判定活任务与孤儿。

## 3. M2-8a 组件与代码落位

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| 迁移 | `app/db/migrations/versions/0006_telemetry_events.py`（新增） | telemetry_events 追加表；alembic head 0006；空库直接建 |
| ORM | `app/db/models/telemetry.py`（新增）+ `models/__init__.py` 注册 | TelemetryEvent（不可变追加） |
| API schema | `app/schemas/telemetry.py`（新增） | TelemetryEventIn/TelemetryBatchRequest/TelemetryMetricsResponse |
| 接收服务 | `app/services/telemetry.py`（新增） | 批/条两级校验、采样判定、批量落库；指标窗口聚合 |
| 限流 | `app/telemetry_ingest/__init__.py` + `rate_limit.py`（新增） | 进程内滑动窗口每用户批次数限流（单副本语义，8b 换 Redis） |
| 路由 | `app/api/v1/telemetry.py`（新增）+ `api/v1/__init__.py` 注册 | POST /telemetry/batch（204）、GET /telemetry/metrics |
| 错误码 | `app/core/exceptions.py`（改造） | TelemetryBatchInvalidError(422/telemetry_batch_invalid)、TelemetryRateLimitedError(429/telemetry_rate_limited) |
| 配置 | `app/core/config.py`、`.env.example` | TELEMETRY_SAMPLE_RATE=1.0、TELEMETRY_RATE_LIMIT_PER_MIN=12；批尺寸为代码常量不开放配置 |
| 契约 | `app/export_openapi.py` + `docs/contract/openapi-m2-8.json` | `_M28_ENDPOINTS` 两路径与描述常量，M2-7 转默认冻结（--refresh-m27），27 路径 |
| 测试 | `tests/test_telemetry_api.py`（新增）、`tests/test_telemetry_ratelimit.py`（新增）、`tests/test_integration_m28.py`（新增真库）、test_models 更新 | AC 见 §9 |
| 文档 | 本方案 v1.0、backend/README 回填、`docs/feedback/M2-8a埋点接收接口交接.md` | 落位遵循《项目人员协调操作规约》 |

## 4. M2-8a 任务分解与实现顺序（T1~T4）

### T1 表模型、0006 迁移与 schema

TelemetryEvent 模型与 0006 迁移；请求/响应 Pydantic 模型与常量（200 条/64KB/正则/±7 天/props 白名单）；test_models 表清单更新。

### T2 接收端点（校验/采样/限流/落库）

`app/telemetry_ingest/rate_limit.py` 进程内滑动窗口；service 两级校验 + user_id/team_id 注入 + 稳定采样 + add_all 单事务落库；POST 路由 204/422/429；离线假会话测试覆盖全分支。

### T3 指标聚合服务与 GET 端点

时间窗解析（默认近 30 天、上限 90 天）；runs/interventions/audit/telemetry_events 聚合 + reports content_json 窗口内取数计算三指标；GET 路由与响应 schema；口径单测。

### T4 真库集成、契约冻结与门禁

`tests/test_integration_m28.py`（m28_orm ORM 落库/索引/读回聚合、m28_migration 0006 往返）；export_openapi 27 路径并与前端 types.ts 的 TelemetryEvent/TelemetryBatchRequest 核对；全量 pytest、ruff、mypy；方案升 v1.0、README、交接单。

## 5. M2-8a 关键设计

### 5.1 表结构（0006）

```text
telemetry_events
  id            String(26) PK
  user_id       String(26) NOT NULL          -- 服务端从 CurrentUser 注入，不信任请求
  team_id       String(26) NOT NULL          -- 同上
  event         String(128) NOT NULL         -- 命中 ^[az0-9_.]{1,128}$
  run_id        String(64) NULL              -- 客户端透传，仅格式校验不查归属
  page          String(128) NULL
  props         JSONB NOT NULL DEFAULT '{}'  -- 扁平标量白名单后的键值
  event_ts      DateTime(tz) NOT NULL        -- 客户端 ts(epoch ms) 转换
  created_at    DateTime(tz) NOT NULL         -- 服务端入库时间（索引）
索引：ix_telemetry_created_at(created_at)
      ix_telemetry_event_time(event, created_at)
      ix_telemetry_run(run_id)
      ix_telemetry_user_time(user_id, created_at)
```

- 追加表：只 INSERT，应用层不 UPDATE/DELETE（与 audit_entries 同代的不可变观测表）。
- 0001 无此表，0006 纯新增无回填；downgrade drop table。
- `run_id` 长度按 64（兼容 ULID 与历史 run 形态）；不建外键——埋点是观测旁路通道，缺外键避免业务行删除/异常 id 阻塞入库。
- props 体积随 64KB 批上限受控，单行不另设截断（键 20 个、字符串 256 字封顶，单行上限约 5KB）。

### 5.2 接收端点两级校验（契约 §14）

**批级（整批 422 `telemetry_batch_invalid`，前端拆批/丢弃）**：

1. `events` 必须是非空数组，条数 1~200；
2. 序列化 JSON 字节数 ≤ 64KB（Pydantic 校验后按请求 raw body 字节长度二次确认）；
3. 单条缺 `event`/`ts` 或类型错误（event 非字符串、ts 非数值）→ 批级 422（前端 schema 已保证，属不可恢复脏数据）。

**条级（非法条丢弃、合法条照收、仍返 204）**：

1. `ts` 早于 now-7 天或晚于 now+7 天 → 丢条（前端断网补发可能过期）；
2. event 不匹配 `^[a-z0-9_.]{1,128}$` → 丢条；
3. `page` 非字符串或 >128 字 → 丢条；`run_id` 非字符串或 >64 字 → 丢条；
4. props 非对象 / 键数 >20 / 键不匹配 `^[a-z0-9_]{1,64}$` / 值非 string|int|float|bool / 字符串值 >256 字 → 丢条；布尔/数值不做值域裁剪（NaN/Inf 由 JSON 解析层拒绝）；
5. 全部条目非法时返回 204 + accepted=0（不 422：批结构合法即可，避免坏队列反复重试整批）。

服务端记录丢弃计数日志（event/原因），供 §5.5 出数与排查；不向前端回传逐条错误（204 无体，契约定版）。

### 5.3 采样与限流（三层保护）

1. **稳定采样**：`TELEMETRY_SAMPLE_RATE`（默认 1.0，范围 0~1）。命中不采样时整批 204 丢弃；采样判定按 `hash(user_id)` 落在稳定区间（同一用户在配置不变时始终采/不采，避免数据破碎），不用每请求随机。
2. **批频率限流**：`TELEMETRY_RATE_LIMIT_PER_MIN`（默认 12）。进程内按 user_id 的 60s 滑动窗口（deque 时间戳），超限 429 `telemetry_rate_limited`。前端正常节奏（5 条/批 + 10s 兜底 ≈ 6 批/分钟上限）不触发；恶意/异常循环上报被挡。进程内实现与 RealtimeHub 内存形态同代，多副本部署不精确——内部试用单 uvicorn 副本可接受，M2-8b 引入 Redis 后换共享计数，本方案明确该技术债边界。
3. **批体积**：200 条 + 64KB 硬上限在采样前先判（§5.2），保证任何单请求工作量有界。

顺序：批级 422 校验 → 限流 429 → 采样（不采样静默 204）→ 条级清洗 → 批量落库。鉴权在最前（未登录 401，由依赖保证）。

### 5.4 隐私与安全边界

- props 仅扁平标量（§5.2），**无自由文本通道**：问题原文、澄清答案、追问内容、token 一律进不来；WP-18 现有三事件的 props 设计已遵守（action/枚举/id/耗时/成败）。
- user_id/team_id 只从 CurrentUser 注入，请求体携带也忽略；事件归属服务端说了算。
- run_id 不做归属校验：埋点通道不因此发起业务查询（保延迟与可用性），伪造 id 只影响交互观测、无任何业务写路径；三指标中业务事实（成功率/介入率/机器溯源率）全部取自业务表，不采信 telemetry 表的 run_id，唯一使用 run_id 的交互指标（§5.5）可与业务表内连接去伪。
- 指标端点只出聚合标量，不返回任何 user_id/team_id/事件明细。

### 5.5 指标口径（GET /telemetry/metrics，A8 出口）

查询参数 `from`/`to`（ISO8601，可空；默认 now-30 天~now，窗口最大 90 天，越界 422）。全部口径服务 PRD §7 明确决策，返回结构：

```jsonc
{
  "window": { "from": "...", "to": "..." },
  "runs": {
    "total_terminal": 42,          // succeeded+failed+cancelled（进入过 running：started_at not null）
    "succeeded": 36, "failed": 2, "cancelled": 4,
    "success_rate": 0.947          // succeeded/(succeeded+failed)，cancelled 不入分母
  },
  "intervention": {
    "runs_started": 50,            // 窗口内 started_at not null 的 run（介入率分母）
    "runs_with_intervention": 11,
    "intervention_rate": 0.22,
    "by_action": { "pause": 3, "resume": 1, "followup": 2, "exclude": 4, "clarify": 1 }
  },
  "traceability": {
    "final_reports": 36,
    "avg_numeric_binding_rate": 1.0,      // 机器口径：final 报告 citation_audit 均值
    "runs_with_citation_open": 30,
    "citation_open_rate": 0.833,          // 交互口径：有 ≥1 次 report.citation.open 的 run / final_reports
    "citation_open_events": 214
  },
  "intent": { "classify_total": 130, "degraded": 5, "degraded_rate": 0.038 },
  "ingest": { "accepted_events": 1520, "dropped_events": 3, "rate_limited_batches": 0 }
}
```

口径定义（评审重点，避免后续各说各话）：

1. **报告生成成功率**：窗口按 `research_runs.created_at` 落窗；分母=终态中的 succeeded+failed（系统对结果负全责的 run），cancelled（用户意图终止）、paused（仍在挂起）、pending/running 不计；率 = succeeded/(succeeded+failed)，无终态样本时返回 null（不返回 0 误导）。
2. **看板介入率**：分母=窗口内 started_at 非空的 run（用户真正看到看板的 run）；分子=有任一介入动作的 distinct run——`run_interventions.status='applied'`（followup/exclude）∪ `audit_entries.action ∈ {run.pause, run.resume, intervene.ask_followup, intervene.exclude_evidence}`（AuditAction 实际枚举值）按 run 目标去重。`by_action` 五键分类计数口径（两套来源，在响应中不做隐式合并）：
   - `pause` ← audit_entries `run.pause` 计数；`resume` ← `run.resume` 计数；
   - `followup`/`exclude` ← run_interventions 按 type、status='applied' 计数；
   - `clarify`：澄清答案提交（前端 submitAnswers）复用 resume 通道，服务端只落 `run.resume`，业务表无法与普通续跑拆分；该键计数取自 telemetry_events 的 `cockpit.intervene` 且 `props.action='clarify'`、`result='success'` 事件数（观测口径，可能因采样/丢弃略低于实际），响应字段注释须标明 `clarify` 来自前端事件、其余四键来自业务表。分子去重仍只信业务表，clarify 事件不参与「是否介入」判定（避免采样影响权威分子）。
3. **溯源可回溯率（双口径并列）**：
   - 机器口径：窗口内 status=final 报告的 `content_json->citation_audit->>numeric_claim_binding_rate` 平均值（Python 侧取窗口 final 行计算，内部试用量小；该值由 M2-7 恒等式保证为 1.0，本指标用于监控退化而非验收弹性）；
   - 交互口径（A8「可采集」的前端面）：窗口内有 ≥1 条 `report.citation.open` 的 distinct run_id（与 reports 内连接去伪）/ final 报告 run 数；总点击数另列。两口径都不替代 PRD 北极星的人工抽检（那是 M3+ 长周期动作）。
4. **意图降级率**：telemetry_events 中 `intent.classify` 事件 `props.degraded=true` 占比（前端观测口径，含超时/请求故障/服务降级，与后端离线评估集互补，不用于 A11/A12 验收）。
5. **ingest 自检**：accepted/dropped/限流批数，dropped 与限流来自服务端计数（进程内累计或日志粗估；精确持久化计数不做，避免为观测自增写压力，窗口内值允许重启归零——该字段仅趋势观察，在响应 schema 注释与交接单写明）。

### 5.6 权限与多租户

- POST：任何登录用户（CurrentUser）。
- GET metrics：**本包裁决为登录即可读全局聚合标量**（内部试用环境用户均为内部成员；响应无明细、无租户/用户维度，不泄漏业务内容）。租户/角色级管控（仅 owner/admin、按 team 过滤）随 M6 审计/运营能力一起做，本包不引入角色体系（评审拍板项，见 §12）。

### 5.7 与 WS/主链路的关系

- WS 零变化：埋点纯 REST 旁路，不发任何实时帧，不接入编排。
- 不阻塞主链路指前端语义；后端接收链路独立事务、异常不外溢到研究链路：接收端点自身异常返回 4xx/5xx，前端静默丢弃该批，不影响 run。

## 6. M2-8a 契约影响（openapi-m2-8，27 路径）

新增 2 路径（均在累积白名单 `_M28_ENDPOINTS`）：

| 方法/路径 | 说明 | 主要错误 |
| --- | --- | --- |
| `POST /api/v1/telemetry/batch` | 批量接收前端埋点；204 无体；采样丢弃/全条非法仍 204 | 401 未登录；422 telemetry_batch_invalid（批级）；429 telemetry_rate_limited |
| `GET /api/v1/telemetry/metrics?from=&to=` | A8 指标窗口聚合 | 401；422 时间窗越界 |

请求/响应与前端冻结形态核对：`TelemetryBatchRequest{events: TelemetryEvent[]}`、`TelemetryEvent{event,ts,run_id?,page?,props?}` 与 `frontend/src/services/api/types.ts` L461-471 同构（props 标量联合含 undefined→缺省）；后端响应无 body（204），前端 `postTelemetry` 已按 `void` 处理。错误码命名采用契约草案 §14 的 snake_case 形态（telemetry_batch_invalid/telemetry_rate_limited），前端 catch 静默不依赖 code 值。

WS 帧：无增删改。M2-7 快照转默认冻结（`--refresh-m27`）。

## 7. M2-8a 数据模型与迁移（0006）

- 新增 `telemetry_events` 表（结构 §5.1）与四索引；0001 基线无此表，无数据回填。
- downgrade：drop 四索引 + drop table（追加表无业务依赖，回退安全）。
- 不改动任何既有表；alembic head = **0006**。
- m24/m25 历史迁移循环用例已在 M2-7 钉死自身版本；m28 新增自身 upgrade/downgrade 循环用例，不改历史用例。

## 8. M2-8a 与 LLD / 契约草案的偏离点

1. **M2-8 拆 a/b 两包**：LLD §15.2 把 Celery 接管与埋点同列 M2-8；本包按风险与就绪度拆分，8b（Celery/孤儿清扫/跨进程 Hub）另立方案，SDP 里程碑编号不变。实现批原子在本方案与 README 标注。
2. **指标端点为本包新增（契约草案未列 GET）**：契约 §14 只定义接收端点，但 A8 验收要求「可出数」，需要可机读出口；新增 GET /telemetry/metrics（聚合标量），不触碰 M6 的明细审计列表 §13。
3. **成功率分母排除 cancelled/paused**：SDP 无逐字分母定义；按「系统责任」口径分母只取 succeeded+failed，cancelled 单独计数展示，避免用户取消拉低系统成功率。
4. **溯源率双口径**：M2-7 的 numeric_claim_binding_rate 是机器恒等式（恒 1.0），单报该值无法反映「用户是否真能回溯」；增加 report.citation.open 交互口径并列。PRD §2.3 北极星的人工抽检不在本包。
5. **进程内限流而非 Redis**：契约 §14「服务端做采样与限流」未定实现；本包以最小进程内滑动窗口落地，明确多副本技术债随 M2-8b 清偿，不提前引入 Redis 依赖。
6. **GET metrics 权限裁决为登录可读**：契约 §13 审计明细是 M6 管理员能力；聚合标量不含明细/租户维度，内部试用阶段不建角色体系。评审若认为应收紧，改为仅 owner 角色不影响其余设计。

## 9. M2-8a 测试与门禁

离线假替身（假会话/不打网络）；真库用例独立 schema（前缀 `m28_`），setup/teardown 各一次 DROP SCHEMA CASCADE。

| 编号 | 判定要点 |
| --- | --- |
| AC-1 | 合法批：登录用户 POST 204；telemetry_events 落库，user_id/team_id 由服务端注入（请求伪造被忽略），event_ts 由 ms 正确转换，props JSONB 往返 |
| AC-2 | 批级 422：events 非数组/空/201 条/raw body>64KB/缺 event 或 ts/类型错误 → telemetry_batch_invalid |
| AC-3 | 条级容错：ts 超 ±7 天、event 非法字符、page 超长、props 嵌套对象/非白名单值/键超限/字符串超长 → 该条丢弃其余照收，204；全非法仍 204 accepted=0 |
| AC-4 | props 隐私白名单：字符串/数值/布尔通过，dict/list/null 值拒绝；自由文本无法进入（三现有事件 props 形态全部通过） |
| AC-5 | 限流：同一用户超过 12 批/60s → 429 telemetry_rate_limited；窗口滑过后恢复；不同用户互不影响 |
| AC-6 | 采样：rate=0 全丢 204 不入库；rate=1 全收；同一 user_id 判定稳定（多次结果一致）；rate=0.5 时判定仅依赖 user_id 可复现 |
| AC-7 | 未登录 401；run_id 伪造/缺失均不影响 204 落库（不查归属） |
| AC-8 | metrics 成功率：造 succeeded/failed/cancelled/paused run，口径为 s/(s+f)、cancelled 排除、无样本字段 null；时间窗 created_at 过滤正确 |
| AC-9 | metrics 介入率：run_interventions applied 与 audit_entries 动作按 run 去重合并出权威分子，分母为 started run；by_action 中 pause/resume 取审计、followup/exclude 取 interventions、clarify 取 telemetry 事件（来源分离），各计数正确 |
| AC-10 | metrics 溯源率：final 报告 content_json 均值 + report.citation.open 交互口径（distinct run 与 final reports 内连）双口径正确；intent degraded 率正确 |
| AC-11 | 时间窗：默认近 30 天、from/to 边界包含、窗口>90 天/from>to → 422；ingest 自检测字段存在 |
| AC-12 | 真库 m28_orm：批量落库（含重复批幂等不报错——追加表不去重）/四索引存在/独立会话聚合读回；m28_migration：upgrade head=0006 表索引存在、downgrade -1 回 0005 消失、upgrade 恢复；跑后 schema DROP 无残留 |
| AC-13 | 全量 pytest 零回退；本批 ruff check/format 全净；mypy 相对 dev 基线零新增；openapi 27 路径且与前端 telemetry 类型字段差异 0 |

## 10. M2-8a 配置项变更

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TELEMETRY_SAMPLE_RATE` | 1.0 | 稳定采样率（0~1，按 user_id 哈希）；0 等价关闭接收落库（端点仍 204） |
| `TELEMETRY_RATE_LIMIT_PER_MIN` | 12 | 每用户每分钟批量请求上限，429 |

批尺寸（200/64KB）、时间窗（±7 天/默认 30 天/最大 90 天）、props 白名单（20 键/256 字）固化为代码常量，不开放环境配置。

## 11. M2-8a 风险与对策

| 风险 | 对策 |
| --- | --- |
| 埋点风暴打爆接收端 | 批 200/64KB 硬上限 + 每用户 12 批/分限流 + 稳定采样三层；单请求工作量恒定；落库单事务 add_all |
| props 混入敏感自由文本 | 类型白名单（仅标量）+ 键名正则 + 字符串 256 封顶；测试断言嵌套/长文被拒；代码评审禁止白名单放宽为 object |
| 伪造 run_id 污染指标 | 业务指标全部只信业务表；唯一用 run_id 的交互指标与 reports 内连接去伪；埋点通道零业务写 |
| 限流多副本不准 | 内部试用单副本；进程内实现与内存 Hub 同代；M2-8b 上 Redis 时替换为共享窗口，方案已挂账 |
| 成功率口径争议（取消算不算失败） | §5.5 明确分母只取 succeeded+failed、cancelled 单独展示；评审确认后冻结，交接单写明 |
| content_json 在线聚合性能 | 窗口内 final 报告 Python 侧计算，内部试用 ≤90 天量级可控；M6 运营阶段再评估物化，不提前优化 |
| 前端坏队列因 422 反复重试 | 批级 422 仅限结构不可恢复场景；条级问题一律 204 丢弃，坏条不卡死队列；前端现有失败策略为整批保留，结构 422 理论不可达（schema 双保险） |
| 采样率变更造成曲线断层 | 采样仅按 user_id 稳定切流，变更影响在响应中无可回看校正（V1 不做加权还原）；变更需在交接/运维记录注明，属可接受的运营动作 |

## 12. M2-8a 评审拍板项

1. **GET /telemetry/metrics 权限**：本方案裁决「登录即可读全局聚合标量」（无明细/无租户维度）；若评审要求收紧，改为 owner 角色或简单内部门禁，不影响 T1~T3 主体。
2. **成功率分母口径**：succeeded/(succeeded+failed)，cancelled/paused 排除（§5.5）。
3. **8a/8b 同里程碑分两段交付**：a 先行（无部署变更、前端在等），b 随后（Redis/worker 跨进程改造）；8b 不晚于 M3 启动前完成，避免长任务 asyncio 形态长期固化。
4. **事件名不设服务端白名单**：允许前端扩展事件零后端发版，结构校验兜底；若评审要求白名单（只收三事件），改为配置化集合，评审时定。

## 13. M2-8a 实现冻结记录（v1.0，2026-09-15）

### 13.1 落地清单（对照 §3 落位表）

| 组件 | 文件 | 结论 |
| --- | --- | --- |
| 迁移 | `0006_telemetry_events.py`（新增） | telemetry_events + 四索引（created_at/event_time/run/user_time），server_default '{}'，downgrade 往返；head=0006 |
| ORM | `app/db/models/telemetry.py`（新增）+ `__init__.py`/test_models 注册 | TelemetryEvent 追加表（无 updated_at），props Mapped[dict[str, Any]] |
| API schema | `app/schemas/telemetry.py`（新增） | 入站 TelemetryEventIn（仅 event:str/ts:int 为批级强约束，run_id/page/props 为 Any 交条级清洗）、TelemetryBatchRequest(1~200)、metrics 五组响应模型；全部常量固化 |
| 限流/采样 | `app/telemetry_ingest/__init__.py`（新增） | SlidingWindowRateLimiter（60s 滑窗）、stable_sample（blake2b user_id 稳定切流）、IngestCounters 进程计数 |
| 接收/指标服务 | `app/services/telemetry.py`（新增） | ingest_batch（采样+条级清洗+归属注入+add_all）、check_rate_limit、parse_window、get_metrics（成功率/介入率/双口径溯源率/意图降级率） |
| 路由 | `app/api/v1/telemetry.py`（新增）+ `__init__.py` 注册 | POST /telemetry/batch（204/422/429，64KB raw body 二次校验）、GET /telemetry/metrics（窗口 422 走 validation_error） |
| 错误码 | `app/core/exceptions.py` | TelemetryBatchInvalidError(422)、TelemetryRateLimitedError(429) |
| 配置 | config.py/.env.example | TELEMETRY_SAMPLE_RATE=1.0、TELEMETRY_RATE_LIMIT_PER_MIN=12 |
| 契约 | export_openapi.py + openapi-m2-8.json | `_M28_ENDPOINTS` 2 路径，M2-7 转默认冻结（--refresh-m27），导出 27 路径 |
| 测试 | test_telemetry_ratelimit（4）、test_telemetry_api（11）、test_integration_m28（4 真库+纯函数）、m27 迁移用例钉 0005、test_models 清单更新 | AC-1~AC-13 覆盖 |

### 13.2 与评审稿的实现偏差

1. **入站 schema 放宽到 Any 由 service 逐行清洗**：评审稿原拟 Pydantic 直接约束 props 标量/字段长度，但嵌套 dict、超长字符串会在请求体解析阶段触发整批 422，与「条级非法丢条、整批仍 204」（AC-3）矛盾。实现为 TelemetryEventIn 只强约束 event:string、ts:int（不可恢复结构错误才整批 422），其余类型/长度/正则/时间窗/props 白名单全部在 service `_clean_event` 逐行判定。
2. **空批/超条数的 422 为 FastAPI 默认请求体错误形态（`detail`）**：仅 64KB 超限走 telemetry_batch_invalid 自定义码；两类都是 HTTP 422，前端 WP-18 静默策略不依赖 body。
3. **介入率分母含 running run**：方案 §5.5 文字为「started_at 非空」，running 同样满足（用户已看到看板），实现按 5 个 started 计；测试期望已据实修正为 2/5=0.4。
4. **审计/介入聚合查询 select 实体而非多列标量**：与全仓 scalars() 约定一致，Python 侧取属性。
5. **响应附加 `X-Accepted-Events` 头**：204 无体，实际接受条数放调试响应头（前端 fire-and-forget 不依赖，契约无破坏）。
6. **test_models 顺手清掉两处既有告警**：触碰该文件后 ruff 纳入检查，删除未用的 `inspect` 导入与未用 `indexes` 变量（不改断言语义）。

### 13.3 门禁证据

- 全量 `pytest -q`：**655 passed**（基线 636 +19：限流/采样 4、接收 API 11、m28 真库 4），含全部真库集成零跳过。
- 真库 m28：m28_orm 造 4 终态+1 running run、exclude/pause 介入、2 final(1.0/0.5)、9 条埋点（含 r-unknown 去伪），独立会话聚合断言成功率 2/3、介入率 2/5、by_action(pause=1/exclude=1/clarify=1)、溯源均值 0.75/交互率 1.0、意图降级 1/4；空窗口率字段全 null；m28_migration 0006 表+四索引 upgrade/downgrade/upgrade 往返。
- ruff：本批文件 check 全净、format 已应用；mypy 54 条与 dev 基线 54 条**零新增**（telemetry 模型 Mapped[dict[str,Any]] 补齐类型参数）。
- 契约：openapi-m2-8 **27 路径**（+batch/+metrics）；入站 TelemetryEventIn 的 JSON 字段（event/ts/run_id/page/props）与前端 types.ts TelemetryEvent 同构，metrics 为后端新增出站（前端 WP-18 只发不读）；M2-7 快照 25 路径零 diff 转冻结。
- 联调前置：alembic head 0005→**0006**（`alembic upgrade head`）；新配置有默认值，不设环境变量即可启动。

---

# M2-8b Celery 接管长任务 · 跨进程事件与孤儿清扫

> 以下 §14~§23 为 8b 段设计；8a 关闭后进入编码，独立迁移（0007）、独立门禁、独立交接（`M2-8b长任务接管接口交接.md`）。

## 14. 8b 总体架构与关键裁决

### 14.1 进程拓扑

```text
浏览器 ──WS──▶ API 进程（uvicorn，可多副本）
                 │ ① POST /runs、/resume：写 DB + 投递 Celery 任务（不驱动图）
                 │ ④ pause/cancel：M2-5 乐观翻转 DB + 写 Redis 控制键
                 ▼
            Redis（broker + Pub/Sub + 控制键 + 租约）
                 ▲ ② research.execute_run / research.resume_run 任务
                 │
            Worker 进程（常驻，单 research 队列，自持 asyncio 事件循环）
                 │ ③ 驱动 LangGraph executor；publish 事件 → Redis 频道
                 ▼
            Redis Pub/Sub ──▶ 各 API 副本订阅协程 ──▶ 本地 WS 连接（runs:{id}）
                 │
                 ▼
            PostgreSQL（run/stage/evidence/report/checkpoint 唯一事实源）
```

职责边界：

- **API 进程**：HTTP/WS、业务校验、DB 乐观状态翻转、任务投递、Redis 控制信号写入；**不再持有 run 协程**。
- **Worker 进程**：消费 research/resume 任务，在进程内 asyncio 事件循环中运行现有 executor；持有 Redis 租约、心跳、读取控制信号、发布事件。
- **Redis**：Celery broker/result、跨进程事件 Pub/Sub、在途租约键、pause/cancel 控制键。
- **PostgreSQL**：唯一业务事实源（含 checkpoint）；Redis 只做易失信号，重启可从 PG 重建。

### 14.2 关键裁决：Celery 作投递外壳，executor 仍为 asyncio

现有 `run_research_async`/`resume_research_async`、graph.astream、LLM/检索客户端、RealtimeHub 全部基于 asyncio。Celery 任务函数本身是同步 `def`，本方案裁决：

- **Celery 任务体只做「在 worker 进程内启动/复用一个 asyncio 事件循环并 `asyncio.run` 执行异步执行器」的薄外壳**（`asyncio.new_event_loop` + `run_until_complete`，或 worker 启动时建一个常驻 loop 并通过 `run_coroutine_threadsafe` 提交）。业务逻辑零改写。
- 不把 executor 重写为同步、不引入 gevent/eventlet 池（`worker_pool=prefork` 默认即可），避免 asyncio/gevent 双重事件循环与 httpx/asyncpg 的兼容风险。
- 队列：单一 `research` 队列（celery_app 已配 `-Q research,report,ingestion`，8b 只投递 research）；`task_acks_late=True`+`prefetch_multiplier=1`+`reject_on_worker_lost=True` 已就绪，保证 worker 崩溃时任务重回队列（配合 §16 租约幂等防重跑）。
- 评审备选：若评审认为 Celery 对「单队列长 asyncio 任务」是多余抽象，可退化为「systemd/supervisor 守护的纯 asyncio worker 脚本 + Redis 列表/流做队列」；但这会绕过已配好的 Celery 重试/可观测体系，本方案倾向保留 Celery 外壳（§22 拍板项）。

### 14.3 前端/契约透明性

执行位置迁移对前端完全透明：

- REST 路径、请求/响应、WS 帧名与载荷、错误码全部不变（M2-5 的 RUN_NOT_PAUSABLE 等口径保留，孤儿经清扫后该错误理论上只在极端竞态出现）。
- WS 连接仍由 API 进程承载；前端订阅 `runs:{id}` 不变。事件多一跳 Redis，内部试用延迟增量在毫秒级，不冲击 M2-3「看板刷新 ≤3 秒」。
- 无新增/删除 REST 路径，openapi 快照不因 8b 变化。

## 15. 8b 组件与代码落位

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| Redis 客户端 | `app/core/redis.py`（新增） | 异步 redis.asyncio 客户端工厂（`redis[hiredis]` 已在锁），API/worker 各自生命周期管理 |
| Redis Hub | `app/realtime/hub.py`（改造） | RealtimeHub 增双态：内存直连（无 Redis 配置时，测试/兼容）+ Redis Pub/Sub（worker publish、API subscribe 桥接到本地队列）；channel 语义 `runs:{id}` 不变 |
| 在途租约/信号 | `app/orchestrator/lease.py`（新增） | Redis 键封装：租约 `lease:run:{id}`（带 TTL/owner，SET NX PX + 心跳续期）、控制键 `control:run:{id}`（pause/cancel + keep_partial）；提供 try_acquire/renew/release/set_control/read_control/clear |
| 跨进程 Registry | `app/orchestrator/registry.py`（改造） | 保留类与方法签名，内部从协程句柄改为 Redis 租约/信号适配；`request_stop` 改为写控制键（API 进程），worker 侧 executor 经 lease.read_control 感知；reserve 占位语义用「DB 条件翻转 + 任务幂等」承接（§16.3） |
| executor 接线 | `app/orchestrator/executor.py`（改造） | 运行入口接收 lease 标识与控制读取回调；`_StopHolder`/协作取消从读 registry 改为读 Redis 控制键（超步边界轮询点已在 debug/values 流中）；驱动结束 release 租约 |
| Celery 任务 | `app/workers/tasks/research.py`（改造） | execute_run/resume_run 从占位改为同步外壳：建 session_factory/llm/retrieval/checkpointer/Redis Hub，asyncio 驱动 executor；按 Celery task_id 关联 run 便于排查 |
| Worker 引导 | `app/workers/bootstrap.py`（新增） | worker 进程版依赖装配（配置/DB engine/checkpointer/LLM/检索/Redis Hub），复用 lifespan 中纯函数（_build_llm_client 等），不 import app.api（§3.3 约束） |
| API 调度改造 | `app/api/v1/runs.py`、`app/services/runs_control.py`（改造） | 两处 `asyncio.create_task` 替换为 `execute_run.delay(...)`/`resume_run.delay(...)`；pause/cancel 增写 Redis 控制键 |
| 孤儿清扫 | `app/workers/reaper.py`（新增） | worker 启动钩子（+ 可选 beat 周期）扫描无有效租约的 running run，按 §17 收敛 |
| 迁移 | `app/db/migrations/versions/0007_run_execution_lease.py`（新增） | research_runs 增执行租约观测列（owner/lease_until，可空，仅可观测与清扫判定；权威租约在 Redis） |
| 配置 | config.py/.env.example | REDIS_URL（沿用 CELERY_BROKER_URL 可派生）、RUN_LEASE_TTL_SECONDS=30、RUN_LEASE_HEARTBEAT_SECONDS=10、RUN_ORPHAN_GRACE_SECONDS=60、WORKER_ENABLE_REDIS（默认 true） |
| 部署 | docker-compose.min.yml、docker-compose.dev.yml、README/ops 手册 | min 栈补 redis 与 worker 服务；WSL2 保活手册补 worker 常驻 |
| 测试 | tests/test_lease.py、test_workers_research.py、test_hub_redis.py、test_orphan_reaper.py、test_integration_m28b.py、test_integration_resume.py 更新 | AC 见 §19；Redis 用 fakeredis 或测试容器，CI 无 Redis 时 skip（与真库 skip 同策略） |

## 16. 8b 关键设计

### 16.1 Redis Pub/Sub RealtimeHub

- 频道命名不变：`runs:{run_id}`。worker 的 Hub.publish = 先 PUBLISH 到 Redis（不再依赖本地有订阅者）；每个 API 副本启动一个 Redis 订阅协程，PSUBSCRIBE `runs:*`，收到消息后投递到本地 RealtimeHub 的订阅者队列——WS 处理器代码零改动。
- 本地兜底：`WORKER_ENABLE_REDIS=false` 或未配 REDIS_URL 时退回纯内存 Hub（单进程测试/离线）。生产 worker 必须配 Redis，bootstrap 启动时强校验（缺失快速失败而非静默回退，避免事件静默丢失）。
- Pub/Sub 是 fire-and-forget，不保证投递；但既有阶段/证据事件本就以 DB（stage/evidence 落库）为权威、WS 为加速通道，前端有轮询/重连收敛（M2-4）。终态帧丢失风险通过：worker 收尾在发布后再释放租约 + API 端 run.finished 缺失时以 REST 轮询兜底（现状已有）。不为本包引入 Redis Stream 持久事件（非目标）。

### 16.2 在途租约（liveness 与互斥）

键 `lease:run:{run_id}` = `{worker_id, celery_task_id, started_at}`，TTL `RUN_LEASE_TTL_SECONDS`（默认 30s）：

- worker 驱动 run 前 `SET NX PX` 抢租约；抢不到说明已有执行者（重投递/重复任务），本次直接退出（不驱动图），靠 DB 状态判定是否需要发帧。
- 驱动期间每 `RUN_LEASE_HEARTBEAT_SECONDS`（10s）续期；超步 await 长于 TTL 的风险由心跳协程独立承担（心跳协程不被业务 await 阻塞）。
- 正常结束（含 pause/cancel 协作收尾）显式 DEL；worker 崩溃则 TTL 过期，孤儿清扫据此识别。
- DB 增 `execution_owner`/`lease_until` 两列（0007）作为**观测镜像**：worker 心跳同步写库（低频，随超步提交捎带，不额外高频写），供清扫 SQL 与人工排查；权威互斥仍在 Redis NX，DB 列不作为锁。

### 16.3 控制信号与 pause/resume/cancel 时序（不回退 M2-5 窗口）

- 键 `control:run:{id}` 值 `pause` 或 `cancel`（只允许升级不降级，同 registry.mode 语义），cancel 附带 keep_partial。
- API 端 pause：保留 M2-5「DB 条件翻转 running→paused 提交优先」，提交后 `SET control:run:{id}=pause`（取代原 registry.request_stop/task.cancel）。
- Worker 端：executor 消费 debug/values 事件的超步边界（M2-5 已建立的轮询点）在每个 super-step 前后 read_control：
  - 收到 pause：等价旧 `task.cancel()` 触发的协作收尾路径——在下一安全点落 paused（checkpoint 已在 PG）、发 stage/run.finished(paused)、释放租约；
  - 收到 cancel：走 `_finalize_termination` 的 cancelled 分支（keep_partial 草稿逻辑不变）。
  - ask_followup 的受理窗口：fan-out 节点在 retrieve 层边界消费 pending followup（M2-5 既有），控制信号不改变这一窗口；pause 只在超步安全点生效，窗口维持 M2-7 回归结论（debug task 节点开阶段即开窗），不得退回 values 单流。
- resume：API 端条件 UPDATE paused→running（M2-5 双击防护不变）后 `DEL control:run:{id}` 并投递 `resume_run` 任务；**reserve 占位被「DB 条件翻转 + Redis 租约 NX」替代**：即便恢复任务与旧首跑收尾短暂交叠，两者对 run 的驱动以租约 NX 互斥，DB 状态以条件 UPDATE 为准。
- 信号清理：run 进入终态/succeeded 收尾时 DEL 控制键；信号键也设兜底 TTL（如 1h）防泄漏。

### 16.4 Celery 任务幂等与崩溃语义

- execute_run/resume_run 必须可安全重投（acks_late 下 worker 崩溃任务会回队）：进入先抢租约；抢不到 + DB 已是 running 且租约新鲜 → 视为已有执行者，任务直接 ack 返回（ACK 成功，非错误）。
- 任务体捕获到非业务异常（LLM/检索基础设施故障）时：区分可重试（ProviderUnavailableError 等瞬时故障）抛出让 Celery 按有限次数重试（max_retries=2，避免无限重放烧 token），与终态失败（落 failed + 帧）分流；重试预算耗尽落 failed。
- token 烧穿防护：重试只在尚未产出终态报告前；重试入口依赖 checkpoint，不重复已完成超步的写操作（沿用 M2-4 中间提交幂等）。
- `reject_on_worker_lost` 保证硬 kill 时任务 requeue；崩溃在途超步由孤儿清扫/租约过期处理。

### 16.5 孤儿 run 清扫（reaper）

触发：worker 进程启动时执行一次；可选 Celery beat 低频任务（默认 600s，评审项，不引入完整 beat 业务编排）。

判定与动作（扫描 status=running 且 `lease_until < now - ORPHAN_GRACE_SECONDS(60s)`，排除刚启动未续约的正常任务）：

1. **查 PostgresSaver checkpoint**：存在未消费 interrupt（clarify/critique 挂起）→ 置 `paused`（等用户 resume），补发 run.finished(paused)（经 Hub，best-effort）；
2. 无挂起且 run 已过 report/无续跑可能（如崩溃发生在终态写前后）：
   - 若已有 final/draft 报告行 → 以报告为准收敛（succeeded 有 final；cancelled 有 partial draft），补齐 run 终态与终态帧；
   - 无报告 → 置 `failed`，error_code=`RUN_WORKER_LOST`，补 finished_at、发终态/成本帧；
3. pending 且从未 started（任务投递后 worker 即全灭）：启动清扫时重投一次 execute_run（租约 NX 保证不重）；超过重投上限（默认 1 次）置 failed。
- 清扫动作写 audit_entries（action 复用/新增 `run.orphan_recovered`，评审项：是否扩 AuditAction 枚举）。
- 收敛后，M2-5 的 running 无句柄 pause 409 路径在稳态下不再触发；pause_run 的 is_active 判定从「本地 registry」改为「Redis 租约是否新鲜」，新鲜则允许 pause（写控制键），不新鲜则说明是孤儿——不再直接 409，而是触发/等待清扫后返回 409 + 明确 code `RUN_ORPHAN_RECOVERING`（新增 details.code，前端提示「任务异常已重置，请刷新」，评审项）。

### 16.6 部署形态

- docker-compose.dev.yml：redis 已在；min.yml 补 redis（复用 7-alpine 镜像与健康检查）+ `worker` 服务（build 同一镜像，command `deep-research-worker`，依赖 redis+pg healthy，环境变量同源）。
- 内部试用：1 个 worker 副本（research 队列）、N 个 API 副本；同机/同 docker 网络。
- WSL2 联调：按 HANDOFF 既有保活模式，`wsl -d Ubuntu -e sleep infinity` 之外需保证 worker 进程常驻（手册补 `uvicorn` + `celery worker` 两个前台进程的启动方式；无 --reload 跑 worker）。
- 健康检查：worker 启动日志可确认注册队列；不新增健康端点（非目标）。

## 17. 8b 数据模型与迁移（0007）

`research_runs` 增两列（均可空，历史 run 无需回填）：

- `execution_owner String(64) NULL`：当前持约 worker 标识（观测用）；
- `lease_until DateTime(tz) NULL`：租约到期时间镜像（清扫扫描索引）；
- 索引 `ix_runs_status_lease (status, lease_until)`（部分扫描 running 孤儿）。

权威互斥/信号在 Redis（易失），DB 列是心跳镜像与清扫输入；worker 正常终态把两列置空。downgrade drop 列与索引。alembic head=**0007**。

## 18. 8b 与现状/LLD 的偏离点

1. **执行外壳用 Celery 同步任务 + worker 内 asyncio loop**，而非 LLD §6.5.7 暗示的节点直接在 worker 调用的纯同步形态：保留全 asyncio executor 零改写，Celery 仅投递/重试/可观测（§14.2）。
2. **reserve 占位机制被 DB 翻转 + Redis 租约 NX 取代**：M2-5 进程内 reserve/restore 的微窗口信号接力在跨进程下不成立，改为以 DB 状态为抢占权威、租约互斥防并发驱动、控制键 TTL 防丢信号；M2-5 的乐观翻转与窗口边界结论全部保留。
3. **Hub 双态（内存/Redis）**：未直接切纯 Redis，保留无 Redis 回退以维持离线测试与单进程开发；生产 worker 强校验 Redis。
4. **孤儿清扫为 worker 启动 + 可选 beat**，而非独立常驻治理服务：内部试用最小实现；是否常态化 beat 周期列为评审项。
5. **pause 孤儿返回码细化**：从一律 RUN_NOT_PAUSABLE 改为「租约新鲜→正常暂停 / 孤儿→RUN_ORPHAN_RECOVERING」，属 details.code 增量、HTTP 状态仍 409，不破坏前端既有错误处理（前端据 code 仅影响提示文案，可在交接中说明）。
6. **不实现跨 worker 任务热迁移与多 worker 抢占调度**（§1.4），内部试用固定单 worker。

## 19. 8b 测试与门禁

离线以 fakeredis/内存 Hub 替身驱动（CI 无 Redis skip 策略同真库）；真链/集成用独立 schema `m28b_` 与测试 Redis db。

| 编号 | 判定要点 |
| --- | --- |
| B-AC-1 | 租约：SET NX 互斥（同 run 第二个执行者抢约失败并安全退出）、TTL 过期可被重新抢占、心跳续期、正常结束释放 |
| B-AC-2 | 控制信号 pause：API 写 DB paused + control 键后，worker 在下一超步边界协作落 paused、发 paused 帧、释放租约；cancel 走 keep_partial 分支；信号只升不降（pause→cancel） |
| B-AC-3 | ask_followup 窗口不回归：retrieve 层边界仍只在 fan-out 消费 followup（M2-7 debug 开窗口径），控制信号不使窗口错位 |
| B-AC-4 | resume：条件 UPDATE + 删控制键 + 投 resume 任务；双击第二次 affected=0 拒绝；PostgresSaver checkpoint 续跑正确（组合链 test_integration_resume 在 Redis Hub 下通过） |
| B-AC-5 | 跨进程事件：worker publish 经 Redis 被另一「API 副本」订阅协程收到并投递本地订阅者；channel runs:{id} 隔离；无 Redis 配置时回退内存 Hub |
| B-AC-6 | 任务幂等：execute_run 重投且租约被持有 → ack 不重驱动、不重复落库；worker 崩溃（模拟 lease 过期）后重投可续跑 |
| B-AC-7 | 有限重试：ProviderUnavailable 触发重试≤2，预算耗尽落 failed+帧；终态后不重试、不重复烧 token |
| B-AC-8 | 孤儿清扫三类：①checkpoint 挂起→paused+帧 ②有报告→按报告收敛终态 ③无报告→failed(RUN_WORKER_LOST)+帧；pending 未启动→重投一次；清扫写审计 |
| B-AC-9 | pause 孤儿返回码：租约新鲜正常暂停；租约过期返回 409 RUN_ORPHAN_RECOVERING 且不清不楚地假装成功 |
| B-AC-10 | 0007 迁移：两列可空、ix_runs_status_lease 存在、upgrade/downgrade 往返；历史 run 两列 NULL 不影响 API |
| B-AC-11 | 真链（docker min/dev 栈）：uvicorn + celery worker + redis 三进程，起一个 standard run 全链路 WS 事件到达浏览器；中途 pause→resume、cancel keep_partial 各一次；重启 worker 后在途 run 被清扫/可恢复 |
| B-AC-12 | 全量 pytest 零回退（8a 用例在双态 Hub 下仍绿）；ruff/mypy 零新增；openapi 路径数仍 27（b 无契约增量） |

## 20. 8b 配置项变更

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `REDIS_URL` | redis://localhost:6379/0 | 异步客户端连接；缺省时可由 CELERY_BROKER_URL 去 scheme/库派生 |
| `RUN_LEASE_TTL_SECONDS` | 30 | 在途租约 TTL |
| `RUN_LEASE_HEARTBEAT_SECONDS` | 10 | worker 租约心跳间隔（须 < TTL/2） |
| `RUN_ORPHAN_GRACE_SECONDS` | 60 | 超过租约多久才判孤儿（防启动误杀） |
| `RUN_ORPHAN_SWEEP_ENABLED` | true | worker 启动清扫开关；beat 周期是否启用（评审项，默认 false：仅启动扫一次） |
| `CELERY_TASK_MAX_RETRIES` | 2 | research 任务瞬时故障重试上限 |
| `WORKER_ENABLE_REDIS` | true | false 退回内存 Hub/进程内 registry（仅离线测试/单进程）；生产 worker 置 true 且强校验 |

## 21. 8b 风险与对策

| 风险 | 对策 |
| --- | --- |
| Celery 同步模型与 asyncio executor 不兼容 | §14.2 任务体仅做事件循环外壳，业务零改写；禁 gevent；test_workers_research 覆盖外壳驱动 |
| 控制信号导致 ask_followup 窗口错位（M2-7 回归红线） | 信号只在 debug 超步边界读，retrieve fan-out 消费逻辑不动；B-AC-3 专项回归；不得退回 values 单流 |
| worker 崩溃在途 run 永久 running | 租约 TTL + 启动/周期清扫 + 有限重投；B-AC-8 三类收敛全覆盖 |
| 租约误判杀死长超步（心跳被业务阻塞） | 心跳独立协程、TTL 30s/心跳 10s；超步正常 await 不阻塞心跳；grace 60s 防启动误杀 |
| Redis 故障导致 WS 事件全断/控制失效 | API↔DB 轮询仍可收敛看板（M2-4 既有）；Redis 不可用时 worker 启动快速失败（不静默假运行）；控制键写失败时 pause/cancel 返回明确错误而非假成功 |
| 任务重投重复执行烧 token | 租约 NX + checkpoint + 终态后不重试 + max_retries=2；B-AC-6/7 验证 |
| Pub/Sub 丢事件 | 事件以 DB 为权威、WS 为加速；前端轮询/重连兜底；不引 Stream（非目标） |
| 部署复杂度上升（多进程/WSL 保活） | docker-compose 补 worker/redis 服务化；ops 手册补启动与保活；联调脚本化 |
| 8b 改动面大引入回归 | 严格分段：8a 先关闭冻结；8b 在 8a 之上开发；executor 主体逻辑不动，只换调度/信号/Hub 后端三层；634+ 既有测试 + B-AC 组合链兜底 |

## 22. 8b 评审拍板项

1. **Celery 外壳 vs 纯 asyncio worker**：本方案裁决保留 Celery（broker/重试/acks_late 已就绪），任务体内建事件循环跑现有 executor；备选 systemd 守护脚本 + Redis 队列，评审定。
2. **孤儿 beat 周期是否启用**：默认仅 worker 启动扫一次（`RUN_ORPHAN_SWEEP_ENABLED` 控启动扫，beat 默认关）；是否在内部试用就开 600s 周期扫，评审定。
3. **新增错误码/审计动作**：`RUN_ORPHAN_RECOVERING`（409 details.code）、AuditAction 是否扩 `run.orphan_recovered`，评审确认。
4. **min 联调栈是否纳入 redis+worker**：本方案裁决纳入（docker-compose.min.yml 补两服务），保证联调即真实形态；若希望 min 栈继续轻量，则需提供单进程开关（WORKER_ENABLE_REDIS=false + create_task 回退）作为显式双模，评审定（本方案倾向不保留长期双模，仅测试态回退）。
5. **worker 副本数**：内部试用固定 1 research worker（不做抢占调度），评审确认容量是否够。

## 23. M2-8b 实现冻结记录（v1.0，2026-09-15）

### 23.1 落地清单（对照 §15）

| 组件 | 位置 | 结论 |
| --- | --- | --- |
| Redis 客户端 | `app/core/redis.py`（新增） | `build_redis`（decode_responses=True）/`close_redis` 工厂，API 与 worker 各自生命周期持有 |
| Redis Hub | `app/realtime/hub.py`（改造） | 双态：无 redis 内存直连（旧行为）；有 redis 构造即起后台桥接协程 `PSUBSCRIBE runs:*`，publish 本地直投 + PUBLISH 扇出，origin node_id 丢弃回环，channel 语义不变 |
| 在途租约/信号 | `app/orchestrator/lease.py`（新增） | `lease:run:{id}` SET NX PX + WATCH 事务 owner 校验续期/释放；`control:run:{id}` 只升不降（pause→cancel）+1h 兜底 TTL；不引入 Lua（fakeredis 无 lupa） |
| 跨进程 Registry | `app/orchestrator/registry.py`（改造） | `RunRegistryLike` Protocol 双态：内存态 M2-5 语义全保留；`RedisRunRegistry` 以租约/控制键适配，reserve/restore/is_reserved 空操作（DB 翻转 + 租约 NX 承接），get/set 工厂注入 |
| executor 接线 | `app/orchestrator/executor.py`（改造） | 新增 `owner_id`；register NX 失败幂等退出；`_poll_stop_signal` 在每个 debug/values 超步帧前读控制键并对本任务 `task.cancel()`，复用 M2-5 CancelledError 收尾；Redis 态 `ProviderUnavailableError` 上抛交 Celery 有限重试，不落 failed；ask_followup fan-out 窗口逻辑零改动 |
| Celery 任务 | `app/workers/tasks/research.py`（改造） | 同步薄外壳 `asyncio.run`；独立心跳协程（10s 续 Redis 租约 + 低频写 PG 镜像，不被业务 await 阻塞）；execute 参数全部从 run 行重载；resume 载荷随任务重放；max_retries=2，预算耗尽独立收敛 failed+帧 |
| Worker 引导 | `app/workers/bootstrap.py`（新增） | `worker_context` 任务级装配（DB/checkpointer/Redis/Hub/lease/registry/LLM/检索）；WORKER_ENABLE_REDIS=false 或 ping 失败快速失败，不静默内存态；不 import app.api |
| API 调度改造 | `app/api/v1/runs.py`、`app/services/runs_control.py`（改造） | 两处 create_task 按 registry 双态分派：Redis 态 `execute_run.delay` / 条件 UPDATE+`clear_stop_signal`+`resume_run.delay`，内存态保持 create_task；pause 孤儿码 Redis 态细化（见 23.2） |
| 孤儿清扫 | `app/workers/reaper.py`（新增） | 扫 pending/running 且 created_at 早于 grace、lease_until 为空或过期的 run：checkpoint interrupt→paused；final 报告→succeeded/draft→cancelled；无报告→failed(RUN_WORKER_LOST)；pending 未启动 Redis 计数重投一次超限 failed；全部写 `run.orphan_recovered` 审计、best-effort 补发终态帧 |
| 迁移 | 0007（新增）+ `db/models/run.py`（改造） | research_runs 增 execution_owner(String64)/lease_until(timestamptz)，均可空；ix_runs_status_lease(status, lease_until)；downgrade 往返 |
| 配置 | `config.py`、`.env.example`（改造） | §20 七项全部落地；celery_app 补 `task_routes` 前缀路由（research/report/ingestion），否则消息落默认 celery 队列无人消费 |
| 部署 | `docker-compose.dev.yml`、`docker-compose.min.yml`（改造） | min 栈纳入 redis+worker（评审拍板项 4），worker 容器 build 同镜像、depends_on healthy；API 仍在主机前台便于排查 |
| 测试 | test_lease(8)/test_hub_redis(5)/test_workers_research(6)/test_orphan_reaper(7)/test_executor 新增(4)/test_run_registry 新增(6)/test_runs_control_api 新增(2)/test_integration_m28b(2) | B-AC-1~B-AC-10、B-AC-12 覆盖；既有 8a/各套件在双态下保持全绿 |

### 23.2 与评审稿（v0.2）的实现偏差

1. **清扫触发点**（§16.5）：方案表述为「worker 进程启动时执行一次」，实现为「**每个 prefork 子进程的第一个任务执行前**扫一次」（`sweep_once_at_startup` 挂在 `_execute`/`_resume` 入口，进程级 `_startup_swept` 保证每子进程仅一次）。原因：worker 依赖为任务级装配，清扫需要 checkpointer/DB/Redis 全部就绪后才能判定 checkpoint 挂起。语义差异：worker 空转（无任务投递）时不扫，首个任务到达时**先清扫后执行**；收敛与触发任务在同一任务体内顺序完成。
2. **任务级依赖装配**（§14.2）：采用方案允许的「任务体内 asyncio.run」形态，未选「worker 启动时常驻 loop/常驻依赖」。engine/checkpointer 连接池、Redis Hub 每任务建拆。本机 Windows+WSL drvfs 实测冷启动 11.9s（其中 LLM provider 模块首次导入 11.7s，redis/db/checkpointer 合计 <1s），投递到 running 翻转约 13s；ext4/容器部署预期显著更快。进程级常驻 loop 池化为后续优化项，不改变正确性。
3. **PG 租约镜像写回方式**（§16.2）：方案设想「随超步提交捎带」，实现为心跳协程每 `RUN_LEASE_HEARTBEAT_SECONDS`（10s）一次独立事务写 execution_owner/lease_until；与图超步事务解耦，单 run 写频率固定 10s。
4. **B-AC-9 孤儿码按双态区分**（§16.5/§18.5）：Redis 态 running 无新鲜租约返回 409 `RUN_ORPHAN_RECOVERING`；内存态（WORKER_ENABLE_REDIS=false，仅单进程/离线测试）保留 M2-5 原码 `RUN_NOT_PAUSABLE`——内存态无跨进程租约概念，不触发新码。
5. **reaper 审计主体**：audit_entries 无用户/团队外键，`run.orphan_recovered` 的 team_id/user_id 统一写 `"system"`（评审拍板项 3 落地）。
6. **Celery 队列路由**（§14.2 补充）：`celery_app.py` 显式 `task_routes`（research.*/report.*/ingestion.* → 同名队列）。既有骨架仅在 worker 侧 `-Q` 声明消费队列，发送端无路由时消息会落到默认 `celery` 队列导致无人消费；本批补齐。

### 23.3 门禁证据

- **全量 pytest：695 passed**（M2-8a 冻结基线 655，净增 40），零跳过；其中真库/Redis 集成：`test_integration_m28b.py` 2 个（m28b_orm 孤儿扫描新鲜度过滤+三类收敛+审计+二次超限、0007 列/索引 upgrade/downgrade/upgrade 往返）真实执行未 skip。
- **离线 Redis 替身**：fakeredis 2.38（dev 依赖新增），租约 NX/TTL/WATCH 事务、Hub 双节点跨进程扇出/回环去重/频道隔离、worker_context 强校验、Celery 外壳重试全部离线覆盖。
- **ruff**：本批 32 个变更 py 文件 check 全净、format 已应用（含清除 test_hub_redis.py 误带的 UTF-8 BOM）。
- **mypy**：53 errors / 28 files，相对 dev 基线 54 errors / 29 files **零新增**；净减 1 条为 execute_run 装饰器加精确 `# type: ignore[untyped-decorator]`；jose stub 缺失 note 随检查文件集合挂点漂移（非 error，基线同样存在）。
- **OpenAPI**：`export_openapi` 仍 **27 路径**，openapi-m2-8.json 与 dev blob 零差异（b 段零契约增量，不产新快照）。
- **B-AC-11 三进程真链**（docker min 栈 PG+Redis，主机 WSL 单并发 worker `--concurrency=1` + uvicorn，WORKER_ENABLE_REDIS=true，alembic head=0007，2026-09-15）：
  1. **pause→resume→succeeded**：run `01M2HR3W8NA20VEMAZ4DQ4C9MB`。worker 从 research 队列取任务驱动；running 中 pause（clarify 阶段）收 `run.finished(paused)`；kind=proceed 恢复（条件 UPDATE+删控制键+resume_run 任务）后收全量跨进程帧——五阶段 stage.started/finished、5 个 sub_question、20 条 evidence.fetched、token.usage.update、`run.finished(succeeded)`，事件全部经 worker→Redis Pub/Sub→API 桥接→WS 到达。
  2. **cancel keep_partial**：run `01M2HR540W5D4KXXJ12BB2WW3R` → `run.finished(cancelled)`。
  3. **worker 重启孤儿清扫**：SQL 注入 running 且 lease_until=NULL 的孤儿 `01M28BORPHAN0000000000001`，grace=0 重启 worker 并投递触发 run `01M2HRBNJGGJEXGXEPVM5DDVCW`；触发任务先 sweep：孤儿 05:24:24.44 收敛 failed(RUN_WORKER_LOST)，终态帧经 Redis 送达已提前订阅的 WS、finished_at 落库、`run.orphan_recovered` 审计落库；触发 run 随后 05:24:24.47 正常抢约执行（后 cancel 收尾）。
  4. 首次启动清扫在真实联调库另演绎 §16.5 第 3 类：历史 pending 未启动 run `01M2HJZEJTPN9TC9YCN2X2CZ1F` 被重投一次并自然 succeeded；清扫后当日无 running/pending 残留（4 个历史 paused 属合法 HITL 挂起，不扫）。
  5. 观察（非缺陷）：prefork 子进程 structlog 业务日志在重定向文件下未见输出（Celery MainProcess 日志正常）；容器化部署 stdout 由 Docker 收集，排查以 run 行 owner/lease 列、Redis 键与 WS 帧为准。
- **联调前置**：`alembic upgrade head`（0006→0007）；min 栈新增 redis/worker 两服务（`docker compose -f docker-compose.min.yml up -d --build`）；主机需同时常驻 uvicorn 与 `deep-research-worker`（环境手册 §7）。

