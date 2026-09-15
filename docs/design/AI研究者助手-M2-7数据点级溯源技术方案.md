# AI 研究者助手 · M2-7 数据点级溯源阶段技术方案

- 版本：v1.0（实现冻结，2026-09-14；v0.1→v0.2 按评审结论修订 outline/marker 与 fixture 口径差异、quote 正文取数路径、字段计数、dispute 双方均剔除边界；v0.2→v1.0 落地偏差与门禁证据见 §13）
- 范围：结构化报告（conclusion/evidence/dispute/limitation 四类 blocks）的真实产出、论断与信源/原文片段的确定性绑定、`report_citations` 表启用与迁移、新增 `GET /reports/{run_id}/citations`、既有两个报告查询端点的结构化超集扩展、累积契约快照 openapi-m2-7
- 上游依据：《AI研究者助手-软件需求规格说明书》§11 验收 A2「报告每个论断可点击回溯到原始来源 URL 与片段，抽样 ≥10 个论断全部可回溯」、A3「结论/证据/分歧/局限四类区块完整呈现」（A2 首次通过归属本包，A3 为 M2-2 首次通过后的共同回归）；《AI研究者助手-智能体协作规格说明》§3.8「Reporter 按模板生成四类区块；任何论断必须绑定信源，缺源即降级为推断标记」、§5.2 阶段契约表「Reporter → 报告呈现层：报告渲染数据 JSON Schema 化；缺源论断降级为推断」、§6「摘要+引用」上下文压缩、§7 失败与回溯策略表「报告缺源：论断降级为推断标记，兜底不冒充事实」；《AI研究者助手-软件开发计划》§3 M2-7「报告内每个论断自动绑定信源与原文片段，UI 支持点击回溯」、§3 里程碑硬指标「有数字或事实声明的论断 100% 绑定信源；纯推论性段落允许标注推断」、§5.1/§5.2（A2 首次通过归属 M2-7）；《AI研究者助手-后端详细设计》§5.3.9 Report + Citation、§6.2 ResearchState、§6.5.7 reporter、§15.2 M2-7「报告 JSON Schema + report_citations 落库，论断绑定信源与原文片段」；《AI研究者助手-前端详细设计》§10.2 溯源体系数据模型、§10.3 CitationMarker、§11.4 报告页；《AI研究者助手-前端M2任务分解与方案设计》WP-16
- 编号口径：以《软件开发计划》§3 M2 里程碑表为唯一事实源——M2-6 信源元数据抽取 → **M2-7 数据点级溯源**；本包承接 M2-6 已真实化的证据元数据（source_type/source_level/credibility/published_at）作为溯源信源索引的数据面（字段计数口径见 §5.9）
- 契约冻结基线：本包落地前冻结在 `docs/contract/openapi-m2-5.json`（24 路径；M2-6 零契约变化未产快照），本包产出 openapi-m2-7（25 路径）

## 1. 目标与范围

M1 报告链已跑通但停在「纯模板 Markdown」：reporter 节点只拼接四段 Markdown（背景/发现/冲突/结论），论断是 critic 的「1 条证据派生 1 条 claim」转写，无 blocks、无角标、无信源索引；0001 迁移建好的 `report_citations` 表从未写入。前端 WP-16 已按 mock-first 完成 blocks 引擎、CitationMarker、SourcePanel，并以 `frontend/src/services/mock/fixtures/demo_full.ts` 与 `frontend/src/services/api/types.ts` 冻结了消费形态——等的是**真实结构化终稿与溯源索引**。本包交付：

1. **结构化终稿真实产出**：reporter 阶段以一次 LLM 结构化调用（温度 0）把 critic 收敛后的论断、证据池摘要、冲突裁决与局限信号综合为 blocks 计划，经确定性绑定引擎校验补全后产出四类 blocks 与 outline，写入 `reports.content_json`。
2. **论断 100% 绑定信源（硬指标可机器审计）**：LLM 只允许引用当 run 证据池中真实存在的 evidence_id；snippet 一律由后端取自证据原文（禁止模型编造）；对无有效引用的 conclusion 块按 §5.5 互斥顺序判定——不含数字断言者强制降级 `inferred` 并保留文本，含数字断言者经一次自修复仍不达标则从终稿剔除并留痕；dispute 块由引擎确定性注入并结构性挂证据——终稿内「含数字或事实声明的论断」绑定率恒为 100%。
3. **marker 体系**：报告级按证据池顺序为每个被引证据分配唯一角标 `[N]`（同一证据跨 block 共享同一 marker），block 内联三字段引用（evidence_id/marker/snippet），底部信源索引按 N 排序。
4. **report_citations 启用**：blocks 展开为 block×evidence 关系行落库（含 position=marker 序号、block_id、claim_id、snippet），支撑信源索引端点与绑定覆盖率审计；新增 0005 迁移补齐表结构。
5. **溯源索引端点**：新增 `GET /reports/{run_id}/citations`，返回报告级去重信源列表（角标 + URL + 标题 + 片段 + 可信元数据，九展示字段；线上连同关联键 evidence_id 共十字段，计数口径见 §5.9），对齐前端 `ReportCitationItem`。
6. **既有报告端点结构化超集**：`GET /reports/{run_id}` 与 `GET /runs/{run_id}/report` 在现有八字段（id/run_id/template_id/status/content_md/token_used/created_at/updated_at）之上增 `outline` 与 `blocks`（markdown 字段保留），前端 `hasBlocks` 判定后进入 blocks 轨，无需切换路径。
7. **分歧块确定性注入**：dispute 类 block 不经 LLM 文本面产出，由后端按冲突状态机（待裁决 / both/reject 保留分歧）确定性生成并强制挂双方证据与 conflict_id，复用 M2-2 已验证的双方口径渲染逻辑。

### 1.1 非目标（本包明确不做）

- **SSE/WS 报告流式生成**：不恢复 `report.chunk` 流（M1 已移除 SSE），不新增任何 WS 帧；真实后端本就不发 `report.finished` 帧（仅 mock 剧本有），本包不补发，报告页取数时机维持 `run.finished(succeeded)` 后进入拉取（冻结代码 `frontend/src/views/report/ReportView.vue` 已是该口径：succeeded 后拉取、422 轮询）。前端详设 §11.4 与 WP-16 中「report.finished 后切 blocks」的措辞属过期文本，由前端在交接批同步修订为 run 状态驱动（§12 交接项），本包不改其引擎与交互。
- **Markdown 轨角标对齐**：`content_md` 继续由现有模板渲染产出（生成中预览/留档），其「引用」段编号规则不改；终稿阅读以 blocks 为唯一形态，markdown 与 marker 不一致不作为缺陷（前端 M2 终稿不渲染 markdown）。
- **报告版本化/重生成/批注/存疑/导出/分享**：`draft→final→superseded` 生命周期中的重生成与全部 M4 能力不做；`superseded` 枚举保留不产出。
- **LLM 评判信源可信度/向量检索/证据簇聚类**：沿用 M2-6 边界；blocks 综合只消费已标准化的证据与元数据。
- **outline→blocks 显式映射**：前端 `locateOutline` 按 type 首块定位（WP-16 既定），本包不引入 section_id 映射字段。
- **evidence/limitation 块的强制绑定率**：硬指标只约束「论断」（conclusion/dispute，见 §5.5 口径）；evidence 为引用语境块、limitation 允许方法性陈述，不做数字断言审计。
- **历史报告回刷**：只影响新 run；存量 content_json 旧形态（claims/conflicts/outline）行不迁移、不重算（M2 内部试用前数据无保留价值）。
- **OpenAPI 客户端生成**：M2-9 工作包，不在本包。
- **critic claim 产出模型改造**：维持「1 证据 1 claim」作为 reporter 的输入材料；论断的跨源综合发生在 reporter，不回流改 critic。
- **前端引擎与交互改动**：前端 WP-16 已完成并冻结形态，blocks 引擎、CitationMarker、SourcePanel、三索引与双轨判定均不重构；本包以后端真实产出对齐既有消费形态。仅交接批的**跟随项**需要前端处理：mock fixture 的 outline id/标题与 marker 占号（§5.3/§5.8 裁决，demo 数据下输出碰巧无差异）、设计文档与 types.ts 过期注释文本（§12 第 5 条），均为文档/fixture 级修订，不在后端仓改前端文件；若真链发现字段级差异，以本方案冻结契约为准提交接。

## 2. 现状盘点（M2-6 基线）

### 2.1 已具备（复用，不重复造）

| 能力 | 位置 | 现状 |
| --- | --- | --- |
| 结构化消费形态（冻结） | `frontend/src/services/api/types.ts` L410-456、`mock/fixtures/demo_full.ts`、`composables/useReportBlocks.ts` | ReportBlock 四值类型、内联引用 ReportCitation 三字段（types.ts 命名即 ReportCitation，后端对外 Pydantic 同名）、ReportCitationItem 九展示字段（线上连同 evidence_id 共十字段，见 §5.9）、StructuredReportResponse 超集形态均已冻结；前端以 runId 并行拉报告与 citations，建 marker/evidence/conflict 三索引。注意 fixture 的 outline id/标题（sec-1..sec-4）与全量 marker 占号是 mock 旧形态，与本包 §5.3/§5.8 裁决口径不同，交接批前端跟随 |
| 报告表 | `app/db/models/report.py` `Report` | content_json 为 JSONB 且从不对外暴露（ReportResponse 无该字段），可自由承载 blocks 新形态；run 1:1 unique |
| 引文表 | 同文件 `ReportCitation`（0001 已建） | report_id/evidence_id/claim_id(64)/position(int)/snippet + ix_citation_claim；**从未有写入点**，本包启用并以 0005 微调 |
| 论断材料 | `critic.py::generate_draft_claims` 与收敛链 | report_claims 每条含 id/text/confidence/citations[{evidence_id,url,title,snippet}]；冲突裁决四值收敛、both/reject 分歧保留、用户剔除过滤均已闭环 |
| 冲突渲染口径 | `reporter.py::_render_conflict_block/_side_view` | 议题/双方口径/来源链接/裁决意见/理由/局限备注的确定性渲染已在 M2-2 交付并测试，dispute 块文本复用 |
| 证据元数据（M2-6） | Evidence 表 + standardizer | url/title/domain/snippet/source_type 五值/source_level 三级/credibility A~D/published_at 真实有值；excluded_by_user 剔除口径在 reporter/critic 已接 |
| LLM 结构化调用范式 | `critic.py::detect_conflicts_semantic`、`app/provider/client.py` | `complete_structured`（温度 0、硬超时、token 回执）；未配置/异常/超时/脏返回走显式 `critic_degraded` 降级并留痕，假 LLM 注入测试模式成熟（test_critic.py 等） |
| 报告落库点 | `executor.py::_mark_succeeded`（约 L414）及 cancelled keep_partial 分支（约 L570） | 成功写 status="draft" 的 Report；同事务可挂接引文行展开 |
| 报告查询端点 | `api/v1/reports.py`、`api/v1/runs.py` | GET /reports/{run_id}（顶级，参数语义即 run_id，1:1 查报告）与 GET /runs/{run_id}/report；run 不存在/非属主 404、报告未生成 ValidationError(422) |
| 契约导出 | `app/export_openapi.py` | 累积白名单 + `--refresh-mXX` 开关机制；当前 24 路径 |
| state 通道 | `app/orchestrator/state.py` | report_outline/report_claims/report_draft 已在 ResearchState；reporter 节点可获取 deps.llm/db_session/hub |

### 2.2 缺口（本包要补）

1. **blocks 不存在**：content_json 只写 `{claims, conflicts, outline}`，outline 是旧四段（background/findings/conflicts/conclusion），无四类 blocks、无结构化终稿，前端真链只能停在 markdown 轨。
2. **report_citations 零写入**：表空转；无 marker/position 分配器、无 block/claim 维度、无 snippet 固化，A2 点击回溯无后端数据支撑。
3. **表结构差两列**：`claim_id` NOT NULL 无法表达 evidence/limitation 块（fixture 中这两类无 claim_id）；无 `block_id` 列，不支持 block 级关系展开与「按 block 审计缺源」；(report_id, block_id, evidence_id) 无唯一约束。
4. **引用无防编造机制**：若 LLM 直出引文，evidence_id 可能幻觉、snippet 可能改写；当前没有任何绑定校验层。
5. **终稿状态语义**：成功终稿落 `status="draft"`，与 fixture/前端终稿语义（final）及 LLD 生命周期不符；draft 应只属于取消时保留的部分草稿。
6. **契约端点缺一个**：citations 索引端点不存在（mock 路由 `GET /api/v1/reports/{run_id}/citations` 已先行）。

## 3. 组件与代码落位

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| 报告结构化领域包 | `app/reporting/__init__.py`（新增） | 新领域包，与 `app/retrieval/` 同级；blocks 生成与绑定逻辑集中，不散落在节点内 |
| LLM 输出 schema 与 prompt | `app/reporting/schemas.py`、`app/reporting/prompts.py`（新增） | LLMBlockDraft/LLMReportPlan 内部 Pydantic 模型（三值 block，不含 dispute）；中文 system/user prompt 构造（材料压缩为摘要+引用，SDP §6） |
| blocks 绑定引擎 | `app/reporting/blocks.py`（新增） | 纯函数：marker 分配、引用白名单校验、snippet 原文填充与 quote 校验、缺源降级、数字断言审计与自修复判定输入、dispute 注入、outline 派生、id/claim_id 生成、审计摘要、citation 关系行展开。正文不入 state（§5.4），由调用方传入 `content_map: dict[evidence_id, EvidenceRow]`（至少含 content/snippet），引擎不持有会话 |
| reporter 节点接入 | `app/orchestrator/nodes/reporter.py`（改造） | Markdown 渲染链保留；新增：按材料池 evidence_id 用 deps.db_session 批量查 Evidence 行组装 content_map（db_session 缺省的测试/未配置路径以 claim citations 与证据字典中既有 snippet 构造）→ 取 deps.llm 调结构化生成 → 绑定引擎 → state 返回 report_blocks；llm 为 None/失败走机械映射降级并标 reporter_degraded（§5.7） |
| state 类型 | `app/orchestrator/state.py`（改造） | ResearchState 增 `report_blocks: NotRequired[list[dict]]`、`reporter_degraded: NotRequired[bool]` |
| 落库 | `app/orchestrator/executor.py`（改造） | `_mark_succeeded`：content_json 改新形态（outline/blocks/citation_audit）、status 改 final、同事务展开写 ReportCitation 行；keep_partial 的 draft 分支维持旧快照形态、不写引文 |
| ORM/迁移 | `app/db/models/report.py`、`app/db/migrations/versions/0005_report_citations_blocks.py`（新增） | claim_id 改 nullable、加 block_id NOT NULL、唯一约束与 position 索引（§7）；alembic head 0005 |
| API schema | `app/schemas/reports.py`（改造） | ReportResponse 增 outline/blocks（默认空列表，draft 旧行为兼容）；新增 ReportOutlineItem/ReportBlock/ReportCitation（block 内联三字段，与前端 types.ts 同名）/ReportCitationItem |
| service | `app/services/reports.py`（新增） | get_report_for_run（归属校验+报告存在性，两端点共用）、list_citations（join Evidence 拼装九展示字段与 evidence_id 关联键、报告级按 position 去重排序） |
| 路由 | `app/api/v1/reports.py`、`app/api/v1/runs.py`（改造） | reports 顶级加 GET /{run_id}/citations；两查询端点切 service 并回结构化超集 |
| 配置 | `app/core/config.py`、`.env.example` | 报告 LLM 超时/输出上限（§10） |
| 契约 | `app/export_openapi.py`、`docs/contract/openapi-m2-7.json` | 新增 `_M27_ENDPOINTS`（1 路径）与描述常量，M2-5 快照转默认冻结 + `--refresh-m25`；累积 25 路径 |
| 测试 | `tests/test_report_blocks.py`（新增）、`tests/test_reporter.py`/`tests/test_runs_api.py`/`tests/test_executor.py`（更新）、`tests/test_integration_m27.py`（新增真库） | AC 见 §9；假 LLM 注入沿用 test_critic 模式 |
| 文档 | 本方案升 v1.0、`backend/README.md` 回填、`docs/feedback/M2-7数据点级溯源接口交接.md` | 落位遵循《项目人员协调操作规约》；LLD §5.3.9 模型偏差与 §6.2 ResearchState 新字段（report_blocks/reporter_degraded）在实现批原子同步（§8） |

## 4. 任务分解与实现顺序

### T1 契约 schema 与绑定引擎纯函数（前置，无外部依赖）

`app/reporting/blocks.py` + `schemas.py`：marker 分配、id 生成、引用校验/去重、snippet 填充（quote 子串校验，正文经 content_map 入参传入，引擎不碰会话）、outline 派生、审计摘要、关系行展开；`app/schemas/reports.py` 四组对外模型。`tests/test_report_blocks.py` 假数据驱动全部分支。

### T2 0005 迁移与 ORM（独立）

ReportCitation 模型改 claim_id nullable + 加 block_id/约束/索引；alembic 升级/回滚脚本；test_models 与迁移结构用例更新。真库迁移循环验证在 T5。

### T3 LLM 结构化生成与 reporter 接入（依赖 T1）

prompts.py + LLMReportPlan 调用（温度 0、超时/token 配置化、token 回执计入用量）；节点先批量取 Evidence.content 组装 content_map（§5.4）；含数字断言违规块的一次自修复；dispute 确定性注入（复用 reporter 现有冲突口径）；llm 缺失/异常/脏返回走机械映射降级并打 reporter_degraded；state 与 test_reporter 更新（假 LLM 替身，离线不打真实网络）。

### T4 落库、service 与端点（依赖 T1/T2/T3）

executor 成功路径：final + content_json 新形态 + 引文行同事务展开；`services/reports.py`；reports/runs 两端点超集；GET citations 新端点；test_executor/test_runs_api 更新（含 404/422、draft 空列表、marker 排序与信源索引字段计数）。

### T5 真库集成、契约冻结与门禁

`tests/test_integration_m27.py`（m27_orm/m27_migration 双 schema：建表/迁移往返、blocks→引文落库→独立会话读回→信源索引端点九展示字段形态、100% 绑定审计）；export_openapi 25 路径导出 openapi-m2-7 并与前端 types.ts 逐字段核对（outline id/标题与 marker 占号差异按 §8 第 7 条裁决核对）；全量 pytest、本批 ruff、mypy 基线零新增；方案升 v1.0、README/交接单。

## 5. 关键设计

### 5.1 结构化终稿总链路

```text
critic 产物（state）
  ├─ report_claims（收敛后论断，含 citations[evidence_id/title/snippet]）
  ├─ conflicts + verdicts（含 both/reject 保留分歧、additional_note）
  └─ standardized_evidence（M2-6 元数据真实化；已剔除 excluded）
        │  reporter 节点
        ├─ 组装材料（question/clarification + claim 摘要 + 证据摘要池 + 局限信号）
        ├─ 按池内 evidence_id 批量查 Evidence 行 → content_map（content/snippet；
        │     正文不入 ResearchState，仅作引擎入参，§5.4）
        ├─ LLM complete_structured（温度 0）→ LLMReportPlan
        │     blocks[type∈conclusion/evidence/limitation, text, confidence,
        │             evidence_ids[], quotes{evidence_id: 原句}?]
        ├─ 数字断言违例？ → 带反馈一次整份自修复（仅一次）
        ├─ 绑定引擎（确定性，app/reporting/blocks.py）
        │     ├─ evidence_id 白名单过滤（幻觉引用剔除，块内去重）
        │     ├─ marker 报告级分配（§5.3）+ snippet 后端填充（§5.4）
        │     ├─ 缺源降级/数字违例剔除（§5.5）
        │     ├─ dispute 块确定性注入（§5.6，不经 LLM）
        │     ├─ block/claim id 生成 + outline 派生（§5.8）
        │     └─ citation_audit 审计摘要
        ├─ Markdown 旧链同时产出 report_draft（保留，不改）
        └─ state.report_blocks → executor 落库
              ├─ reports.status=final，content_json={outline, blocks, citation_audit}
              └─ report_citations 展开（block×evidence 行，position=marker 序号）
```

### 5.2 LLM 输入输出契约（只信证据池，不信用模型自由发挥）

输入（system/user 中文 prompt，JSON 序列化材料）：

- 研究问题与澄清目标/范围；
- 证据摘要池：每证据仅 `id/domain/title/snippet/credibility/published_at`（不传 content 全文，遵循 SDP §6「摘要+引用」上下文压缩）；
- 收敛论断：text/confidence 与其 evidence_id 列表；
- 局限信号：both/reject 分歧要点与 additional_note、待裁决冲突数、被用户剔除数、元数据缺失情况（无发布日期/D 级证据计数）。

输出（Pydantic 校验，温度固定 0）：

```text
LLMReportPlan
  blocks: list[LLMBlockDraft]
LLMBlockDraft
  type: conclusion | evidence | limitation      # 无 dispute（§5.6）
  text: str                                       # 中文，1~3 句，长度上限配置
  confidence: single_source | cross_verified | inferred
  evidence_ids: list[str]                         # 只能引用池内 id
  quotes: dict[str, str] = {}                     # 可选：论断依据的证据原句
```

Prompt 硬约束（写入 system 段）：只可引用给定证据 id；结论/事实/数字必须有 evidence_ids；无来源的综合判断只能标 inferred 且不得写具体数字/日期/排名；不得编造 URL、机构名与数据；按 conclusion（综合结论）/evidence（关键证据展开）/limitation（研究局限与不确定性）归类；分歧相关内容不要自行下结论（后端统一产出 dispute 块）。

脏返回/校验失败（空 blocks、text 全空、字段越界）按 §5.7 降级处理，与 critic 语义检测同一范式。

### 5.3 marker 编号（报告级、按证据池、跨块共享）

- 顺序来源：reporter 渲染视图内的 `standardized_evidence`（已过滤用户剔除项，保持 standardizer 既有确定性顺序：跨子问题、credibility→relevance），按序为**被引用到的证据**分配 `[1]…[N]`；未被任何 block 引用的证据不占号。
- 同一证据被多个 block 引用时 marker 相同（fixture 同形态：`citationByMarker` 为一对一 Map，底部信源索引按 N 排序）；block 内同一证据只出现一次（LLM 重复给号去重）。
- marker 字符串由后端从 position 格式化（`f"[{n}]"`），不接受 LLM 提供；position 即 report_citations.position（整数，报告内从 1 起）。
- 稳定性：编号只依赖材料池顺序与引用集合，二者同一次 run 确定；cancelled 后 resume 重跑 reporter 视为新报告内容，编号以最终终稿为准（M2 无版本化）。
- 与 fixture 的差异（评审裁决）：mock `demo_full.ts` 的 `CITATION_MARKERS` 为全部证据按池顺序全量占号（[1]..[12]）、citations 返回全量 12 条；本包以**仅被引证据占号**为权威口径，citations 端点返回被引去重列表，真链信源条数 ≤ 证据总数。demo 剧本中 ev01~ev12 恰好全部被 blocks 引用，两种规则输出碰巧一致；真链出现未被引用的证据时 marker 会重排。前端只渲染后端返回的 marker 与列表、无硬编码编号，运行层零改动；fixture 的占号规则与 outline（§5.8）在交接批跟随修订（§8 第 7 条）。

### 5.4 snippet 一律后端取自原文（防编造）

LLM 不给 snippet，只给 evidence_ids 与可选 quotes。正文 `content` 不在 ResearchState/LLM 输入中（state 的 EvidenceDict 无该字段，遵循 SDP §6 摘要压缩），由 reporter 节点在调用引擎前按 evidence_id 批量查询 Evidence 行组装 content_map 传入（§3/§5.1），引擎按下述规则取值：

1. 提供了 quote 且在 content_map 中该证据 `content`（trafilatura 正文，可能为空）做**规范化子串匹配**（统一空白、去除首尾差异后连续包含）命中 → snippet 取命中句及其所在句的有限窗口（上限 ~120 字），实现「数据点→原文原句」的最细粒度回溯；
2. 未提供 quote、content 缺失（含 db_session 缺省未能查库的路径）、或匹配未命中 → snippet 取证据既有 `Evidence.snippet`（检索摘要，本就是来源原文片段；state 证据字典与 claim.citations 中均带该值），并在该块引用留痕 `quote_verified=false`（仅入 content_json 内部审计，不进响应）；
3. quote 命中与否**都不丢弃该引用**（绑定成立，片段粒度降级为摘要级），不做网络补抓、不做模糊改写。

block 内联引用响应三字段固定为 `{evidence_id, marker, snippet}`（对齐前端 types.ts 的 `ReportCitation`，后端 Pydantic 同名）；citations 端点中同一证据的 snippet 与其报告级首块一致。

### 5.5 缺源降级与 100% 绑定审计（硬指标的机器化口径）

事实性论断集合 = 全部 `conclusion` 与实际注入的 `dispute` 块（dispute 为确定性注入，除「双方证据均被剔除」外结构性挂双方/存活方引用，见 §5.6）。绑定引擎对过滤掉幻觉/重复引用后的块按以下**互斥**顺序判定：

1. **含数字断言的 conclusion 块无有效引用**（数字断言模式：正则覆盖阿拉伯/中文数字、百分比、金额单位 万/亿/元/$、倍数、具体年份日期、排名序数等）→ 进入一次自修复（把违规块文本回传，要求补池内引用或删除数字断言，整份重产一次）；自修复后仍无引用 → **从终稿剔除**（不输出无来源数字，SDP §7「不冒充事实」），计入 `dropped_numeric_blocks`；
2. **不含数字断言的 conclusion 块无有效引用** → confidence 强制置 `inferred`（SDP §3.8 缺源即推断），文本保留（纯推论允许呈现，WP-16 渲染「推断」标签），计入 `forced_inferred_blocks`；即终稿中的 inferred 块必然不含数字断言；
3. 有有效引用的块保留 LLM 给定 confidence（引用集合变化不反推降级，cross_verified 的判定依据是 critic 收敛结论而非引用计数）；
4. evidence/limitation 块不参与数字断言审计（非论断块，§1.1）；limitation 文本可无引用。

落库前生成审计摘要写 `content_json.citation_audit`（内部字段，不进 REST）：

```json
{
  "claim_blocks": 10,
  "bound_blocks": 9,
  "forced_inferred_blocks": 1,
  "dropped_numeric_blocks": 0,
  "skipped_disputes": 0,
  "hallucinated_refs": 2,
  "quote_verified_refs": 7,
  "numeric_claim_binding_rate": 1.0
}
```

口径：claim_blocks = 终稿全部 conclusion/dispute 块数（被剔除块不计入），恒等于 bound_blocks + forced_inferred_blocks；numeric_claim_binding_rate = 终稿含数字断言的论断块中有引用者占比，按规则 1 恒为 1.0；skipped_disputes = 冲突双方证据均被用户剔除而未注入 dispute 块的条数（§5.6），单独留痕、不进 claim_blocks 恒等式。「有数字/事实声明的论断 100% 绑定信源」由此可机器验证，PRD A2 的 ≥10 论断人工抽样在交接真链环节执行。

### 5.6 dispute 块确定性注入（分歧不托付模型）

对每条「待人工裁决」冲突（status detected/awaiting_human 且无 verdict）与裁决后保留分歧（verdict.choice ∈ {both, reject}），引擎各生成一个 dispute 块：

- `type="dispute"`、`claim_id` 按序生成、`conflict_id=冲突id`；双源齐时 `confidence="cross_verified"`（双源并呈，与 fixture cf1 块一致；仅存活方时按下方规则降级）；
- `citations` 强制为冲突双方证据（evidence_a_id/evidence_b_id，去重；任一证据已被剔除时只挂存活方并在文本说明）；
- **双方证据均被用户剔除**：该冲突不具备任何有效溯源载体，**不注入 dispute 块**（避免产出零引用的论断块破坏 §5.5 恒等式），计入 `citation_audit.skipped_disputes`；该冲突仍可经分歧列表端点查看，不在本包报告呈现范围；
- 文本由后端确定性拼接：议题、双方口径与来源、裁决状态/意见/理由/additional_note——直接复用 `reporter.py::_side_view/_render_conflict_block` 已验证口径，压成单段文本；仅挂存活方时 confidence 随引用实际情况置 `single_source`，双源齐才用 `cross_verified`；
- 冲突状态以 DB/state 为准，LLM 输出中即使涉及分歧也不生成 dispute 块（schema 已禁止该枚举），LLM 写的分歧叙述若含数字断言按 §5.5 同规则处理。

### 5.7 LLM 不可用时的显式降级（reporter_degraded）

沿用 M2-2 critic 的既定范式（项目一致模式，非静默兜底）：deps.llm 为 None、调用异常/超时、结构化校验失败或自修复后仍为空 →

1. 机械映射产出结构完整的 blocks：每条收敛 claim 转一个 conclusion 块（text=claim.text、confidence 沿用、引用挂其 evidence_id），待裁决/保留冲突按 §5.6 注入 dispute 块；无 claim 时至少产出一个含研究问题的概述块；
2. 该路径下所有引用均真实（材料就是 claim 自身的 citations），绑定率 100% 天然成立，文本不做跨源综合（质量降级、结构不降级）；
3. state 与 content_json 标 `reporter_degraded=true`（内部留痕），token 消耗如实计 0。

不引入「禁用 LLM」配置开关：测试经 deps 注入假 LLM（test_critic 同模式，conftest 不打真实网络），生产默认真 LLM；None 路径仅服务测试与未配置环境。

### 5.8 block/claim 标识与 outline 派生

- block.id：`block-01` 起两位顺序号（fixture 同形态），按终稿 blocks 顺序；
- claim_id：conclusion/dispute 块按序 `claim-01…`（报告级论断标识，不外露 critic 的 ULID）；evidence/limitation 块不带 claim_id（0005 后该列 nullable）；
- outline 从终稿 blocks 确定性派生（id/标题为后端权威口径，标题固定中文）：
  - `sec-overview` 调研概述（type=conclusion，始终）；
  - `sec-findings` 核心发现（type=conclusion，始终）；
  - `sec-disputes` 分歧与不确定性（type=dispute，仅当存在 dispute 块）；
  - `sec-limitations` 局限与建议（type=limitation，仅当存在 limitation 块；LLM 未产出任何 limitation 块时引擎补一个局限块，提示证据范围与未复核项，保证 A3 局限区块不缺位）。
- 与 fixture 的差异（评审裁决）：mock `demo_full.ts` 现用 id `sec-1..sec-4`、标题随 demo 内容命名（架构与索引机制/规模化性能与数据分歧等）且固定 4 条；本包以语义 id 与上表固定标题为准、分歧/局限章按需出现（2~4 条）。前端 `locateOutline` 只消费 type，id/标题改动运行层零影响；fixture outline 在交接批跟随修订（§8 第 7 条）。
- outline 仅 {id,title,type}，不建立 section→blocks 映射（§1.1）；content_json.outline 写新形态，state.report_outline 旧四段仅供 markdown 渲染，不改名不删。

### 5.9 落库与读取

成功路径（executor._mark_succeeded 同事务）：

1. Report 行：status 改 `final`；content_md 照旧；content_json = `{"outline": [...], "blocks": [...], "citation_audit": {...}}`；
2. flush 取 report.id 后，把每个 block 的 citations 展开为 ReportCitation 行：(report_id, block_id, evidence_id, claim_id 可空, position=marker 序号, snippet)；块×证据粒度，同块同证据唯一（0005 约束）；
3. cancelled keep_partial 分支：report 节点未执行时 state 无 report_blocks，维持 draft + 旧快照 content_json，不写引文（draft 无溯源语义正确）；
4. citations 端点：join Evidence 按 report 取行，Python 侧按 position 去重（每 marker 一条）并按 N 升序。响应元素线上共 **10 字段** = 关联键 `evidence_id` + **9 展示字段**（marker/snippet/url/title/domain/source_type/source_level/credibility/published_at）：evidence_id/marker/snippet/url/title 必填；domain/source_type/source_level/credibility 按前端 types.ts 可选声明、M2-6 后实际总有值；published_at 可 null。即九展示字段中除 published_at 外八者总有值。全文「九字段/九展示字段」均指不计 evidence_id 的展示口径；
5. 错误口径：run 不存在或非创建者 → 404 NotFoundError；报告未生成 → 422 ValidationError（与现有 /reports/{run_id} 及 mock 路由一致）；报告为 draft 时端点正常返回空 citations 列表（不报错）。

## 6. 契约影响（openapi-m2-7，25 路径）

新增 1 个 REST 路径（WS 零变化）：

| 方法/路径 | 说明 | 主要错误 |
| --- | --- | --- |
| GET `/api/v1/reports/{run_id}/citations` | 报告级信源索引（按 marker 升序、去重）；元素为 ReportCitationItem | run 不存在/非属主 404；报告未生成 422 validation_error |

既有两个报告路径 schema 超集扩展（非新路径，同路径响应增字段）：

- `GET /api/v1/reports/{run_id}`、`GET /api/v1/runs/{run_id}/report`：响应在现有 id/run_id/template_id/status/content_md/token_used/created_at/updated_at 八字段之上增 `outline: ReportOutlineItem[]`、`blocks: ReportBlock[]`（draft/旧报告为两空数组，不破坏既有八字段消费者）；
- 路径参数语义统一说明：顶级 `/reports/{run_id}` 的路径参数历史命名即 run_id（run↔report 1:1），前端 `getReportCitations(reportId)` 实参传 runId、mock 同路径同语义，本包按 run_id 命名冻结，不新增按 report.id 查询的路径。

与前端 `frontend/src/services/api/types.ts` 的逐字段核对结论在 T5 导出后写入交接单，字段名/枚举/可选性目标差异为 0；字段可选性以前端冻结形态为准（block.id 在本包终稿恒有值、响应中按必填产出，前端可选声明不拒绝）。outline id/标题与 marker 占号不属于 schema 字段差异，而是 mock fixture 的样例数据差异，按 §8 第 7 条由前端在交接批跟随，不作为后端契约偏差。

## 7. 数据模型与迁移（0005）

`report_citations` 表 0001 建成后从未写入（空表），0005 直接改结构，无数据回填：

- `claim_id`：String(64) NOT NULL → **nullable**（evidence/limitation 块无论断标识）；
- 新增 `block_id`：String(64) **NOT NULL**（block 级溯源与按块审计的必备维度）；
- 新增唯一约束 `uq_citation_block_evidence (report_id, block_id, evidence_id)`（块内引用不重复）；
- 新增索引 `ix_report_citations_position (report_id, position)`（信源索引按 marker 排序读取）；
- 既有 `ix_report_citations_report_id`、`ix_citation_claim(report_id, claim_id)` 保留；
- downgrade：drop 约束与两索引、drop block_id、claim_id 恢复 NOT NULL（空表回退安全）；
- 迁移后 alembic head = **0005**，真库双 schema（m27_orm / m27_migration）验证 upgrade/downgrade 往返。

## 8. 与 LLD / 前端设计的偏离点

1. **ReportCitation 增 block_id、claim_id 可空**：LLD §5.3.9 原表无 block 维度且 claim_id NOT NULL；WP-16 冻结的 block 模型中 evidence/limitation 块无 claim_id，且数据点级审计需要 block 粒度。实现批原子同步修订 LLD §5.3.9；同批同步 LLD §6.2 ResearchState（state.py 文件头注释要求增删字段同步文档），补 `report_blocks`/`reporter_degraded` 两字段（《项目人员协调操作规约》文档同步矩阵：阶段方案与实现同 PR）。
2. **reporter 由纯模板变为「LLM 综合 + 确定性绑定」双段**：LLD §6.5.7 目标态是逐 section 流式生成（stream_section）；本包采用一次 complete_structured 出 blocks 计划 + 确定性引擎绑定（非流式、温度 0），流式报告明确列为非目标（§1.1），与 M2 既有「run 状态驱动生成中态」口径一致。
3. **dispute 块不允许 LLM 产出**：LLD 叙述 Reporter 统一生成四区块；本包把分歧文本收归确定性注入，理由是冲突状态机/双方口径的准确性已有 M2-2 专门链路，模型再写只会产生漂移。
4. **终稿 status 落 final**：现实现成功报告写 draft；本包按 LLD 生命周期语义纠正（draft 仅留给取消保留的部分草稿），与 fixture status=final 一致。
5. **outline 新四段语义 id 形态**：采用 sec-overview/sec-findings/sec-disputes/sec-limitations 语义 id 与固定中文标题（分歧/局限章按需出现），替代旧 background/findings/conflicts/conclusion；旧 outline 仅留作 markdown 渲染内部数据。
6. **含数字无来源块的处置是「一次自修复 + 剔除」而非节点失败**：不把单块绑定问题升级为整个 run 失败（终稿恒等式由引擎保证，质量信号进 citation_audit）；该口径是对 SDP §7「缺源降级推断、不冒充事实」在数字断言场景的具体化，已经 v0.1 评审确认。
7. **outline 样例形态与 marker 占号规则以后端新口径为准，mock fixture 交接批跟随（v0.1 评审裁决）**：fixture 现状 outline 为 `sec-1..sec-4`/内容化标题/固定 4 条、marker 为全证据全量占号（§5.3/§5.8）；本包终稿用语义 id + 按需章节 + 仅被引证据占号。这些是 mock 样例数据而非 types.ts schema 差异（前端只消费 type、marker 字符串与后端列表，运行层零改动），由前端在 M2-7 交接批修订 `demo_full.ts` 并同批清退 types.ts L430/L440-443、useReportBlocks 中「M2-7 冻结前/后」过期注释；前端详设 §11.4 与 WP-16 的「report.finished 后切 blocks」措辞同步改为 run.finished(succeeded) 驱动。
8. **quote 校验正文不经 state、由节点查库注入**：LLD §6.2 EvidenceDict 无 content 字段（SDP §6 摘要压缩），而 §5.4 quote 子串匹配需要 trafilatura 正文；本包不把正文加入 ResearchState，由 reporter 节点批量查 Evidence 组装 content_map 传给纯函数引擎，LLD 无需为此加 state 字段。

## 9. 测试与门禁

离线全假替身（假 LLM 注入，不打真实网络）；真库用例独立 schema（前缀 `m27_`），setup/teardown 各一次 DROP SCHEMA CASCADE。

| 编号 | 判定要点 |
| --- | --- |
| AC-1 | marker：按证据池顺序为被引证据分配 [1]..[N]，跨块共享、块内去重、未引证据不占号；字符串仅由后端格式化 |
| AC-2 | 引用白名单：LLM 给出不存在的 evidence_id 一律剔除并计入 hallucinated_refs；其余字段（snippet/marker）不采信模型输出 |
| AC-3 | snippet：quote 在 content_map 的 content 规范化子串命中 → 原句窗口且 quote_verified=true；无 quote/content_map 无正文（含 db_session 缺省）/不命中 → 回证据 Evidence.snippet 且标记 false；两种情形引用均保留 |
| AC-4 | 缺源降级：不含数字断言的 conclusion 无有效引用 → confidence 强制 inferred 且保留文本（原 confidence 为 cross_verified 也必须改判）；含数字断言者按 AC-5 路径处理，不进入 inferred |
| AC-5 | 数字断言审计：含数字/百分比/金额/年份模式的无引用 conclusion 触发一次自修复；重产合规则保留；仍违规则块被剔除且 dropped_numeric_blocks 计数；终稿含数字的 conclusion/dispute 绑定率恒 100% |
| AC-6 | dispute 注入：每条待裁决冲突与每条 both/reject 保留分歧各得一个 dispute 块，带 conflict_id、claim_id、cross_verified 与双方证据引用；文本含议题与裁决口径；一方证据被剔除时挂存活方、文本说明且 confidence 降 single_source；双方证据均被剔除时不生成块且 skipped_disputes 计数；LLM 输出 dispute 枚举直接判脏返回走降级 |
| AC-7 | blocks/outline 形态：四类 type 枚举与前端一致；id=block-NN、conclusion/dispute 带 claim-NN；limitation 缺失时引擎补一块；outline 用语义 id（sec-overview/sec-findings/sec-disputes/sec-limitations）且仅在有对应类型块时出分歧/局限章（2~4 条） |
| AC-8 | 降级路径：llm=None/异常/超时/空计划/脏返回 → 机械映射 blocks（claim→conclusion + dispute 注入），结构完整、引用全部真实、reporter_degraded=true、token 计 0；无 claim 时仍有概述块 |
| AC-9 | 落库：成功终稿 status=final、content_json 含 outline/blocks/citation_audit；引文行按 block×evidence 展开，position/block_id/claim_id/snippet 正确，同块同证据不重复；keep_partial 的 draft 不写引文、维持旧 content_json |
| AC-10 | API：GET /reports/{run_id}/citations 元素线上 10 字段（evidence_id + 九展示字段）齐全、仅含被引证据、按 marker 升序去重；两报告端点含 outline/blocks 超集且旧八字段不变；404（run 不存在/非属主）、422（报告未生成）、draft 报告返回 200 空列表 |
| AC-11 | 真库集成：m27_orm 下 blocks→引文写入→独立会话读回与端点形态一致；m27_migration 下 alembic upgrade 至 0005 后列/约束/索引存在、downgrade -1 回 0004 后消失；跑后两 schema DROP 无残留 |
| AC-12 | 契约冻结：export_openapi 输出 25 路径（新增 1），openapi-m2-7 与前端 types.ts 的 ReportBlock/ReportCitation/ReportOutlineItem/ReportCitationItem 字段名/枚举/可选性差异为 0；M2-5 转默认冻结；outline 样例 id/标题与 marker 占号属 fixture 跟随项（§8 第 7 条），不计入契约字段差异 |
| AC-13 | 全量 pytest 零回退；本批变更文件 ruff check/format 全净；mypy 相对 dev 基线零新增（worktree 对比） |

## 10. 配置项变更

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `REPORT_LLM_TIMEOUT_SECONDS` | 45 | 结构化终稿单次调用硬超时（秒）；报告为终节点，宽于 critic 冲突判定的 8s；超时走 §5.7 降级 |
| `REPORT_LLM_MAX_TOKENS` | 4096 | blocks 计划输出上限（中文 10~15 块约 3k token，留余量） |
| 温度 | 固定 0（不开放配置） | 与意图路由/critic 判定口径一致，结构化终稿要求可复现 |

## 11. 风险与对策

| 风险 | 对策 |
| --- | --- |
| LLM 漏绑/幻觉证据 id | 白名单强校验 + 幻觉计数留痕；snippet 只从后端证据取；数字块一次自修复 + 剔除兜底，终稿恒等式可机器断言（AC-5） |
| LLM 综合文本与 critic 结论冲突或自创数字 | prompt 限定只能基于摘要池写作；争议文本以确定性 dispute 块为准；真链抽验 ≥10 论断纳入交接单人工核对（A2） |
| quote 子串匹配命中率低（正文抽取质量/模型改写） | 不命中静默降级到 Evidence.snippet（仍为来源片段，A2 URL+片段可回溯不受损）；quote_verified 率入审计观察，不做模糊匹配硬凑；db_session 缺省路径拿不到 content 时等同未命中处理并在真链确认生产路径必带会话 |
| 单次 LLM 调用拖慢终稿 | 45s 硬超时 + max_tokens 上限；材料只传摘要不传正文；失败显式降级仍出完整终稿，run 不失败；耗时纳入交接实测项 |
| 机械降级的终稿可读性差 | 降级仅发生在 LLM 不可用环境（测试/未配置/故障），reporter_degraded 留痕可观测；内部试用期若真链频繁触发按故障处理而非接受为常态 |
| dispute 注入与 LLM limitation 叙述重复 | 近重复合并不做（M3 边界）；dispute 文本是状态机权威口径，重复不影响绑定正确性；fixture 已接受 limitation 回指分歧的形态 |
| 0005 改空表结构的迁移假设 | 落地前以 SQL/grep 再次确认 report_citations 无任何写入点与历史行（当前仅模型/迁移/测试引用）；空表直接改结构、**不写回填逻辑、不做旧数据兼容**（与 §1.1 历史不回刷口径一致）；真库 downgrade 往返验证 |
| 前端误依赖 mock 的 report.finished 帧 | 本包不补发该帧（WS 零变化）；冻结代码已是 run.finished(succeeded) 驱动（§1.1），交接单列两项：① 真链确认 succeeded 后并行三拉取（报告/citations/conflicts）可用；② 前端详设 §11.4、WP-16 的「report.finished 后切 blocks」过期措辞由前端同批修订（§8 第 7 条） |
| content_json 新旧形态并存 | final 新形态、draft(partial) 旧形态；响应层 outline/blocks 缺省空数组；历史行不回刷（§1.1） |

## 12. 交付与交接清单

1. 代码：T1~T5 全部文件（§3 落位表）、配置与 `.env.example`；新增依赖：无（complete_structured 已在用）；
2. 迁移：0005 落地后联调环境须执行 `alembic upgrade head`（head 由 0004 变 0005）；
3. 契约：重新生成 `docs/contract/openapi-m2-7.json`（25 路径，派生产物禁手改），与前端 types 逐字段核对；
4. 文档：本文件评审通过后先单独提交 dev（文档先行）；实现冻结后升 v1.0 回填 §13；实现批原子同步 LLD §5.3.9 与 §6.2（report_blocks/reporter_degraded）；`backend/README.md` 里程碑行回填（并顺手刷新文首「当前里程碑」过期描述）；
5. 联调：`docs/feedback/M2-7数据点级溯源接口交接.md`，重点为真链 run 的 blocks 四类型分布、≥10 论断角标 URL+片段回溯（A2）、四类区块完整（A3）、citations 信源索引（evidence_id + 九展示字段）与 SourcePanel/ConflictBlock 联动、未被引证据不占号的重排实例、report.finished 帧差异确认；交接单同时列明前端跟随项并由前端回归关闭：① `demo_full.ts` outline 改语义 id/固定标题/按需章节、marker 改仅被引占号；② types.ts L430/L440-443 与 useReportBlocks 中「M2-7 冻结前/后」过期注释清退；③ 前端详设 §11.4 与 WP-16 取数时机措辞改 run.finished(succeeded) 驱动；回归关闭后移入 archive/；
6. 不在本包的动作（流式报告、版本化/批注/导出/分享、证据簇、历史回刷、M2-9 客户端生成、前端 WP-16 引擎/交互重构）以 §1.1 为准流转后续里程碑，不临时扩包；第 5 条所列前端 fixture/文档跟随项是本包交接的组成部分，不属于扩包。

## 13. 实现冻结记录（v1.0，2026-09-14）

### 13.1 落地清单（对照 §3 落位表）

| 组件 | 文件 | 结论 |
| --- | --- | --- |
| 报告结构化领域包 | `app/reporting/__init__.py`（新增） | 领域包建立，与 retrieval 同级 |
| LLM 输出 schema | `app/reporting/schemas.py`（新增） | LLMBlockDraftModel/LLMReportPlan；type 三值无 dispute、text 1~800、evidence_ids/quotes |
| Prompt | `app/reporting/prompts.py`（新增） | 中文 system 六硬约束 + JSON 材料 user 消息 + 数字违规自修复消息；入参精确 TypedDict |
| 分歧原语 | `app/reporting/disputes.py`（新增） | 标签常量、select_disputes、evidence_side、compose_dispute_text；reporter Markdown 渲染改为复用本模块（去重不双写） |
| blocks 绑定引擎 | `app/reporting/blocks.py`（新增） | DraftBlock/EvidenceText/ReportAssembly；marker 池序仅被引占号、白名单去重、quote 原文窗口、缺源降级/数字剔除、dispute 注入（双方均剔除跳过）、语义 outline、审计恒等式、引文行展开 |
| reporter 节点 | `app/orchestrator/nodes/reporter.py`（改造） | Markdown 链保留；content_map 查 Evidence 行（正文不入 state）；一次结构化调用 + 一次自修复；机械映射降级 reporter_degraded；装配经 NodeDeps.report_assembly 交 executor |
| state 类型 | `app/orchestrator/state.py`（改造） | ResearchState 增 report_blocks/reporter_degraded（LLD §6.2 已同步） |
| 依赖通道 | `app/orchestrator/dependencies.py`（改造） | NodeDeps 增 report_assembly（run 级进程内通道，不占 state 字段） |
| 落库 | `app/orchestrator/executor.py`（改造） | `_mark_succeeded` 终稿 status=final、content_json={outline,blocks,citation_audit}、flush 后同事务展开引文行；citation_audit 带 reporter_degraded |
| ORM/迁移 | `app/db/models/report.py`、`0005_report_citations_blocks.py`（新增） | block_id NOT NULL、claim_id 可空、uq_citation_block_evidence、ix_report_citations_position；downgrade 往返；head=0005 |
| API schema | `app/schemas/reports.py`（改造） | ReportBlockType/ClaimConfidence、ReportCitation/ReportOutlineItem/ReportBlock/ReportCitationItem；ReportResponse 增 outline/blocks |
| service | `app/services/reports.py`（新增） | get_report_for_run（404/422 口径）、to_response（新形态超集/旧 draft 空数组）、list_citations（scalars 双查 + position 去重升序） |
| 路由 | `app/api/v1/reports.py`、`app/api/v1/runs.py`（改造） | GET /reports/{run_id}/citations 新增；两报告端点切 service 出超集；清理内联归属逻辑 |
| 配置 | `app/core/config.py`、`.env.example` | REPORT_LLM_TIMEOUT_SECONDS=45、REPORT_LLM_MAX_TOKENS=4096 |
| 契约 | `app/export_openapi.py`、`docs/contract/openapi-m2-7.json` | `_M27_ENDPOINTS` 1 路径 + 描述常量；M2-5 转默认冻结（`--refresh-m25`）；导出 25 路径，M2-5 文件零 diff |
| 测试 | test_report_blocks（45 新增）、test_reporter（+6）、test_runs_api（+5）、test_executor（1 更新）、test_integration_m27（2 新增真库）、test_integration_m24/m25（钉版本号） | AC-1~AC-13 全覆盖 |
| 文档 | 本文件 v1.0、LLD §5.3.9/§6.2/§6.5.7 同步、backend/README 回填、M2-7 交接单 | 落位遵循《项目人员协调操作规约》 |

### 13.2 与评审稿（v0.2）的实现偏差

1. **装配产物经 NodeDeps 通道传递，不入 state**：评审稿允许的 state 新字段仅 report_blocks/reporter_degraded 两个；outline/citation_audit/citation_rows 若再开 state 字段会偏离冻结清单，实现为 `NodeDeps.report_assembly: ReportAssembly | None`（deps 本就是 run 级注入通道，executor 在同一进程内读取），ResearchState 严格只增两字段。
2. **content_map 查询走 `scalars(select(Evidence))` 而非 `execute(select(id,content,snippet))`**：与项目持久化层（persistence.py 全部 scalars）及现有假会话替身约定保持一致；按 run_id 取全量证据行后由引擎按池 id 使用，不改变 §5.4 语义。
3. **分歧原语抽到 reporting.disputes 而非在引擎内复制**：reporter Markdown 渲染（M2-2 已交付并测试）与 blocks 引擎共用同一份冲突选择/双方口径原语，reporter 侧改为薄封装委托，26 条既有 Markdown 用例零改动通过。
4. **信源索引查询不做 SQL JOIN**：service 用两次 scalars（引文行 + run 证据池）在 Python 侧拼装去重，与全仓数据访问风格一致；证据受外键约束必然在池内，不设缺行兜底。
5. **历史迁移循环测试钉死版本号**：test_integration_m24/m25 原以 `upgrade head` 断言固定版本，新增 0005 后 head 漂移；按「历史用例验证自身迁移循环」语义改为显式 `upgrade "0003"/"0004"`，断言内容不变。
6. **citation_audit 增补 reporter_degraded 布尔**：§5.7 要求 state 与 content_json 双标记，实现把该标记并入 audit 对象（不另开 content_json 顶层键），state 侧仍为独立字段。
7. **联调 P1-1 修复（2026-09-15）**：前端真链回归发现零 conclusion 存活时兜底概述块把含数字断言的研究问题原文嵌入无引用 inferred 块（绕过 §5.5 审计）。修复两点：① `_fallback_overview_block` 在问题文本命中数字断言时改用无数字通用概述「本次研究的多源核验结果如下。」，与 §5.5 同口径；② `numeric_claim_binding_rate` 由硬编码 1.0 改为遍历终稿 claim 块按数字断言/引用实况回算，使审计恒等式可自检。补「数字问题 × 零 conclusion 存活」与空问题两个组合用例。

### 13.3 门禁证据

- 全量 `pytest -q`：**634 passed**（离线 + 全部真库集成，零跳过）；M2-7 新增 58 用例（test_report_blocks 45 + test_reporter 结构化接线 6 + test_runs_api 超集/信源索引 5 + test_integration_m27 真库 2），test_executor 更新 1 处终稿断言，m24/m25 迁移用例钉版本号；基线 576 → 634。
- 真库 `tests/test_integration_m27.py`：m27_orm 下引擎装配（quote 命中统计局正文「30%」原句窗口）→ final Report + 3 条 block×证据引文行（evidence 块 claim_id 为空）→ 独立会话经 service 读回信源索引 [1]/[2] 九展示字段齐全；m27_migration 下 alembic upgrade head=0005（block_id NOT NULL、claim_id nullable、uq 约束与三索引存在）→ downgrade -1 回 0004 全部消失 → upgrade 恢复；两 schema 跑后 DROP CASCADE 无残留。
- ruff：本批全部变更文件 check All checks passed、format 已应用。
- mypy：54 条，与 dev worktree 基线（b9b3481）54 条**零新增**（实现中出现的 2 条 prompts TypedDict 入参告警已通过精确标注消除）。
- 契约（AC-12）：openapi-m2-7 导出 **25 路径**（新增 GET /api/v1/reports/{run_id}/citations）；ReportBlock（required id/type/text，四可选）、ReportCitation（三必填）、ReportCitationItem（五必填 + domain/source_type/source_level/credibility/published_at 五可选，线上 10 字段）、ReportOutlineItem（三必填）与前端 types.ts 字段名/枚举/可选性差异为 0；M2-5 快照保持 24 路径零 diff（转默认冻结）。
- 联调前置：alembic head 由 0004 变 **0005**，联调环境须 `alembic upgrade head`。
- P1-1 修复后复测（2026-09-15）：全量 **636 passed**（+2 兜底概述组合用例）；ruff 全净、mypy 54 条零新增；无契约/迁移变化（纯引擎纯函数修复，openapi-m2-7 25 路径不变）。
