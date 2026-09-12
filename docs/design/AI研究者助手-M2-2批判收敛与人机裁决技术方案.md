# AI 研究者助手 · M2-2 批判收敛与人机裁决阶段技术方案

- 版本：v1.0（M2-2 实现冻结，2026-09-13）
- 范围：LLM 语义冲突检测、保守降级、过程数据落库、冲突状态机、人机裁决 REST、Postgres 检查点与跨请求恢复、报告分歧呈现
- 上游依据：《AI研究者助手-后端详细设计》§4.2.6、§6.2、§6.3、§6.5.6、§6.5.7、§6.5.10、§6.6；《AI研究者助手-后端契约草案》§6.2/§6.3；冻结机读契约 `docs/contract/openapi-m2-2.json`

## 1. 目标与范围

M1 Critic 为纯启发式（title+snippet 字集合 Jaccard 0.4 且可信度不同才判冲突），无 LLM、无落库；run 挂起后图状态随一次性 InMemorySaver 释放，无恢复入口。M2-2 交付：

1. Critic 接入 LLM 语义判定（温度 0、单批 8 秒硬超时），故障统一保守降级；
2. 子问题/证据/冲突全部落库，run 归属可查；
3. high 冲突挂起门控 + 冲突三端点 REST API（列表/详情/裁决）；
4. 应用级 AsyncPostgresSaver，裁决清零后自动从检查点恢复续跑；
5. 报告显式呈现未消解分歧（both 并存 / reject 双弃）。

验收项（`.trae/specs/m2-2-critic-convergence/spec.md` AC-1~AC-16）全部由自动化测试或离线评估覆盖。

## 2. 组件与代码落位

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| 检查点工厂 | `app/orchestrator/checkpoint.py` | `CHECKPOINTER_BACKEND=postgres`（默认）构造应用级单例 `AsyncPostgresSaver`（psycopg v3 连接池），启动 `setup()` 自建表；失败/`memory` 回退应用级 InMemorySaver |
| 执行器 | `app/orchestrator/executor.py` | `run_research_async` 首次执行；`resume_research_async` 先 `aupdate_state(human_input)` 再 `Command(resume=...)`；外层失败护栏 |
| 过程数据落库 | `app/orchestrator/persistence.py` | `persist_sub_questions`（upsert）、`persist_evidence`（按 ID 幂等）、`persist_conflicts`（按 ID 幂等，resolved 写 resolved_at） |
| Critic | `app/orchestrator/nodes/critic.py` | 候选词面预筛、`detect_conflicts_semantic`、启发式 `detect_conflicts`、权威收敛、四值裁决收敛 |
| HITL 节点 | `app/orchestrator/nodes/await_human.py` | critique 分支解析 `answers.verdicts` 并推 `conflict.verdicts` 帧 |
| 条件边 | `app/orchestrator/edges.py` | `decide_after_critique`：存在 `awaiting_human` 且无对应 verdict 才挂起 |
| Reporter | `app/orchestrator/nodes/reporter.py` | 待裁决区块 + 分歧与局限区块；结论段只计数不回写 claim |
| Schema | `app/schemas/conflicts.py` | ConflictResponse / ConflictDetailResponse / ConflictEvidenceSummary / VerdictRequest / VerdictResponse |
| 领域服务 | `app/services/conflicts.py` | 归属校验、裁决状态机、剩余待裁决统计、`resume_after_verdict` 自动恢复调度 |
| REST | `app/api/v1/conflicts.py` | 三端点（tags=conflicts，路径挂 /runs 与 /conflicts，统一 /api/v1 前缀） |
| 迁移 | `app/db/migrations/versions/0002_verdict_additional_note.py` | verdicts 增列 additional_note，可 upgrade/downgrade |

## 3. 关键时序

### 3.1 检测、落库与挂起

1. standardize 产出 `standardized_evidence` 后幂等落 evidence 行（ULID 与 state 一致，保证冲突外键）。
2. critic 对候选证据对（Jaccard 召回阈值 0.12，按相似度截断 `MAX_LLM_CANDIDATE_PAIRS=12`）单批调 LLM，四类型/三严重度严格枚举，温度 0，`asyncio.wait_for` 8 秒。
3. low/medium：`resolve_by_authority`（按来源可信度，平局保留先出现者）→ Conflict 行 `resolved` + `resolved_at`，无 Verdict，不挂起。
4. high：Conflict 行 `awaiting_human`，每条立即推一帧 `conflict.detected`（resolved 不推帧）。
5. 节点出口统一 `persist_conflicts`（含预算 30% 提前返回路径）；条件边判定仍有待裁决冲突时图在 `interrupt_before=await_human` 挂起，执行器写 run=paused/current_stage=critique 并推 `run.finished(status=paused)`。

### 3.2 裁决提交与自动恢复

1. `POST /conflicts/{id}/verdict`：归属 404 → 终态 409 → 写 Verdict（1:1 unique）+ Conflict 置 resolved/resolved_at，统计同 run 剩余 awaiting_human。
2. 剩余 > 0：不调度，run 保持 paused（前端经 GET /runs 与分歧列表观测）。
3. 剩余 = 0：服务层汇总该 run 全部 Verdict 构造 `human_input={"answers":{"verdicts":{cid:{choice,reason,additional_note?,user_id}}}}`（暂停期间多条裁决均未进入图状态，必须全量带上；await_human 按 conflict_id 去重，重复送达幂等），显式 commit 后 `asyncio.create_task(resume_research_async(...))`，依赖全部取自 `request.app.state`（独立会话/factory、应用级 saver、hub、llm、retrieval）。
4. 恢复后 await_human 回流 critic：四值收敛（evidence_a/evidence_b 取一、both 双方 claim 打 `divergence_flags`、reject 双弃），无新冲突则经成本闸门到 report，run=succeeded 并新建 Report(draft)，推 `run.finished(status=succeeded)`。
5. 恢复护栏：编译、`aupdate_state`、图驱动任一步异常均落 run=failed（error_code=异常类名）并推 failed 终态帧，无静默卡死。

### 3.3 降级口径

未配置 LLM、Provider 异常、8s 超时、脏 JSON（越界/重复 pair_index）四类故障整轮回退启发式，state 置 `critic_degraded=True`，研究主链不中断、run 不 failed。

## 4. REST 契约（冻结于 openapi-m2-2.json）

| 方法与路径 | 语义 | 错误 |
| --- | --- | --- |
| GET `/api/v1/runs/{run_id}/conflicts` | run 下全部分歧，created_at 升序；ConflictResponse 十字段 | 401 无 token；404 不存在/非创建者（不泄漏存在性） |
| GET `/api/v1/conflicts/{conflict_id}` | ConflictResponse + evidence_a/evidence_b（id/title/url/domain/snippet/credibility/source_type/published_at 八项） | 401/404 |
| POST `/api/v1/conflicts/{conflict_id}/verdict` | 写 Verdict + Conflict resolved，返回 `{conflict_id,status,verdict_id}` | 401/404；422 choice 非四值枚举、reason strip 后为空；409 status 已 resolved/abandoned |

`VerdictRequest.additional_note` 可空，纯空白归一为 None；reason 纯空白 422。

## 5. 实时帧契约

- `conflict.detected`：`{type:"conflict.detected", run_id, stage:"critique", payload:{id,run_id,claim,evidence_a_id,evidence_b_id,type,severity,status}}`。字段嵌套 payload，避免与帧 type 同名覆盖（WP-10 已踩坑）。
- `conflict.verdicts`：恢复时由 await_human 推送，`payload.verdicts=[{conflict_id,choice}]`。
- 频道 `runs:{run_id}`，进程内 `RealtimeHub`；两帧均不纳入 OpenAPI paths。

## 6. 报告呈现（AC-16）

- 「冲突与不确定性」分两区：待人工裁决（议题、类型/严重度中文标签、双方口径、双方 Markdown 来源链接）；分歧与局限（both/reject 裁决后保留项，附裁决意见/理由/additional_note）。
- 自动收敛与单边人工裁决只进摘要计数，不渲染待办；发现段对带 divergence_flags 的 claim 附分歧指引。
- 结论段只做发现数/待裁决数/未消解分歧数计数，结构上不回写 claim 文本——被舍弃 claim 在 critic 回流时已移出 report_claims，不可能夹带。

## 7. 与 LLD/契约草案的偏离点

1. LLM 候选召回阈值 0.12（LLD 未定数值）：显著松于 M1 启发式 0.4，因语义冲突（temporal/perspective）常发生在同可信度、弱词面相似证据之间；上限 12 对控 token。
2. 冲突详情证据摘要为八项（FR-7 明文），AC-5 文本写「七项」为枚举笔误，以 FR-7 与机读契约为准。
3. `resume_research_async` 相对 Task 2 增补外层 try 护栏（TR-8.3 要求编译/状态写入失败也可见 failed）。
4. LangGraph checkpoint 系列表由 saver `setup()` 自建并随 schema 管理，不纳入 Alembic 业务迁移；业务侧仅 0002 一个迁移。
5. Conflict 的 LLM 判定 reason（一句中文依据）按 LLD 仅用于日志，不持久化（Conflict ORM 无该列）；面向用户的理由取 Verdict.reason。

## 8. 离线评估结论（FR-15）

`tests/eval/conflict_cases.jsonl`（20 条：12 冲突覆盖四类型各 3 + 8 同议题互补负例），脚本 `scripts/eval_conflicts.py`，2026-09-13 deepseek-v4-flash 实测存档 `tests/eval/conflict_result.json`：

- 语义冲突召回 66.67% ≥ 启发式 50%（TR-9.1 门禁通过）；负例误报率 0% vs 启发式 25%；检出类型准确率 100%。
- 已知短板：temporal 0/3。模型把「双方已明确标注不同时点」判为信息已区分而非冲突；M2-2 不阻塞，列为后续 prompt 迭代项（可在判定原则补「被并列引用到同一论断即为时间错配」的正例）。
- 评估资产不进 CI：tests/eval 无 test_*.py，`pytest tests/eval --collect-only` 收集 0 项。

## 9. 前端 types.ts 逐字段核对记录（TR-10.2）

核对基线：`frontend/src/services/api/types.ts`（WP-10）。

| 契约对象 | 结论 | 明细 |
| --- | --- | --- |
| ConflictResponse | 一致 | id/run_id/claim/evidence_a_id/evidence_b_id/type/severity/status/created_at?/updated_at? 字段名与可空性一致 |
| ConflictSeverity / ConflictStatus | 一致 | low/medium/high；detected/awaiting_human/resolved/abandoned |
| VerdictChoice | 一致 | evidence_a/evidence_b/both/reject |
| VerdictRequest | 一致 | choice、reason（必填非空）、additional_note?；后端 strip 空白、note 空白归一 None 为服务端归一不破坏字段契约 |
| VerdictResponse | 一致 | conflict_id/status/verdict_id |
| ConflictDetailResponse（含 evidence_a/evidence_b 八项） | 后端增量 | 前端 types.ts 未声明详情专用类型，WP-10 页面经证据池按 evidence id 反查；后端额外提供内嵌摘要属超集增量，前端可在 M3 直接消费，无破坏性 |
| WS conflict.detected payload | 一致 | 前端 `Pick<ConflictResponse,'id'|'claim'|'evidence_a_id'|'evidence_b_id'|'type'|'severity'|'status'>` 与后端帧逐字段一致，嵌套 payload |
| WS conflict.verdicts | 一致 | await_human 帧 payload.verdicts 与前端恢复处理对齐 |
| 冲突 type 枚举 | 差异（mock） | WP-10 mock 出现过 `performance_data`，不在后端四值内；以本契约为准，mock 数据切换真实接口时对齐（见 Task 11 联调交接单） |

结论：破坏性差异 0；1 项 mock 枚举差异经交接单跟进；1 项详情内嵌摘要为后端超集增量。

## 10. 测试与门禁

- 全量 pytest 410 passed（含 18 个真实 Postgres 集成/端到端用例：过程落库、冲突链路、冲突 API、裁决恢复），无外部 DB 的 CI 环境全部自动 skip。
- ruff check/format 零新增；mypy 全仓 137（M2-2 新增 app/services、schemas、节点改造文件零新增错误）。
- 集成测试独立 schema（m22_task5/7/8）真隔离：conftest.create_all_in_schema 显式在目标 schema 建表，规避 Inspector 默认查 public 同名表跳过建表的陷阱。

TR-10.3（与 LLD/契约草案一致性 rubric）待独立评审签署。
