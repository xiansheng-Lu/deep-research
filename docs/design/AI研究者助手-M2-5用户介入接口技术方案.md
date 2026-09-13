# AI 研究者助手 · M2-5 用户介入接口阶段技术方案

- 版本：v0.1（实施前评审稿，2026-09-14；实现冻结后升 v1.0）
- 范围：运行软暂停/继续/硬取消四个 REST 控制点、统一 HumanInput 恢复模型、主动介入（追加追问/剔除证据）、澄清挂起真正联通与 `interrupt.requested` 帧、WS 控制指令与 ACK、0004 介入队列表、控制动作审计留档、M2-5 累积 OpenAPI 冻结
- 上游依据：《AI研究者助手-后端详细设计》§4.2.5/§4.2.5a/§4.2.5b/§4.3.1/§6.5.9/§6.5.10/§6.6；《AI研究者助手-后端契约草案》§6.1/§6.2（已评审定版）；《AI研究者助手-软件开发计划》§3 M2-5、§5.2（A8 审计动作）；《AI研究者助手-前端详细设计》§9.5/§13.1~§13.3；《AI研究者助手-前端M2任务分解与方案设计》WP-15

## 1. 目标与范围

M2-1~M2-4 已交付意图路由、批判收敛、看板与实时成本；研究任务一旦发起，用户除在澄清/裁决挂起点作答外**没有任何受控干预手段**：前端 WP-15 依赖的 pause/resume/cancel/intervene 真实后端全部 404（M2-3/M2-4 联调已实测登记）。本工作包交付：

1. **运行控制四端点**：`POST /runs/{id}/pause`、`/resume`、`/cancel`、`/intervene`，请求/响应/错误码严格按契约草案 §6.1/§6.2 定版形态。
2. **软暂停/硬取消的执行期语义**：进程内运行任务注册表持有在途协程句柄，以协作式取消让 LangGraph 在 super-step 边界落 `paused`/`cancelled`；`paused` 经既有检查点恢复路径续跑，`cancelled` 为终态不可恢复。
3. **统一恢复模型**：`resume` 的 `human_input` 三分支（`answers` 澄清/裁决答案、`action` 介入动作、`kind=proceed` 纯继续）；修复当前澄清答案未合并进 state、回流可能再次挂起的缺陷。
4. **主动介入队列**：running@retrieve 追加子问题补查（fan-out 分层循环边界动态消费）、running@retrieve/standardize/critique 剔除证据（DB 标记 + critic/reporter 过滤）；`Idempotency-Key` 自然幂等。
5. **澄清挂起可观测**：挂起时推送 `interrupt.requested` 帧（questions/defaults/expires_in_seconds），前端 ClarificationCard 不再依赖 mock。
6. **WS 控制指令**：WS 连接内 `intervene`/`cancel` 客户端指令与 REST 等价，回 `intervene.ack`/`intervene.error`（带 request_id），前端 §9.5 双通道预期闭环。
7. **控制动作审计**：pause/resume/cancel/intervene 写 `audit_entries`（`run.pause`/`run.resume`/`run.cancel`/`intervene.ask_followup`/`intervene.exclude_evidence`），为 M2-8 审计完整版铺底。
8. 冻结 `docs/contract/openapi-m2-5.json`（M2-4 快照 20 路径 + 本批 4 路径 = 24 路径），出联调交接单。

### 1.1 非目标（本包明确不做）

- `revert_stage` / `mark_doubt` 介入动作：前端详设 §13.2 标注 M4；收到这两类 action 返回 409 `INTERVENE_NOT_ALLOWED`，不实现图回退。
- 子问题计划 gate（契约草案 §6.5，`PUT /sub-questions/plan`）：产品维持 MVP「自动执行、只读展示」，不建确认 gate。
- 服务端澄清超时自动续跑：`expires_in_seconds` 仅为展示语义（前端只读标注「已按默认假设」），后端不做定时器、不自动注入默认答案。
- 跨进程控制与 Redis Pub/Sub：研究任务仍在 API 进程内 `asyncio.create_task`，注册表为进程内单例；多 worker / Celery 接管在 M2-8，届时注册表升级为 DB 租约 + 消息信号。
- 前端取消按钮入口：前端详设 §13.2 标注 M3（依后端能力）；本包交付 cancel 能力与 WS 指令，前端入口节奏由前端掌握。
- `Stage.status` 增加 `cancelled` 枚举：取消时当前阶段行保持 `running`（与 paused 同口径，run 终态为唯一事实），避免破坏 M2-4 已冻结的 StageResponse 五值契约。
- 报告生成后的剔除/存疑/重生成：报告一旦 succeeded，证据不可改（`exclude_evidence` 在 report 阶段开始后 409）；M4 的 `/reports/{id}/doubt` 通道另行排期。
- 孤儿 run（API 进程重启时停在 running/paused 的行）的启动恢复/清扫：M2-8 生命周期治理统一处理；本包仅保证不产生新的不一致。
- pause/resume 的 WS 客户端指令：前端 ClientCommandType 仅含 intervene/cancel，软暂停只走 REST，不增设指令。

## 2. 现状盘点（M2-4 基线）

### 2.1 已具备（复用，不重复造）

| 能力 | 位置 | 现状 |
| --- | --- | --- |
| HITL 恢复执行器 | `app/orchestrator/executor.py::resume_research_async` | `aupdate_state(human_input)` + `Command(resume=...)` 从 interrupt_before 挂起点续跑；仅 paused 可恢复（非 paused 静默忽略）；恢复护栏异常落 failed |
| 图挂起点 | `executor.py::_INTERRUPT_BEFORE = ("await_human", "user_intervention")` | 澄清/裁决挂在 await_human；成本超 90% 挂在 user_intervention（节点是空占位 `return {}`，proceed 即可续到 report） |
| 挂起落库口径 | `_drive_to_terminal` 无报告产出分支 | run 置 paused、当前阶段行保持 running、回写真实 token、推 `run.finished(status=paused)` 后 WS 关流；前端 M2-2 起保活重连 |
| 裁决恢复 | `app/services/conflicts.py::resume_after_verdict` | 末条裁决后 `asyncio.create_task(resume_research_async)`，构造 `human_input.answers.verdicts` |
| 检查点 | AsyncPostgresSaver（生产）/ MemorySaver（测试） | thread_id=run_id，每 super-step 持久化；恢复重放安全已由 M2-2/M2-4 确立（证据按 ID 插缺失、子问题 upsert、阶段行唯一约束） |
| 每超步中间提交 | `_astream_and_publish` | 逐快照 `session.commit()`，协作式取消落在超步之间时落库状态一致 |
| 成本发射器 | `app/quota/emitter.py` | paused/cancelled 收尾同样可强制补发末帧 |
| 实时通道 | hub + ws.py | 频道 `runs:{run_id}`；ws `_receive_messages` 目前只消费 `pong`，**已预留 intervene/cancel 注释位**；连接建立时已做 run 归属校验 |
| 审计表与枚举 | `audit_entries`（0001 已建）、`app/audit/base.py` | 表可用；AuditAction 枚举仅有 start/interrupt/complete，缺 pause/resume/cancel/intervene.*；logger 为内存占位 |
| 前端契约面 | types.ts `PauseRunRequest/CancelRunRequest/HumanInput/InterveneRequest/RunControlResponse`、realtime/types.ts `ClientCommand/InterveneAck/Error Envelope`、mock router/engine 四端点模拟 | 字段与错误码（RUN_NOT_PAUSABLE/RUN_NOT_RESUMABLE/INTERVENE_NOT_ALLOWED/INVALID_ACTION_PAYLOAD）均已预置 |

### 2.2 缺口（本包要补）

1. 四个 REST 端点不存在（runs.py 仅 POST 创建/GET 详情/GET report + M2-4 六个看板 GET）。
2. 在途研究任务无句柄注册表：`asyncio.create_task` 后任务不可寻址，无法 pause/cancel；也无法防止 paused 行被重复恢复。
3. **澄清恢复未真正联通（缺陷）**：`await_human.run` 的 clarify 分支只回写 `interrupt_reason`，未调用已存在的 `clarifier.clarification_to_state_payload` 把 `human_input.answers` 合并为 `state.clarification`；回流 clarifier 后 `clarification` 仍为 None，真实 LLM 可能再次判需追问而重复挂起（当前测试因替身 LLM 第二次返回无需追问而通过，真链有死循环风险）。
4. `interrupt.requested` 帧从未发布；`RunResponse`/REST 也无 interrupt_payload 通道，前端澄清卡在真链只能空等。
5. running 中主动介入无落地通道：无介入队列表、fan-out 无动态补查入口、`Evidence.excluded_by_user` 只在看板查询过滤，critic/reporter 不过滤，剔除不影响结论。
6. 审计无真实写入器，控制动作 reason 无处落档。
7. export_openapi.py 无 M2-5 白名单。

## 3. 组件与代码落位

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| 运行任务注册表 | `app/orchestrator/registry.py`（新增） | 进程内单例 `RunRegistry`：run_id → `RunHandle(task, mode_event)`；register/unregister/cancel_for；测试可注入假表 |
| 执行器取消语义 | `app/orchestrator/executor.py`（改造） | 首跑/恢复任务统一注册；捕获 `asyncio.CancelledError` 后按注册表 mode（pause/cancel）落库收尾；T2 详述 |
| 澄清回流修复 | `app/orchestrator/nodes/await_human.py`（改造） | clarify 分支合并 human_input.answers → clarification（复用 `clarification_to_state_payload`）+ needs_clarification=False |
| 澄清挂起帧 | `executor.py::_drive_to_terminal`（改造） | paused 结局且 interrupt_reason=clarify 时推 `interrupt.requested` |
| 介入队列模型 | `app/db/models/run.py` 或新增 `app/db/models/intervention.py` + 0004 迁移 | `RunIntervention` 表（§8） |
| 介入队列服务 | `app/services/interventions.py`（新增） | 入队（校验/幂等/审计）、fan-out 边界拉取并标记 applied、剔除证据 DB 写 |
| fan-out 动态补查 | `app/orchestrator/nodes/researcher_fan_out.py`（改造） | layer 循环每轮后经 deps 拉取 pending ask_followup，构新子问题追加为后续 layer；exclude 在入 standardize 前从证据集过滤 |
| 结论链剔除过滤 | `nodes/critic.py`、`nodes/reporter.py`（改造） | 渲染前按 DB excluded id 集合过滤证据（经 NodeDeps.db_session 一次查询） |
| 控制域 schema | `app/schemas/runs.py`（扩展） | PauseRunRequest/ResumeRunRequest/CancelRunRequest/InterveneRequest/RunControlResponse/HumanInput 模型（字段对齐前端 types.ts） |
| 控制端点 | `app/api/v1/runs.py`（扩展四个 POST） | 归属复用 get_owned_run 口径；service 层在 `app/services/runs_control.py`（新增） |
| 审计写入器 | `app/audit/logger.py`（改造为真实 ORM 写入）+ `app/audit/base.py`（补动作枚举） | 轻量 `write_audit_entry(session, ...)`；失败只告警不阻断主链（与实时帧同口径） |
| WS 指令 | `app/realtime/ws.py`（改造） | receive 循环识别 intervene/cancel → 调同一 service → 回 ack/error（request_id 透传） |
| 契约 | `app/export_openapi.py` + `docs/contract/openapi-m2-5.json` | `_M25_ENDPOINTS` 四路径；M2-4 快照默认冻结（加 `--refresh-m24`） |
| 配置 | `app/core/config.py`、`.env.example` | `CLARIFICATION_EXPIRES_SECONDS`（仅展示语义，默认 900） |

## 4. 任务分解与实现顺序

### T1 注册表 + pause/cancel 执行期语义（前置）

`RunRegistry`、executor 注册/取消收尾、paused/cancelled 落库与终态帧、stages 口径、keep_partial 草稿。

### T2 resume 统一入口与澄清联通（T1 后可独立验证）

`POST /resume` 三分支、状态机乐观抢占、澄清 answers 合并修复、`interrupt.requested` 帧；裁决仍保留 `/conflicts/{id}/verdict` 自动恢复通道（两条入口共用 `resume_research_async`，不改 M2-2 行为）。

### T3 主动介入队列

0004 表 + 入队端点 + fan-out 动态补查 + exclude DB 标记与 critic/reporter 过滤 + 幂等。

### T4 WS 控制指令与审计

ws intervene/cancel → ack/error；五个控制动作写 audit_entries；AuditAction 枚举补齐。

### T5 测试与门禁

单测 + 真库集成（m25_* 独立 schema）+ 全量回归，AC 见 §11。

### T6 契约冻结与交接

openapi-m2-5.json（24 路径）、前端只读字段核对、交接单、README、方案升 v1.0。

## 5. 关键设计

### 5.1 状态机与允许矩阵

```text
pending ──首跑调度──▶ running ──pause──▶ paused ──resume──▶ running
                         │  ▲                │  ▲
        澄清/裁决/成本挂起└──┘(已有路径)      │  └────cancel────▶ cancelled
                         │                   │
                         └────cancel─────────┘
running/paused ──cancel──▶ cancelled（终态）
succeeded/failed/cancelled：pause/resume 409；cancel 幂等返回当前状态
```

| 端点 \ 当前状态 | pending | running | paused | succeeded/failed | cancelled |
| --- | --- | --- | --- | --- | --- |
| pause | 409 RUN_NOT_PAUSABLE | 200 | 409 RUN_ALREADY_PAUSED | 409 RUN_NOT_PAUSABLE | 409 |
| resume | 409 RUN_NOT_RESUMABLE | 409 RUN_NOT_RESUMABLE | 200（按挂起原因校验载荷，§5.3） | 409 | 409 |
| cancel | 200（幂等置 cancelled） | 200 | 200 | 200 幂等返回当前状态 | 200 幂等 |
| intervene ask_followup | 409 INTERVENE_NOT_ALLOWED | 仅 current_stage=retrieve 受理 | 409（走 resume{action} 亦不支持补查，见 §5.5） | 409 | 409 |
| intervene exclude_evidence | 409 | current_stage ∈ retrieve/standardize/critique 受理 | 409 | 409 | 409 |

- 409 统一经现有 `ConflictError`，`code` 保持 `"conflict"`，业务判别码放 `details.code`（与 M2-2 裁决 409 同一响应形态，前端按 message/已有映射处理）；为减少前端改动，响应 `message` 直接使用上表中文短语。
- 422：请求体结构非法（action 未知字段/payload 缺必填）用 `ValidationError`；payload 业务非法（evidence/sub_question 不存在或跨 run）仍归 404/409 归属口径，不泄漏存在性。
- 全部端点 401（无 token）与 404（非创建者/不存在）口径同 M2-4。

### 5.2 软暂停与硬取消：协作式取消协议

LangGraph 无运行中 pause 原语，本包以「注册表信号 + `task.cancel()` + 取消原因分流」实现：

1. `run_research_async` / `resume_research_async` 调度时把 asyncio.Task 注册进 `RunRegistry`，协程终了（succeeded/failed/paused/cancelled）finally unregister。
2. pause：校验 running 且注册表有句柄 → 置 `handle.mode="pause"` → `task.cancel()`；REST 请求**不等协程收尾**，先把 run 行乐观置 paused 并提交，再返回 200 `{run_id,status:"paused"}`。协程在下一 await 点收到 CancelledError。
3. executor 包一层取消归一：在 `_astream_and_publish`/`_drive_to_terminal` 外层捕获 `asyncio.CancelledError`，读注册表 mode：
   - `pause`：run 置 paused（finished_at=None）、当前阶段行保持 running、token 写穿当前快照值、commit；推 `run.finished(status=paused)`（复用既有终态帧出口，WS 按既有口径关闭，前端保活重连）；**吞掉** CancelledError 让任务正常退出。
   - `cancel`：run 置 cancelled + finished_at；`keep_partial=true` 且最后一份快照含非空 report_draft 时落 `Report(status="draft")` 并在响应中带回 partial_report_id（M2 报告为一次性确定性拼装，多数取消点无草稿，字段允许缺省）；推 `run.finished(status=cancelled)`；同样吞掉取消异常。
   - 注册表无 mode（意外取消，如进程关停）：按 cancel 口径落库，避免 running 孤儿悬挂。
4. 一致性边界：cancel/pause 可能落在节点 IO（LLM/检索 await）中途，该 super-step 无 checkpoint、无中间提交；恢复（仅 pause 可恢复）时节点整体重放，重放幂等已由 M2-2/M2-4 论证（证据插缺失、子问题 upsert、阶段行 succeeded 不回退）。落在两超步之间时，最近完成节点的 checkpoint 与中间提交均已落盘。
5. 乐观置 paused 与协程收尾的竞态：协程可能恰好在 cancel 前进入终态（succeeded/failed）。归一逻辑以「协程内观察到的真实结局优先」：若捕获 CancelledError 时 run 已在本协程内被置终态并提交，则保留终态、不再回写 paused/cancelled；REST 200 与最终状态短暂不一致由随后的 `run.finished` 帧 + GET 详情收敛（前端 M2-2 起已有该收敛模式）。
6. cancel paused run：无协程句柄，service 直接 UPDATE 置 cancelled 并推帧；cancel 已终态 run：纯幂等读返回。

### 5.3 resume：三分支载荷校验与调度

请求体 `{human_input?: {answers?, action?, kind?}}`，三选一互斥（同时出现多个键 422）：

| 挂起原因（interrupt_reason / 阶段） | 允许的输入 | 行为 |
| --- | --- | --- |
| clarify | `answers`（至少一个非空答案键） | await_human clarify 分支合并为 `state.clarification={"_human_answers": {...}}` 并置 needs_clarification=False；回流 clarify 节点见 clarification 直接跳过进入 decompose。空 answers/proceed → 422 `validation_error`（提示需提交澄清答案） |
| critique（high 冲突挂起） | `answers.verdicts`（与 §6.2 同构）或 proceed | verdicts 经 await_human 既有解析回流 critic；proceed 表示不追加裁决继续（critic 按既有未决冲突逻辑自行收敛或再挂，行为不新造）。注：前端 M2 裁决主通道仍是 `/conflicts/{id}/verdict`，本分支仅为统一入口完整性 |
| cost（user_intervention） | `kind=proceed` 或空 body | user_intervention 空节点 → report；与前端成本闸门实测恢复路径一致 |
| 手动软暂停（任意阶段） | `kind=proceed` 或空 body | 从最近 checkpoint 续跑当前阶段 |

调度防重：端点事务内 `UPDATE research_runs SET status='running' WHERE id=:id AND status='paused'`，rowcount=0 即 409（并发双击/状态已变）；提交后 `asyncio.create_task(resume_research_async(...))` 并注册注册表。响应 200 `{run_id,status:"running"}`（异步语义，最终成败以帧/REST 为准，与创建 run 同模式）。

`resume_research_async` 改造：现状「非 paused 静默忽略」保留为协程内兜底；human_input 为空时归一为 `{"kind":"proceed"}`；恢复任务同样进注册表，可被 pause/cancel。

### 5.4 interrupt.requested 帧（澄清挂起）

`_drive_to_terminal` 判定 paused 结局时，若最后快照 `interrupt_reason=="clarify"` 且 `interrupt_payload.questions` 非空，在 `run.finished(paused)` **之前**推一帧：

```json
{
  "type": "interrupt.requested",
  "stage": "clarify",
  "payload": {
    "reason": "clarify",
    "questions": [{"key":"scope","text":"...","options":[],"recommended":null}],
    "defaults": {},
    "expires_in_seconds": 900
  }
}
```

- 字段对齐前端 `InterruptRequestedPayload`；critique 挂起不发本帧（已有 conflict.detected）；成本挂起不发（成本卡 danger + paused 已足够）。
- 重连补齐：本帧不做服务端回放（沿用 M2-4 口径）；为支持刷新页面后仍能渲染澄清卡，`GET /runs/{id}` 的 RunResponse **新增可选字段** `interrupt: {reason, questions, defaults, expires_in_seconds} | null`（paused@clarify 从图 checkpoint state 的 interrupt_payload 读；非澄清挂起/非 paused 为 null）。读取 checkpoint state 的方式：`graph.aget_state(config)` 取 interrupt_payload（service 层编译只读图实例，不驱动执行）。

### 5.5 主动介入队列（running 中介入，不切 run 状态）

核心取舍：intervene 不采用「先暂停再恢复」（run 状态会抖动、且 retrieve 之后的挂起点无法重开检索），而采用 **DB 队列 + 节点安全边界消费**，run 始终保持 running。

**入队（POST /intervene）**：

- `ask_followup`：仅 running@retrieve。payload `{sub_question_id（必填，须本 run 子问题）, question（1~1000 字非空）, reason?}`；写 RunIntervention(status=pending)。
- `exclude_evidence`：running 且 current_stage ∈ retrieve/standardize/critique。payload `{evidence_id（必填，须本 run 证据）, reason?}`；**同步**完成 DB 标记（`excluded_by_user=true`，立即提交、对看板 REST 生效），RunIntervention 直接记 applied（供 fan-out/结论链做 state 侧剔除）；不存在/跨 run 404。
- 幂等：可选 `Idempotency-Key` 头（前端双击防护带），run_interventions 对 (run_id, idempotency_key) 建唯一索引；重复键直接返回首条记录对应的 200 结果，不重复执行。无键时不强制（ask_followup 以业务内容执行一次，重复点击是不同请求，前端按钮防抖兜底）。
- 响应 200 `RunControlResponse{run_id,status}`（exclude 无附加字段；保持统一形态）。

**消费（fan-out layer 边界）**：

- `researcher_fan_out.run` 把拓扑分层改为动态队列：初始 layers 同现状；每完成一层 gather 与落库后，用 `deps.db_session` 查本 run status=pending 的 RunIntervention：
  - ask_followup：新建 SubQuestionDict（新 ULID，depends_on=[payload.sub_question_id]，pending）→ persist_sub_questions upsert → 发 `sub_question.created`（channel/帧形态同 T3 of M2-4）→ 作为新一层加入循环（followup 检索走同一 `_research_one`，完成发 started/finished）；标记 applied。
  - 本层待剔除证据 id：从 `all_evidence` 与相关子问题 evidence_ids 移除（已落库的检索原始证据行保留，排除标记为准），保证不进 standardize。
  - 无 pending 时正常结束；followup 层数不设硬上限但受 token 预算闸门天然约束（补查同样累计 token，90% 挂起）。
- 节点每轮检查同时响应协作式取消（cancel/pause 在 gather await 点生效，§5.2 已覆盖）。

**结论链过滤**：

- critic：构建冲突前用 `deps.db_session` 一次查询本 run `excluded_by_user=true` 的 id 集合，从输入证据中剔除（已检测出的冲突不追溯改写；仅影响后续报告结论）。
- reporter：`_index_evidence` 汇总后按同一 id 集合过滤；被剔除证据不产生论断引用。
- standardize：无需改（分类结果落库后剔除以 DB 标记为准；看板默认已过滤）。

**WS 指令通道**：ws receive 循环解析 `{type:"intervene"|"cancel", request_id, payload}` → 复用同一 service（user_id/run_id 取自连接鉴权上下文）→ 回信封 `{type:"intervene.ack",request_id,payload:{status}}` 或 `{type:"intervene.error",request_id,payload:{code,message}}`；cancel 指令的结果同样回 ack（前端 CommandAck 联合形态）。WS 指令不带来超出 REST 的能力，仅省一次 HTTP；异常不归一吞掉，必须回 error 帧。

### 5.6 审计留档

- `AuditAction` 增补：`RUN_PAUSE="run.pause"`、`RUN_RESUME="run.resume"`、`RUN_CANCEL="run.cancel"`、`INTERVENE_FOLLOWUP="intervene.ask_followup"`、`INTERVENE_EXCLUDE="intervene.exclude_evidence"`。
- `app/audit/logger.py` 提供 `write_audit_entry(session, *, team_id, user_id, action, target_type, target_id, payload)`：插行 + 随调用方事务提交；控制端点在同一事务内写（pause/cancel/resume/intervene），reason 入 payload。
- 帧广播失败不阻断主链；审计写入失败同口径（log.warning，不回滚用户动作——控制可用性优先于审计完备性，与详设审计「不阻断业务」原则一致）。
- 审计查询端点 `/audit` 仍是占位，M2-8 交付。

## 6. REST 契约（本批新增，冻结于 openapi-m2-5.json）

四个端点均挂 `/api/v1/runs/{run_id}` 前缀，Bearer 鉴权，创建者归属（不符统一 404）：

| 方法与路径 | 请求体 | 200 响应 | 主要错误 |
| --- | --- | --- | --- |
| POST `/pause` | `{reason?: string}` | `RunControlResponse{run_id,status:"paused"}` | 409 RUN_NOT_PAUSABLE / RUN_ALREADY_PAUSED |
| POST `/resume` | `{human_input?: HumanInput}` | `RunControlResponse{run_id,status:"running"}` | 409 RUN_NOT_RESUMABLE；422 载荷互斥/澄清缺 answers |
| POST `/cancel` | `{reason?: string, keep_partial?: bool=true}` | `RunControlResponse{run_id,status, partial_report_id?}` | 幂等：终态返回实际 status |
| POST `/intervene` | `InterventionAction{type,payload}` | `RunControlResponse{run_id,status}` | 409 INTERVENE_NOT_ALLOWED；422 INVALID_ACTION_PAYLOAD；404 引用资源跨 run |

HumanInput（与前端 types.ts 逐字段对齐）：

```json
{
  "answers": {"scope": "近三年", "verdicts": {"conf_...": {"choice":"evidence_a","reason":"..."}}},
  "action": {"type": "ask_followup", "payload": {"sub_question_id":"...", "question":"..."}},
  "kind": "proceed"
}
```

RunResponse 增量（M2-4 契约的超集变更，仅新增可选字段）：

```json
"interrupt": {
  "reason": "clarify",
  "questions": [{"key":"scope","text":"...","options":[],"recommended":null}],
  "defaults": {},
  "expires_in_seconds": 900
} | null
```

## 7. 实时帧契约（不入 OpenAPI）

| 帧 type | 时机 | payload | 与前端核对 |
| --- | --- | --- | --- |
| `interrupt.requested` | clarify 挂起落库后、run.finished(paused) 前 | `reason, questions[], defaults, expires_in_seconds` | 与 `InterruptRequestedPayload` 一致 |
| `run.finished(status=paused/cancelled)` | pause/cancel 收尾 | 既有 RunFinishedPayload（cancelled 为新增状态值，前端联合类型已含） | 已含，无新帧 |
| `sub_question.created/started/finished` | followup 补查复用 M2-4 帧 | 既有形态 | 已含 |
| `intervene.ack` / `intervene.error` | 仅 WS 指令应答 | `{status}` / `{code,message}`，信封带 request_id | 与前端 ack 信封一致 |

不新增 `run.paused`/`run.resumed` 独立帧：paused 由 run.finished(paused) 承载（M2-2 冻结口径），resume 后续跑由 stage.started 表达；避免新增前端 PayloadMap 之外的帧。

## 8. 数据模型与迁移（0004）

新增 `run_interventions`（主动介入队列与留档合一）：

| 列 | 类型 | 约束 |
| --- | --- | --- |
| id | VARCHAR(26) | PK（ULID） |
| run_id | VARCHAR(26) | FK research_runs.id，index |
| user_id | VARCHAR(26) | FK users.id |
| type | VARCHAR(32) | ask_followup / exclude_evidence |
| payload | JSON | 非空默认 {}（sub_question_id/question/evidence_id/reason 原样） |
| status | VARCHAR(16) | pending / applied / rejected（pending→applied 由消费方更新；rejected 为校验竞态兜底，如补查时 run 已离开 retrieve） |
| idempotency_key | VARCHAR(64) | 可空；(run_id, idempotency_key) 唯一索引 |
| created_at / updated_at | DateTime(tz) | 时间戳 |

迁移 `0004_run_interventions.py`（down_revision="0003"）：upgrade 建表 + 唯一索引；downgrade 删表。不改动 research_runs/stages/evidence 表结构（不新增 pause_reason 列，原因走审计；不新增 stage cancelled 枚举）。

## 9. 与 LLD / 契约草案的偏离点

1. **软暂停实现方式**：LLD §6.6 OrchestratorService 骨架未定义 pause 语义，契约草案 §6.1 仅定义接口形态。本方案以进程内注册表 + `task.cancel()` 协作式取消实现，而非节点内轮询暂停标志——后者需侵入全部六个节点且 LangGraph 对 running thread 无 pause 原语；取消落在超步边界的一致性由检查点 + 重放幂等保证。跨进程形态在 M2-8 重做（§1.1）。
2. **REST 先返回、协程后收尾**：pause/cancel 的 200 可能先于协程 CancelledError 落库，极端竞态下 GET 瞬时读到旧状态；以 run.finished 帧为最终收敛，不单发 paused/cancelled 业务帧。
3. **running 中介入不经过图 interrupt**：LLD §6.5.9 把 ask_followup/exclude_evidence 放在 user_intervention 节点（挂起后 resume 注入）。本包改为 DB 队列 + fan-out 边界消费，以支持 retrieve 进行中行内追问（WP-15 场景）且不抖动 run 状态；user_intervention 节点仍只服务成本挂起 proceed。M4 revert_stage 仍走挂起/恢复语义。
4. **cancel 不新增 Stage cancelled 态**：当前阶段行保持 running，仅 run 置 cancelled；避免修改 M2-4 冻结的 StageResponse 枚举。
5. **WS 仅接 intervene/cancel 指令**：契约草案提「WS cancel 与 REST 等价」，pause/resume 无 WS 指令（前端 ClientCommandType 未声明），保持 REST 单通道。
6. **RunResponse 增 interrupt 字段**：为支持澄清卡刷新恢复（不做服务端帧回放的既定口径下唯一 REST 补齐通道），是 M2-4 冻结契约的超集增量（可选字段，旧消费方不受影响）。
7. **审计动作先于审计端点**：本包只写 audit_entries 与枚举，`/audit` 查询仍占位（M2-8）；reason 因此立即可查（库表）但暂无 UI/REST。

## 10. 测试与门禁

离线全假替身；真库用例独立 schema（前缀 `m25_`），setup/teardown 各一次 DROP SCHEMA CASCADE。

| 编号 | 判定要点 |
| --- | --- |
| AC-1 | pause：running run 收到 200 status=paused；协程在下一 await 点落 paused（阶段行保持 running、token 写穿）；run.finished(paused) 恰好一帧；paused/succeeded/failed 再 pause 均 409 |
| AC-2 | resume proceed：手动暂停后续跑至 succeeded，阶段从 checkpoint 续接（succeeded 行不回退、不重复阶段帧）；非 paused resume 409；并发双击第二次 409（乐观更新 rowcount=0） |
| AC-3 | cancel running：200 status=cancelled + run.finished(cancelled)；协程停在模拟的长 IO 节点；重复 cancel 幂等返回 cancelled；cancel succeeded run 返回 200 status=succeeded 不改状态 |
| AC-4 | cancel keep_partial：快照含 report_draft 时返回 partial_report_id 且 Report 行 draft 可 GET；无草稿时字段缺省 |
| AC-5 | 澄清联通：clarifier 挂起 → interrupt.requested 帧（questions/defaults/expires_in_seconds）→ resume answers 后 clarification 合并、不二次挂起、run 一路 succeeded；GET /runs/{id} 的 interrupt 字段在挂起时可读、恢复后为 null；澄清挂起空 body resume 422 |
| AC-6 | 成本挂起 proceed：90% 挂起后 resume 空 body → user_intervention → report → succeeded（回归 M2-4 链路） |
| AC-7 | 裁决 resume 通道：human_input.answers.verdicts 经统一 resume 入口可恢复，与 /conflicts/verdict 自动恢复结果一致（同一 await_human 解析） |
| AC-8 | ask_followup：retrieve 中 POST 介入 200；fan-out 下一边界新增子问题层（created/started/finished 帧齐、证据落库）；报告含追加证据；非 retrieve 阶段 409；sub_question_id 跨 run 404；question 空串 422 |
| AC-9 | exclude_evidence：retrieve 中剔除后证据不进 standardize/报告；standardize/critique 中剔除后 critic 冲突输入与 reporter 论断不含该证据；看板默认列表不含、include_excluded=true 可带出；report 阶段剔除 409；evidence_id 跨 run 404 |
| AC-10 | 幂等：同一 Idempotency-Key 双击只产生一条 RunIntervention 与一次补查，返回同一 200；无键时两次请求独立执行 |
| AC-11 | WS 指令：intervene/cancel 指令回 ack/error，request_id 原样透传；归属不符连接已拒；错误动作回 INTERVENE_NOT_ALLOWED error 帧 |
| AC-12 | 审计：五个动作均落 audit_entries（action/payload.reason/target 正确），审计写入失败不影响控制动作成功 |
| AC-13 | 取消/暂停重放：cancel 落在 LLM await 中途后 run 为 cancelled 不复活；pause 落在 gather 中途，resume 后该层检索重放无重复证据行/无重复帧 |
| AC-14 | 0004 迁移 head→downgrade -1→upgrade 退出成功，run_interventions 表与唯一索引随迁移增减 |
| AC-15 | 全量 pytest 零回退；ruff/mypy 对本批文件零新增告警 |
| AC-16 | openapi-m2-5.json 24 路径；四端点请求/响应/错误码与前端 types.ts 逐字段核对差异为 0（interrupt 可选字段为超集增量） |
| AC-17 | 真库集成用例使用 m25_* 独立 schema，跑后无残留 |

## 11. 配置项变更

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CLARIFICATION_EXPIRES_SECONDS` | 900 | interrupt.requested 帧与 RunResponse.interrupt 的展示超时；仅前端展示语义，后端不做超时动作 |

## 12. 风险与对策

| 风险 | 对策 |
| --- | --- |
| cancel() 落在节点 IO 中途造成半截外部副作用（如检索已发出） | 外部调用均为只读/幂等查询；LLM 调用无写副作用；重放以指纹去重 + ID 插缺失兜底（M2 既有机制） |
| 乐观置 paused 与协程终态竞态 | 协程内真实终态优先，REST 瞬时差异由终态帧 + GET 收敛（§5.2.5）；AC-1/AC-13 覆盖 |
| followup 补查被滥用刷爆预算 | 与主链共用 token 累计与 90% 闸门；单 run 介入次数 M2 不设硬上限（单用户内部口径），M2-8 审计可追溯后再加限流 |
| 进程内注册表重启丢失 | 仅影响 running 态可控性；pause/cancel 对无句柄 run：paused 409、cancel 直接置终态；孤儿清扫列 M2-8（§1.1） |
| exclude 后已生成冲突/论断不一致 | 仅影响剔除之后的 critic/reporter 读取；已落库冲突/已生成报告不追溯（M4 doubt 通道处理成品报告） |
| WS 指令与 REST 双发竞态 | 同走单例 service + DB 乐观状态机；同一 run 的控制动作最终状态一致，幂等键防双执行 |

## 13. 交付与交接清单

1. 代码：T1~T4 全部文件（§3 落位表）、0004 迁移、配置与 `.env.example`；
2. 契约：`docs/contract/openapi-m2-5.json`（派生冻结产物，同代码提交，手工不编辑）；
3. 文档：实现冻结后本文件升 v1.0 回填实测结论；`backend/README.md` M2 里程碑行回填 M2-5 状态（指针引 SDP §3）；
4. 联调：`docs/feedback/M2-5用户介入接口交接.md`（前端 WP-15 真实链路回归，重点 AC-1~AC-5、AC-8、AC-9、AC-11），回归关闭后移入 archive/；
5. 不在本包的动作（revert/mark_doubt、子问题 gate、报告后剔除、跨进程控制）以 §1.1 为准，流转 M4/M2-8，不临时扩包。
