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

验收项 AC-1~AC-17 全部由自动化测试或离线评估覆盖，完整基线见本文 §11 附录（原工作稿位于 `.trae/specs/m2-2-critic-convergence/spec.md`，该目录不入库，§11 为其入库快照与团队可见基线）。

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

## 11. 附录：FR / NFR / AC 验收基线（入库快照）

> 本节为工作稿 `.trae/specs/m2-2-critic-convergence/spec.md` 的入库快照（2026-09-13 并入；`.trae/` 目录不进仓库，团队可见基线以本节为准）。工作稿中的 Background/Goals/Non-Goals/Constraints 已由本方案 §1-§2 承接，不重复搬运；以下仅保留验收所需的 FR/NFR/AC 原文要点。

### 11.1 功能需求（FR-1 ~ FR-16）

| 编号 | 要点 |
| --- | --- |
| FR-1 | LLM 语义冲突检测：对「论断+两两证据」输出严格 JSON（议题/四类型/三严重度/一句中文依据），温度 0，单批硬超时 8 秒 |
| FR-2 | 保守降级：LLM 未配置/失败/超时/脏 JSON 四类故障回退 M1 Jaccard 启发式，标记 critic_degraded，主链不中断 |
| FR-3 | 自动收敛：low/medium 按可信度权威收敛（平局保留先出现者），落库 resolved、无 Verdict、不挂起 |
| FR-4 | 挂起门控：high 置 awaiting_human 并落库；同轮有待裁决冲突时 run=paused@critique 并发 run.finished(status=paused) |
| FR-5 | 实时事件：每条 high 一帧 conflict.detected（字段嵌套 payload）；裁决恢复时推 conflict.verdicts |
| FR-6 | GET /runs/{id}/conflicts：全量分歧按创建时间升序；登录 + 创建者，否则 404 |
| FR-7 | GET /conflicts/{id}：内嵌双方证据摘要**八项**（id/title/url/domain/snippet/credibility/source_type/published_at）；404 归属保护 |
| FR-8 | POST /conflicts/{id}/verdict：{choice, reason, additional_note?}，reason 非空、choice 强枚举；写 Verdict 与 Conflict resolved/resolved_at，返回三字段 |
| FR-9 | 裁决状态机：终态重复裁决 409；非法枚举/空 reason 422；不存在/不归属 404 |
| FR-10 | 自动恢复：剩余 awaiting_human>0 保持 paused；=0 时经 AsyncPostgresSaver Command(resume=...) 恢复，回流 critic 直至报告 |
| FR-11 | 四值语义：evidence_a/b 取一、both 并存标分歧、reject 双弃；被舍弃 claim 不得进入结论段 |
| FR-12 | 过程数据落库：sub_questions/evidence 生产路径写 Postgres（ULID 与 state 一致，保证外键），落库失败不静默 |
| FR-13 | 报告显式分歧：「冲突与不确定性」段陈述议题、双方口径与来源、裁决理由/备注，不隐藏、不写成定论 |
| FR-14 | Checkpointer 切换：应用级单例 AsyncPostgresSaver（thread_id=run_id，启动建表）；澄清/裁决两类挂起跨请求可恢复，M1 行为不回退 |
| FR-15 | 离线评估：约 20 条样本集 + 复跑脚本（真实密钥，不进 CI），输出语义检测 vs 词面启发式的召回/误报对照 |
| FR-16 | 契约同步：导出含本批端点的机读 OpenAPI（累积快照）到 docs/contract/，与前端 types.ts 完成字段核对；本方案与代码同 PR |

### 11.2 非功能需求（NFR-1 ~ NFR-6）

| 编号 | 要点 |
| --- | --- |
| NFR-1 | 批判阶段消耗 ≤ 档位 token 预算 30%（CRITIC_BUDGET_RATIO=0.30），触顶即停 |
| NFR-2 | 单 run 批判迭代 ≤ 3 轮（MAX_CRITIC_ITERATIONS） |
| NFR-3 | 判定温度固定 0，同输入同模型结果稳定 |
| NFR-4 | 既有测试全部通过 + 新增分支覆盖；mypy/ruff 零新增告警 |
| NFR-5 | Alembic 迁移可 upgrade/downgrade；checkpointer 自建表不纳入业务迁移 |
| NFR-6 | WS 鉴权、run.finished 终态帧、paused 语义、M1 既有端点行为不回退 |

### 11.3 验收标准（AC-1 ~ AC-17）

| 编号 | 类型 | 判定要点 | 证据 |
| --- | --- | --- | --- |
| AC-1 | rule | high 冲突检出后四件事同时成立：Conflict 落 awaiting_human、run paused@critique、WS conflict.detected、随后 run.finished(status=paused) | 新增单测 |
| AC-2 | rule | 抛错/超时/脏 JSON 三类注入下降级启发式，日志有 degraded，run 不 failed | pytest 用例 |
| AC-3 | rule | low/medium 自动收敛：高可信 claim 保留；Conflict resolved/resolved_at、无 Verdict、run 不挂起 | pytest + DB 断言 |
| AC-4 | rule | 分歧列表：创建者 200 按时间排序；非创建者 404；无 token 401 | API 测试 |
| AC-5 | rule | 分歧详情：双方证据**八项**摘要齐备（注：早期工作稿写「七项」为笔误，以 FR-7 与机读契约八项为准）；不存在 id 返回 404 | API 测试 |
| AC-6 | rule | 合法裁决 200 三字段且 Verdict 含 additional_note；非法 choice/空 reason → 422；重复裁决 → 409 | API 测试 |
| AC-7 | rule | 2 条 high：第一条裁决后仍 paused，第二条后自动恢复至 succeeded，报告含未消解分歧议题与双方来源 | 集成测试 |
| AC-8 | rule | 四值裁决后 report_claims 正确：a/b 取一、both 并存带标注、reject 双弃 | 参数化单测 |
| AC-9 | rule | 新图实例/新请求下 clarify 与 critique 两类挂起均可凭 thread_id 跨请求恢复 | 集成测试 |
| AC-10 | rule | 0001→最新→0001 迁移重放退出码 0；verdicts.additional_note 随迁移增删；checkpoint 表不在迁移内 | 迁移测试 |
| AC-11 | rule | 挂起时 sub_questions/evidence 表行数与 state 一致、ID 对应、外键有效、excluded_by_user 默认值正确 | 集成测试 |
| AC-12 | rule | 全量 pytest（M2-2 为 410）+ mypy + ruff 退出码 0、零新增告警 | 命令输出 |
| AC-13 | rule | 约 20 条样本真实 DeepSeek 评估跑通并产出对照文件（tests/eval/，不进 CI）；语义召回不低于启发式 | eval 资产 |
| AC-14 | rule | openapi-m2-2.json 三端点路径/请求/响应/枚举与前端 types.ts 核对，破坏性差异 0 或有联调反馈记录 | 契约 + 核对记录 |
| AC-15 | rubric | LLM 判定结构化稳定性与合理性（1-5，阈值 ≥4）：严格 JSON 稳定、四类型/严重度合理、依据可读 | 样本逐条记录 |
| AC-16 | rubric | 报告分歧表达质量（1-5，阈值 ≥4）：议题/双方口径与来源/裁决备注层次清晰，结论段不夹带被舍弃 claim | 真实冒烟报告 |
| AC-17 | rubric | 与 LLD §4.2.6/§6.5.6/§6.5.10/§6.6、契约草案 §6.2/§6.3 的架构一致性（1-5，阈值 ≥4）；偏离点须在本方案显式说明（见 §7） | 独立评审对照 |

### 11.4 开放问题关闭情况

| 工作稿 Open Question | 关闭情况 |
| --- | --- |
| 联调排期与前端切换窗口 | 已关闭：《M2-2批判收敛与人机裁决接口交接》单已发出，状态待前端回归 |
| mock 非法枚举 performance_data | 已关闭：交接单 §3.5 要求替换为 perspective，前端回归时执行（本工作包不改前端 mock） |
| 评估集数量与分布 | 已关闭：实际落 20 条（12 冲突 + 8 负例），结果见 §8 与 backend/tests/eval/conflict_result.json |
