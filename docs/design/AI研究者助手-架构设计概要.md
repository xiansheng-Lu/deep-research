# AI 研究者助手 · 架构设计概要

> 版本: v1.0 · 起草日期: 2026-09-09 · 适用范围: M1-M3 范围
> 上游文档: PRD · HLD（智能体协作规格）· SDP · DS（交互设计规范）
> 下游产出: 后端详细设计 · 前端详细设计 · 数据模型详细设计 · 部署运维手册

---

## 1. 概述

### 1.1 目标

为「AI 研究者助手」提供 M1-M3 阶段的整体架构设计，确保：

1. 支撑 6 阶段研究流水线的端到端编排（澄清 → 分解 → 检索 → 标准化 → 批判收敛 → 报告）
2. 8 类业务智能体的协作可观察、可介入、可审计
3. 私域数据（CRM、内部数据库、Notion、Confluence）安全接入
4. 数据点级溯源链贯穿始终

### 1.2 不在本概要范围

- 具体模块的详细设计（由后端 / 前端详细设计文档承接）
- 具体 LLM 的 prompt 设计（由 prompt 工程规范承接）
- 性能压测指标与调优（M3 之后再补）
- 多区域部署、灾备方案（M3 之后再补）

### 1.3 读者

- 后端工程师：明确服务边界、模块依赖、API 契约
- 前端工程师：明确 API 形态、实时推送协议、鉴权模型
- 数据工程师：明确数据模型、迁移策略、备份方案
- 产品经理：明确能力边界、技术风险与里程碑

---

## 2. 技术选型（已确认）

| 类别 | 选型 | 理由 |
|---|---|---|
| 后端语言 | Python 3.11 | AI/LLM 生态最成熟 |
| 后端框架 | FastAPI | 异步原生、OpenAPI 自动生成、类型提示完备 |
| Agent 编排 | LangGraph | 状态图语义、Human-in-the-loop 原生、与 6 阶段流水线高度匹配 |
| LLM 抽象 | 自研 Provider Adapter | 可插拔 OpenAI / Anthropic / 国产模型 |
| 主数据库 | PostgreSQL 16 | 关系型 + JSONB + 事务 |
| 向量检索 | pgvector | 知识库语义检索、单数据源、运维简单 |
| 任务队列 | Celery + Redis | 阶段任务、异步检索、可观测 |
| 实时推送 | WebSocket（指挥舱） + SSE（报告流） | 延迟与实现成本最优解 |
| 对象存储 | S3 兼容（MinIO 自托管起步） | 私域文档、报告 PDF、原始证据 |
| 私域连接器 | 独立 Connector Service | CRM / Notion / Confluence / 内部销售库 |
| 前端框架 | Vue 3（Composition API + `<script setup>`） | 组合式写法全站统一 |
| 前端路由 | Vue Router 4 | 懒加载 + 导航守卫 |
| 状态管理 | Pinia | 仅持久化实体与全局态，实时数据不进全局 store |
| 前端语言 | TypeScript（strict） | 业务类型由 OpenAPI 生成 |
| 部署 | Docker + docker-compose（M1-M2） → Kubernetes（M3+） | 与团队规模匹配 |

---

## 3. 系统拓扑

### 3.1 总体拓扑图

```
┌─────────────────────────────────────────────────────────────────────┐
│                          客户端 (浏览器)                              │
│                                                                       │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│   │  首页    │ │  指挥舱  │ │  报告    │ │  向导    │ │ 知识库   │ │
│   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
└───────────────────────┬─────────────────────────────────────────────┘
                        │ HTTPS / WebSocket
                        ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       API 网关 (FastAPI)                              │
│  鉴权 · 限流 · OpenAPI · 请求路由 · CORS                              │
└──────┬───────────────────────────────────────────┬─────────────────┘
       │                                           │
       │ 同步 API                                  │ WebSocket / SSE
       ↓                                           ↓
┌──────────────────────────┐    ┌─────────────────────────────────────┐
│   Core API (FastAPI)     │    │   Realtime Gateway                  │
│   - 项目/报告 CRUD       │    │   - 阶段状态推送                     │
│   - 用户/团队管理        │    │   - 证据流推送                       │
│   - 知识库查询           │    │   - token 计数推送                   │
│   - 裁决记录             │    │   - 分歧告警推送                     │
└──────┬───────────────────┘    └──────────────┬──────────────────────┘
       │                                        │
       ↓                                        ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    Agent 编排层 (LangGraph)                          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │   Orchestrator (状态图调度)                                     │  │
│  │     ↓                                                          │  │
│  │   Clarifier → SubQuestioner → Researcher (×N 并行)              │  │
│  │     → Standardizer → Critic → Reporter                         │  │
│  │     ↑                                                          │  │
│  │   Human-in-the-Loop Checkpoints                                │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────┬───────────────────────────────────────────────────┬─────────┘
       │                                                   │
       ↓                                                   ↓
┌──────────────────────────┐               ┌─────────────────────────┐
│   Worker Pool (Celery)   │               │   Provider Adapter       │
│  - 检索任务              │               │  - OpenAI                │
│  - 数据标准化            │               │  - Anthropic             │
│  - 报告生成              │               │  - 国产模型              │
│  - 分歧检测              │               │  - 内部模型 (M3+)        │
└──────┬───────────────────┘               └─────────────────────────┘
       │
       ↓
┌─────────────────────────────────────────────────────────────────────┐
│                         数据层                                       │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────────────┐ │
│  │   PostgreSQL   │ │     Redis      │ │   MinIO (S3)           │ │
│  │   + pgvector   │ │  (缓存/队列)   │ │  (文件/原始证据)       │ │
│  └────────────────┘ └────────────────┘ └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
       ↑
       │ (通过 Connector Adapter)
       ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    私域数据连接器层                                   │
│  CRM Connector · 内部销售库 · Notion · Confluence · [+ 扩展点]       │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 部署视图（M1-M2）

单区域起步，最小化部署单元：

- 1 个 API 节点（FastAPI + Uvicorn）
- 1 个 Worker 节点（Celery）
- 1 个 PostgreSQL 实例（含 pgvector 扩展）
- 1 个 Redis 实例
- 1 个 MinIO 实例
- 1 个 Nginx 反向代理

预计资源：4 vCPU / 16 GB RAM / 200 GB SSD（单实例可支撑 ~50 并发研究任务）

---

## 4. 模块划分

### 4.1 后端模块（M1-M2）

| 模块 | 职责 | 核心实体 |
|---|---|---|
| `api-gateway` | 鉴权、限流、路由、OpenAPI | - |
| `core-api` | 项目/报告/知识库 CRUD API | Project, Report, User, Team |
| `research-orchestrator` | 基于 LangGraph 的 6 阶段流水线 | ResearchRun, Stage, Checkpoint |
| `agents` | 8 类业务智能体实现（Orchestrator 属横切编排层，归 `research-orchestrator` 模块） | IntentRouter, Clarifier, Planner, SubQuestioner, Researcher, Standardizer, Critic, Reporter |
| `provider-adapter` | LLM 抽象层 | LLMRequest, LLMResponse, TokenUsage |
| `realtime-gateway` | WebSocket / SSE 推送 | EventBus, Subscription |
| `worker-tasks` | Celery 异步任务 | RetrievalTask, NormalizeTask, ReportTask |
| `knowledge-store` | 知识库检索 / 沉淀 | KnowledgeItem, Embedding, Citation |
| `connector-hub` | 私域数据连接器 | Connector, SyncJob, Token |
| `audit-log` | 全量操作审计 | AuditEntry |

### 4.2 前端模块（M1-M2）

| 模块 | 职责 | 对应原型页面 |
|---|---|---|
| `pages/home` | 首页 · 进行中研究 · 知识库入口 | 01-home.html |
| `pages/cockpit` | 指挥舱 · 6 阶段时间线 · 用户介入抽屉 | 02-cockpit.html |
| `pages/report` | 报告阅读 · 协作 · 批注 · @ · 实时编辑 | 03-report.html |
| `pages/wizard` | 发起研究向导 · 5 步 | 04-new-research.html |
| `pages/disputes` | 分歧处理工作台 | 05-disputes.html |
| `pages/knowledge` | 知识库浏览 · 搜索 · 复用 | 06-knowledge.html |
| `components/common` | Button / Card / Badge / Input / Drawer / Modal | - |
| `components/citation` | CiteRef · SourceBadge · AnnotationCard · Mention · AvatarStack | 03 / 05 |
| `stores` | 状态管理（Pinia，已确认；实时运行态不进全局 store，见前端详细设计 §7） | - |
| `services` | API 客户端 + WebSocket 客户端 | - |

### 4.3 数据模块

| 子模块 | 职责 |
|---|---|
| `migrations` | Alembic 数据库迁移 |
| `models` | SQLAlchemy ORM 模型 |
| `seeds` | 种子数据（团队、模板、连接器示例） |
| `vectors` | pgvector 嵌入索引管理 |

---

## 5. 关键流程架构

### 5.1 6 阶段研究流水线（端到端）

```
用户提交研究问题
       ↓
[Stage 1] Clarifier
       · 同步调用 (< 10s)
       · 必要时挂起 → WebSocket 推送"待澄清问题" → 用户抽屉回复
       ↓
[Stage 2] SubQuestioner
       · 同步调用 (< 15s)
       · 分解为 3-8 个子问题，产出 SubQuestion 列表
       ↓
[Stage 3] Researcher (扇出)
       · Celery 异步任务，N 个 Researcher 并行
       · 每个子问题检索 5-10 条证据
       · Realtime Gateway 推送证据流（增量）
       ↓
[Stage 4] Standardizer
       · 同步 / 异步，标准化证据格式
       · 写入 knowledge-store
       ↓
[Stage 5] Critic (循环)
       · 同步调用，检测分歧
       · 不可调和分歧 → 触发 Human-in-the-Loop
       · 用户裁决后写回 → 进入下一轮或退出
       ↓
[Stage 6] Reporter
       · 异步生成报告（> 30s 时 SSE 流式推送）
       · 报告草稿写入 DB，流式推送片段
       · 最终归档 + 知识库沉淀
```

### 5.2 用户介入的架构支持

介入点分布在 4 个位置：

1. **阶段 1 澄清问题** · WebSocket 推送待澄清问题，前端抽屉展示
2. **阶段 3 检索中追问** · Celery 任务支持 `interrupt()` 信号，前端通过 API 注入追问
3. **阶段 5 分歧处理** · Orchestrator 状态图暂停在 Critic 节点，`05-disputes` 页面裁决后 resume
4. **阶段 6 报告生成中** · SSE 流式中断，前端可"打回重审"

所有介入操作记录到 `audit_log`，包含：用户 ID、时间戳、介入类型、附加说明、对应 Run/Stage 引用。

---

## 6. 数据模型概要

### 6.1 核心实体（ER 概要）

```
User ─┐
      ├─< TeamMembership >─ Team
      │
      └─< Project (creator)
              │
              ├─< ResearchRun (1:N)
              │     ├─ id, project_id, status, current_stage
              │     ├─< Stage (1:N) [6 阶段记录]
              │     ├─< SubQuestion (1:N)
              │     ├─< Evidence (1:N)
              │     ├─< Conflict (1:N)
              │     │     └─< Verdict (1:1)
              │     └─> Report (1:1)
              │
              └─> Template (N:M, 复用关系)

KnowledgeItem ─< Embedding (pgvector)
              └─> ReportCitation (N:M, 引用关系)

AuditEntry (append-only, 全量审计)

Connector (私域连接器配置)
  └─< ConnectorSync (1:N, 同步任务)
```

### 6.2 关键表字段概要

| 表 | 关键字段 | 索引 |
|---|---|---|
| `users` | id, email, hashed_password, display_name, role, team_id | email unique |
| `projects` | id, name, owner_id, team_id, status, created_at | owner_id, team_id |
| `research_runs` | id, project_id, status, current_stage, orchestrator_state, started_at, finished_at | project_id, status |
| `stages` | id, run_id, stage_name, status, started_at, finished_at, token_used | run_id |
| `sub_questions` | id, run_id, question, status, assigned_researcher_id | run_id |
| `evidence` | id, run_id, sub_question_id, source_url, source_level, content, raw_data, fetched_at | run_id, sub_question_id |
| `conflicts` | id, run_id, evidence_a_id, evidence_b_id, type, severity, status, resolved_at | run_id, status |
| `verdicts` | id, conflict_id, user_id, choice, reason, created_at | conflict_id unique |
| `reports` | id, run_id, content_md, content_html, status, created_at | run_id unique |
| `knowledge_items` | id, type, title, content, source_url, created_by, project_id | type, project_id, created_by |
| `knowledge_embeddings` | item_id, embedding vector(1536), chunk_text | pgvector HNSW |
| `audit_entries` | id, user_id, action, target_type, target_id, payload, created_at | user_id, target_id, created_at |
| `connectors` | id, team_id, type, config, auth_status, last_sync_at | team_id, type |

---

## 7. Agent 编排设计

### 7.1 LangGraph 状态图骨架

```python
# state.py (伪代码)
class ResearchState(TypedDict):
    run_id: str
    project_id: str
    question: str
    clarification: Optional[Dict]
    sub_questions: List[SubQuestion]
    evidence: List[Evidence]
    standardized: List[Evidence]
    conflicts: List[Conflict]
    verdicts: List[Verdict]
    report_draft: str
    current_stage: str
    interrupt_reason: Optional[str]
    human_input: Optional[Dict]

# graph.py (伪代码)
workflow = StateGraph(ResearchState)

# 核心智能体节点
workflow.add_node("clarify", clarifier_node)
workflow.add_node("decompose", sub_questioner_node)
workflow.add_node("retrieve", researcher_fan_out)        # 扇出并行
workflow.add_node("standardize", standardizer_node)
workflow.add_node("critique", critic_node)              # 循环
workflow.add_node("report", reporter_node)

# 横切节点（与 §11 风险对策呼应）
workflow.add_node("cost_checkpoint", cost_checkpoint_node)   # 阶段间预算闸门，超额触发用户确认
workflow.add_node("user_intervention", user_intervention_node)  # 通用人工介入点（暂停/追问/剔除/打回）
workflow.add_node("failure_recovery", failure_recovery_node)    # Provider 失败 / 超时 / 中断 恢复

# 边定义
workflow.add_edge("clarify", "decompose")
workflow.add_edge("decompose", "retrieve")
workflow.add_edge("retrieve", "standardize")
workflow.add_edge("standardize", "critique")

# Critic → 条件边：分歧是否需要人工裁决
workflow.add_conditional_edges(
    "critique",
    lambda s: "await_human" if s["conflicts"] and not s["verdicts"] else "cost_checkpoint",
    {"await_human": "await_human", "cost_checkpoint": "cost_checkpoint"}
)

# cost_checkpoint → 条件边：是否超额
workflow.add_conditional_edges(
    "cost_checkpoint",
    lambda s: "user_intervention" if s["token_used"] > s["budget_threshold"] else "report",
    {"user_intervention": "user_intervention", "report": "report"}
)

# 人工裁决后回到 critic 再循环
workflow.add_edge("await_human", "critique")
workflow.add_edge("user_intervention", "report")

# 失败恢复：任意节点失败均进入 failure_recovery，根据恢复策略决定重试 / 跳过 / 终止
workflow.set_entry_point("failure_recovery")  # 启动时先做健康检查
workflow.add_edge("failure_recovery", "clarify")
workflow.add_edge("report", END)
```

> **意图路由不进入本状态图**：意图路由（IntentRouter）在 API 层以独立接口实现（后端详细设计 §4.2.8 `/intent/classify`，SDP M2-1），仅当判别结果为"研究"时才创建 ResearchRun 并启动本图；"闲聊"请求不产生 Run。`nodes/intent_router.py` 是该接口的 LLM 调用实现单元，复用节点目录的 prompt 与 Provider 基建，但不作为图节点编排。

### 7.2 Human-in-the-Loop 实现

LangGraph 提供原生 `interrupt()` API：

- 在 `await_human` 节点调用 `interrupt({"conflicts": [...], "context": ...})`
- 状态被持久化（checkpoint），任务暂停
- 前端通过 WebSocket 收到 `interrupt` 事件
- 用户裁决后，前端调用 `POST /runs/{run_id}/resume` API
- Orchestrator 从 checkpoint 恢复，`human_input` 注入 state

### 7.3 实时事件推送

Orchestrator 各节点通过 `EventBus` 发布事件：

| 事件 | 推送方式 | 消费者 |
|---|---|---|
| `stage.started` / `stage.finished` | WebSocket | 指挥舱时间线 |
| `evidence.fetched` | WebSocket (节流 500ms) | 指挥舱证据流 |
| `conflict.detected` | WebSocket | 指挥舱 + 报告角标 |
| `token.usage.update` | WebSocket (节流 1s) | 指挥舱成本展示 |
| `report.chunk` | SSE | 报告阅读页 |
| `report.finished` | WebSocket | 全局通知 |

---

## 8. API 契约概要

### 8.1 REST API 分组

```
/api/v1/auth            · 鉴权 (登录/刷新/SSO)
/api/v1/users           · 用户管理
/api/v1/teams           · 团队管理
/api/v1/projects        · 项目 CRUD
/api/v1/runs            · 研究运行 CRUD + 控制
/api/v1/runs/{id}/resume · 人工裁决后恢复
/api/v1/conflicts       · 分歧查询 + 裁决
/api/v1/reports         · 报告查询
/api/v1/knowledge       · 知识库查询
/api/v1/connectors      · 私域连接器配置
/api/v1/templates       · 模板管理
/api/v1/audit           · 审计日志查询
```

### 8.2 WebSocket 端点

```
ws://api/v1/runs/{run_id}/stream
  · 双向：客户端可发送"暂停/追问/剔除证据"指令
  · 服务端推送：stage / evidence / conflict / token 事件
```

### 8.3 SSE 端点

```
GET /api/v1/runs/{run_id}/report/stream
  · 单向：服务端流式推送 report.chunk 事件
  · 含 done 事件表示结束
```

完整 API 契约由 FastAPI 自动生成 OpenAPI 3.1，前端通过 OpenAPI Generator 生成 TypeScript 类型。

---

## 9. 私域连接器架构

### 9.1 连接器抽象

```python
class Connector(Protocol):
    type: str                      # "crm" | "notion" | "confluence" | "internal_sales" | ...
    async def authorize(config): ...
    async def fetch(query, scope): ...   # 检索
    async def list_documents(): ...      # 知识库同步
    async def health_check(): ...
```

### 9.2 隔离与安全

- 连接器运行在独立进程组（Celery 专用队列）
- 凭证存储在 PostgreSQL 加密字段（AES-256，密钥由 KMS 管理）
- 数据访问范围按 team 隔离，越权读取触发审计告警
- 私域数据进入系统后打 `source_private` 标签，水印到证据流

### 9.3 同步策略

- 启动时全量同步（异步任务）
- 每 6 小时增量同步（Celery beat）
- 用户可在 UI 触发"立即同步"

---

## 10. 可观测性

| 维度 | 工具 | 关键指标 |
|---|---|---|
| 日志 | structlog → JSON → stdout | 请求 trace_id、智能体执行轨迹 |
| 指标 | Prometheus | 阶段耗时、token 用量、并发任务数 |
| 追踪 | OpenTelemetry → Jaeger | 跨服务调用链 |
| 告警 | Alertmanager | 阶段超时、token 暴增、LLM provider 失败 |

---

## 11. 关键技术风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| LLM 输出不稳定 | 报告质量波动 | Critic Agent 多轮评审 + 关键事实交叉验证 |
| 长任务中断 | 状态丢失 | LangGraph checkpoint + PostgreSQL 持久化 + failure_recovery 节点 |
| 私域数据泄露 | 合规事故 | 数据加密 + team 隔离 + 全量审计 + 水印 |
| Token 成本超预算 | 经济风险 | 预算硬上限 + cost_checkpoint 阶段闸门 + 实时成本展示（≤3s） |
| 并发检索压垮上游 | 服务降级 | 任务队列限流 + 退避重试 + 熔断 |
| 向量检索召回差 | 知识库可用性低 | 混合检索（向量 + 全文 + 元数据） + 人工反馈闭环 |
| **LLM Provider 单点故障** | 流水线瘫痪 | Provider Adapter 抽象层 + 主备 provider 池 + 故障自动转移（OpenAI 主 / Anthropic 备）+ 熔断降级到轻量模型 |
| **LangGraph 版本稳定性** | API 不兼容 | 锁定主版本（`<1.x`）+ 自研适配层（`graph_wrapper.py`）隔离升级影响 + CI 中跑升级演练 |
| **私域连接器 SLA 差异** | 数据陈旧 / 拉取失败 | 异步隔离 + 健康检查（5min 一次）+ 失败时降级到本地缓存 + 用户可见"同步失败"提示 |

---

## 12. 与现有文档的衔接

| 现有文档 | 本概要承接 | 详细化位置 |
|---|---|---|
| PRD §5 模块划分 | §4 模块划分 | 后端详细设计 / 前端详细设计 |
| HLD §10 交互协议 | §7 Agent 编排 | 后端详细设计 §Orchestrator |
| SDP §5 里程碑 | §13 实施路径 | SDP 不变 |
| DS §3 信息架构 | §4.2 前端模块 | 前端详细设计 §组件树 |
| DS §4 页面布局 | §8 API 契约 | 前端详细设计 §路由 |

---

## 13. 实施路径（与 SDP 对齐）

里程碑编号与语义以 SDP §3 为唯一基线，下表为架构视角的 M1-M3 交付映射。

| 阶段 | 后端交付 | 前端交付 | 数据交付 |
|---|---|---|---|
| M1（最小链路端到端 demo） | Core API + Orchestrator + Clarifier/Planner/SubQuestioner/Researcher + 统一日志基线 + PostgreSQL 基础表 | 4 个 v1.0 页面 + Mock | 表结构 + Alembic |
| M2（能力补齐与工程化） | Critic/Reporter + 意图路由（/intent/classify）+ 分歧 API + 用户介入接口 + pgvector | v1.1 优化（分歧处理 / 知识库页） | 知识库表 + 嵌入索引 |
| M3（核心 MVP） | 档位参数化 + 模板引擎最简版 + 基础运营（账号/邀请/反馈） | 模板/档位向导完善 + 报告 v1 完整 | 模板表 + 配额表 |

> 批注/@、报告分享等协作增强对应 SDP M4（见后端契约草案 §17.1 里程碑映射）；连接器层、多团队、SSO、审计等规模化与私域能力对应 SDP M5/M6，架构增量在相应里程碑启动前细化。

---

## 14. 下一步

1. **本周**：基于本概要启动后端详细设计文档（含 LangGraph 节点详细实现、API 完整契约、数据库迁移脚本骨架）
2. **下周**：并行启动前端详细设计（组件层次、状态管理选型、路由结构）
3. **M1 Sprint 1**：完成 M1 范围内的最小可跑通骨架

---

> 备注：本概要确认后即可作为后续两份详细设计文档的「上层输入」，避免反复对齐。
