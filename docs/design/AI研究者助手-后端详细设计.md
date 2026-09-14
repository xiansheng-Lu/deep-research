# AI 研究者助手 · 后端详细设计

> 版本: v2.0 · 起草日期: 2026-09-09 · 适用范围: M1-M2 · 定稿日期: 2026-09-10
> 状态: 已定稿
> 定稿说明: 编排契约（§6.2 ResearchState / §6.3 状态图 / §6.5 节点）为权威实现基线；M0 编排层代码（state/graph/edges/nodes）已按本契约对齐
> 进度口径: 本文为 M1-M2 设计基线，**当前实现进度与各里程碑关闭状态以《软件开发计划》§3 与 backend/README「里程碑与当前状态」为唯一事实源**（M1 已于 2026-09-12 关闭，M2-1/M2-2 已完成）；本文 §3.2 为 M0 快照不随实现滚动，已落地代码与本文不一致处以代码 + 冻结契约（docs/contract/openapi-m2-2.json）+ 阶段技术方案偏离点章节为准
> 上游文档: [PRD](./AI研究者助手-软件需求规格说明书.md) · [HLD·智能体协作规格](./AI研究者助手-智能体协作规格说明.md) · [架构设计概要](./AI研究者助手-架构设计概要.md) · [SDP](./AI研究者助手-软件开发计划.md)
> 平级文档: 前端详细设计 · 数据模型详细设计 · 部署运维手册
> 读者: 后端工程师 · 平台架构师 · 数据工程师

---

## 目录

1. [文档概述](#1-文档概述)
2. [技术栈与工程基线](#2-技术栈与工程基线)
3. [项目结构与代码组织](#3-项目结构与代码组织)
4. [API 契约完整定义](#4-api-契约完整定义)
5. [数据访问层设计](#5-数据访问层设计)
6. [Agent 编排层详细设计](#6-agent-编排层详细设计)
7. [Provider Adapter 详细设计](#7-provider-adapter-详细设计)
8. [实时推送层详细设计](#8-实时推送层详细设计)
9. [横切模块详细设计](#9-横切模块详细设计)
10. [私域连接器设计](#10-私域连接器设计)
11. [错误处理与重试策略](#11-错误处理与重试策略)
12. [可观测性](#12-可观测性)
13. [安全设计](#13-安全设计)
14. [部署与运维](#14-部署与运维)
15. [M1-M2 实施清单](#15-m1-m2-实施清单)
16. [与上游文档衔接](#16-与上游文档衔接)

---

## 1. 文档概述

### 1.1 目标

承接 [架构设计概要](./AI研究者助手-架构设计概要.md)（以下简称"HLD"），将其中描述的后端骨架落地为可直接编码的实现级设计。具体目标：

1. 给出 M1-M2 范围内每个后端模块的代码目录、类签名、关键函数契约
2. 给出完整的 REST / WebSocket / SSE API 契约，可直接生成 OpenAPI 客户端
3. 给出 6 阶段 LangGraph 状态图的节点详细实现路径
4. 给出数据库迁移骨架（Alembic）、关键表索引、查询模式
5. 给出横切关注点（鉴权、配额、审计、限流）的统一实现策略

### 1.2 不在本文范围

- 前端组件、状态机、路由（见前端详细设计）
- 字段级数据库建模、索引调优（见数据模型详细设计）
- LLM prompt 模板具体内容（见 prompt 工程规范，受控版本管理）
- K8s 多区域部署（见部署运维手册）
- 多模态、视觉检索等 M3+ 能力

### 1.3 术语约定

| 术语 | 含义 |
|---|---|
| Run / ResearchRun | 一次研究任务，对应 HLD §4.1 中 `research-orchestrator` 的实体 |
| Stage | 6 阶段之一，对应一个 LangGraph 节点 |
| SubQuestion | 阶段 2 分解出的子问题 |
| Evidence | 阶段 3 抓取的原始证据 |
| Conflict | 阶段 5 检出的分歧 |
| Checkpoint | LangGraph 状态持久化点 |
| Envelope | 实时事件统一消息外壳（HLD §7.4） |

---

## 2. 技术栈与工程基线

### 2.1 运行时与依赖

| 类别 | 选型 | 版本 | 备注 |
|---|---|---|---|
| 运行时 | Python | 3.11 | LTS · 与 LangGraph、FastAPI 一致 |
| Web 框架 | FastAPI | 0.111+ | 异步原生 · OpenAPI 自动生成 |
| ASGI | Uvicorn | 0.30+ | 标准 ASGI 服务器 |
| Agent 编排 | LangGraph | 1.x | 状态图 + 原生 interrupt |
| ORM | SQLAlchemy | 2.0+ (async) | 异步 API + 类型提示 |
| 迁移 | Alembic | 1.13+ | 与 SQLAlchemy 同源 |
| 任务队列 | Celery | 5.3+ | Redis broker · 阶段任务 |
| 校验 | Pydantic | 2.x | 与 FastAPI 同源 |
| 配置 | pydantic-settings | 2.x | 12 因子配置 |
| 日志 | structlog | 24+ | JSON 格式 · 上下文贯通（§12.1） |
| 日志采集 | Vector | 0.40+ | 采集 stdout JSON，转发至 Loki |
| 日志存储 | Grafana Loki | 3.x | 按 trace_id/run_id 检索，与 Prometheus/Grafana 同栈 |
| 追踪 | OpenTelemetry SDK | 1.27+ | OTLP 导出至 Jaeger |
| 指标 | prometheus-client | 0.20+ | Prometheus 格式 |
| 密码 | passlib[bcrypt] | 1.7+ | bcrypt 哈希 |
| JWT | PyJWT | 2.x | 无状态会话 |
| 加密 | cryptography | 42+ | Fernet (M1) · AES-GCM (M3+) |
| HTTP 客户端 | httpx | 0.27+ | 异步连接器调用 |
| 浏览器自动化 | Playwright | 1.45+ | 公域网页抓取 |
| LLM 抽象层 | langchain-core | 1.x | `BaseChatModel` 作 Provider 底层 |
| LLM Provider 集成 | langchain-openai | 1.x | OpenAI ChatModel 适配 |
| LLM Provider 集成 | langchain-anthropic | 1.x | Anthropic ChatModel 适配 |
| 工具与文档(M5) | langchain-community | 1.x | 仅 M5 私域连接器引入 |
| LangChain 配套 SDK | openai | 1.40+ · `<2.0` | 锁定主版本,M1 结束前不升级 |
| LangChain 配套 SDK | anthropic-sdk | 0.40+ · `<1.0` | 锁定主版本,M1 结束前不升级 |

### 2.2 工程基线

#### 2.2.1 目录约定

所有路径基于项目根目录 `backend/`（M0 已落地：`app/` 单包 + 工程配置文件）。

#### 2.2.2 代码风格

- 遵循 PEP 8 · 行宽 100 · 4 空格缩进
- 类型注解必须（`from __future__ import annotations`）
- 公共函数必须含 docstring（中文，UTF-8）
- 导入顺序：`__future__` → 标准库 → 第三方 → 本地（`isort` 兼容）
- 格式化：`ruff format` · 静态检查：`ruff check` + `mypy --strict`

#### 2.2.3 错误返回统一约定

FastAPI 全局异常处理器将异常转为 RFC 7807 `application/problem+json`：

```json
{
  "type": "https://errors.research-assistant/problems/run-not-found",
  "title": "研究运行不存在",
  "status": 404,
  "detail": "run_id=run_abc123 在当前租户下不可见",
  "instance": "/api/v1/runs/run_abc123",
  "code": "RUN_NOT_FOUND",
  "trace_id": "01HZ..."
}
```

错误码采用 `DOMAIN_REASON` 大写下划线形式（如 `RUN_NOT_FOUND` · `QUOTA_EXCEEDED`），与 i18n key 解耦。

#### 2.2.4 配置加载

`pydantic-settings` 读取 `.env` 与环境变量，强类型校验，启动失败立即报错。环境变量采用扁平命名（`APP_*` / `DB_*` / `LLM_*` 等），字段名语义化，经 `alias` 绑定。全量字段以 `backend/app/core/config.py` 与 `.env.example` 为准（M0 已落地）。

```python
# backend/app/core/config.py
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ===== 运行环境 =====
    app_env: Literal["dev", "staging", "prod"] = Field(default="dev", alias="APP_ENV")
    app_name: str = Field(default="deep-research-api", alias="APP_NAME")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", alias="LOG_LEVEL")

    # ===== 安全 =====
    secret_key: SecretStr = Field(alias="SECRET_KEY")
    encryption_key: SecretStr = Field(default=SecretStr(""), alias="ENCRYPTION_KEY")  # Fernet key · M1-M2

    # ===== 数据访问 =====
    db_async_url: str = Field(alias="DB_ASYNC_URL")   # async 引擎（应用运行时）
    db_sync_url: str = Field(alias="DB_SYNC_URL")     # sync 引擎（Alembic 迁移）
    redis_url: str = Field(alias="REDIS_URL")

    # ===== LLM 主备（Provider 适配层，§7） =====
    llm_primary_base_url: str = Field(default="", alias="LLM_PRIMARY_BASE_URL")
    llm_primary_api_key: SecretStr = Field(default=SecretStr(""), alias="LLM_PRIMARY_API_KEY")
    llm_backup_api_key: SecretStr = Field(default=SecretStr(""), alias="LLM_BACKUP_API_KEY")

    # ===== 统一日志 / 可观测性（§12.1） =====
    log_collector_endpoint: str = Field(default="", alias="LOG_COLLECTOR_ENDPOINT")  # Vector/Loki 采集端，留空仅 stdout
    otel_enabled: bool = Field(default=True, alias="OTEL_ENABLED")                    # OpenTelemetry 追踪总开关
    llm_trace_enabled: bool = Field(default=True, alias="LLM_TRACE_ENABLED")          # LLM 留痕总开关：元数据始终记录；本开关控制失败现场与采样内容落盘
    llm_trace_sample_rate: float = Field(default=0.01, alias="LLM_TRACE_SAMPLE_RATE")  # 成功调用明文采样率 0~0.10，按 team 可热调覆盖（§12.1.5）

settings = Settings()  # type: ignore[call-arg]
```

---

## 3. 项目结构与代码组织

### 3.1 顶层目录

基线：`backend/`（仓库内独立后端工程）。M0 已落地全部模块骨架与公共接口，M1 起逐模块填充实现。

```
backend/
├── pyproject.toml              # 项目元数据、依赖、ruff/mypy 配置
├── alembic.ini                 # Alembic 迁移配置
├── Dockerfile                  # 多阶段构建镜像
├── docker-compose.dev.yml      # 本地开发依赖（pgvector / redis / minio）
├── .env.example                # 配置样例（不含敏感值）
└── app/                        # Python 包根 · 入口 uvicorn app.main:app
    ├── __init__.py             # 包元信息（__version__）
    ├── py.typed                # PEP 561 类型标记
    ├── main.py                 # FastAPI 应用工厂 create_app()（§3.4）
    ├── api/                    # HTTP 路由层
    │   ├── deps.py             # 公共依赖注入（DB 会话、鉴权等）
    │   └── v1/                 # /api/v1 聚合路由（每域一个文件）
    │       ├── _placeholder.py # 占位路由工厂 build_router()
    │       ├── auth.py / users.py / teams.py / projects.py
    │       ├── runs.py / conflicts.py / reports.py
    │       ├── knowledge.py / connectors.py / templates.py / audit.py
    ├── core/                   # 横切基础设施
    │   ├── config.py           # Settings（pydantic-settings）
    │   ├── security.py         # JWT / 密码哈希
    │   ├── exceptions.py       # AppError 业务异常体系
    │   ├── context.py          # 请求上下文（ContextVar）
    │   ├── logging.py          # structlog 配置
    │   ├── lifespan.py         # 应用生命周期
    │   └── tracing.py          # OpenTelemetry 配置
    ├── db/                     # 数据访问层
    │   ├── base.py             # DeclarativeBase + IdMixin/TimestampMixin
    │   ├── session.py          # async engine / SessionFactory
    │   └── migrations/         # Alembic（env.py / script.py.mako）
    ├── schemas/                # Pydantic 请求/响应模型（与 ORM 解耦）
    ├── orchestrator/           # 研究编排层（LangGraph）
    │   ├── state.py            # ResearchState TypedDict / ResearchStage
    │   ├── graph.py            # build_research_graph() / compile_research_graph()
    │   ├── edges.py            # 条件边路由函数
    │   └── nodes/              # 11 个节点（_base.py 提供 instrument 装饰器）
    ├── agents/                 # Agent 业务实现（与编排节点解耦）
    │   ├── base.py             # AgentContext / AgentResult 契约
    │   ├── intent_router.py / clarifier.py / planner.py / sub_questioner.py
    │   └── researcher.py / standardizer.py / critic.py / reporter.py
    ├── provider/               # LLM 适配层
    │   ├── base.py             # LLMProvider 协议 / ChatMessage 等
    │   ├── client.py           # LLMClient 门面（主备路由 + 熔断 + 用量）
    │   ├── circuit_breaker.py  # 熔断器
    │   ├── registry.py         # Provider 注册表
    │   └── usage.py            # UsageTracker 用量跟踪
    ├── retrieval/              # 公域检索
    │   ├── base.py             # RetrievalRequest / RetrievalHit
    │   ├── web_search.py       # 搜索引擎适配
    │   ├── extractor.py        # 正文抽取
    │   └── ranker.py           # 相关性排序
    ├── knowledge/              # 私域知识库
    │   ├── base.py             # KnowledgeItem 等
    │   ├── store.py            # 向量存储读写
    │   ├── embeddings.py       # 嵌入生成
    │   └── chunker.py          # 文档分块
    ├── connectors/             # 私域连接器
    │   ├── base.py             # Connector 抽象基类
    │   ├── http.py             # 通用 HTTP 客户端
    │   └── oauth.py            # OAuth 授权辅助
    ├── workers/                # Celery 异步任务
    │   ├── celery_app.py       # Celery 实例与运行入口
    │   └── tasks/
    │       ├── research.py     # 研究执行
    │       ├── report.py       # 报告导出
    │       └── ingestion.py    # 知识摄取
    ├── realtime/               # 实时推送
    │   ├── hub.py              # RealtimeHub 事件总线
    │   ├── ws.py               # WebSocket 端点
    │   └── sse.py              # SSE 流
    ├── quota/                  # 成本治理
    │   ├── tiers.py            # Tier 档位与预算
    │   └── budget.py           # 预算预留
    ├── audit/                  # 审计日志
    │   ├── base.py             # AuditAction / AuditEntry
    │   └── logger.py           # AuditLogger
    ├── notifications/          # 通知
    │   ├── base.py             # Notification / NotificationChannel
    │   └── dispatcher.py       # 分发器
    └── templates/              # 项目/报告模板
        ├── base.py             # ProjectTemplate / ReportTemplate
        └── registry.py         # 模板注册表
```

> 与设计阶段的差异说明：M0 落地时将原规划的 `apps/api/app/` 三层扁平化为 `backend/app/` 单包（commit 1d706d7），
> Alembic 迁移目录内聚到 `app/db/migrations/`；`tests/` 与 `deploy/` 目录按 SDP 在 M1 引入。

### 3.2 模块实现状态（M0 快照）

> 本表冻结于 M0，仅记录当时的脚手架状态，**不随实现滚动更新**；M1/M2 实际落地情况以代码目录与 backend/README 里程碑表为准。

| 模块 | M0 已落地 | M1 规划填充 |
|---|---|---|
| core | Settings、AppError 异常体系、structlog、trace_id 中间件 | 分页、i18n、RFC 7807 problem 响应完整化 |
| db | Base + IdMixin/TimestampMixin、SessionFactory、Alembic 骨架 | ORM 模型（user/team/project/run/...）、首版迁移 |
| api/v1 | 占位路由（build_router 工厂，GET 占位） | 各端点真实实现与鉴权接线 |
| orchestrator | StateGraph 骨架、11 节点占位、4 条件边 | 节点业务实现、checkpoint、interrupt 包装 |
| agents | 8 个 Agent 占位（AgentContext/AgentResult 契约） | prompt 模板、LLM 调用注入 |
| provider | LLMClient 门面、CircuitBreaker、UsageTracker、Registry | OpenAI / Anthropic 适配器 |
| retrieval | 接口占位 | Tavily 接入、网页抓取、正文抽取 |
| knowledge | 内存占位实现 | pgvector 读写、混合检索 |
| workers | Celery 实例 + 3 任务占位 | 任务与 orchestrator 接线 |
| realtime | 内存 Hub 占位 | Redis Pub/Sub 扇出、心跳 |
| quota / audit / notifications / templates | 契约与占位 | 按 SDP M1-M2 填充 |

### 3.3 模块依赖约束

- `app/orchestrator/` 可依赖 `app/agents/`、`app/provider/`、`app/retrieval/`、`app/knowledge/`，但反向不可
- `app/api/v1/` 只依赖 `app/schemas/` 与 `app/api/deps.py` 注入的依赖（DB 会话等）；业务编排委托 `app/orchestrator/`（M1 若引入 `app/services/` 编排层，编排逻辑落于此），不直接操作 ORM
- `app/db/` 与 `app/agents/`、`app/orchestrator/` 解耦（避免循环）
- `app/workers/` 复用 `app/agents/` 与 `app/orchestrator/`，但不得引用 `app/api/`

### 3.4 应用工厂

```python
# backend/app/main.py
from fastapi import FastAPI
from app.api.v1 import api_v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.lifespan import lifespan as default_lifespan

def create_app(settings: Settings | None = None) -> FastAPI:
    """工厂函数：便于测试时构造独立实例。"""
    cfg = settings or get_settings()
    app = FastAPI(
        title=cfg.app_name,
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=_lifespan,
    )
    app.state.settings = cfg
    app.add_middleware(CORSMiddleware, ...)   # 开发态全放开（§13 生产收敛）
    app.middleware("http")                    # x-trace-id 注入请求上下文
    app.exception_handler(AppError)           # 统一业务异常响应
    app.get("/healthz")                       # 健康探针
    app.include_router(api_v1_router)         # 内部已挂载 /api/v1
    return app

app = create_app()
```

中间件挂载顺序（外→内）：CORS → TraceId → 业务异常处理（路由级鉴权随端点实现）。

---

## 4. API 契约完整定义

### 4.1 通用约定

#### 4.1.1 协议与版本

- 基路径：`/api/v1`
- 协议：HTTPS（生产）/ HTTP（本地）
- 内容类型：请求 `application/json` · 响应 `application/json` 或 `application/problem+json`
- 字符集：UTF-8

#### 4.1.2 鉴权

- 登录接口返回 `access_token`（JWT，HS256，TTL 30 分钟）+ `refresh_token`（TTL 7 天）
- 受保护接口 Header：`Authorization: Bearer <access_token>`
- WebSocket 鉴权：`Sec-WebSocket-Protocol` 携带 JWT 或查询参数 `?token=<jwt>`

#### 4.1.3 幂等

- POST 写接口支持 `Idempotency-Key` Header（UUIDv4），24 小时内同 key 同 body 返回首次响应
- GET 接口天然幂等

#### 4.1.4 分页

- 查询参数：`page`（默认 1）· `page_size`（默认 20，最大 100）· `cursor`（可选，用于 deep paging）
- 响应包装：`{ items: [...], total: int, page: int, page_size: int, has_more: bool }`

#### 4.1.5 时间

- 所有时间戳为 ISO 8601 UTC（`2026-09-09T12:00:00Z`）
- 数据库存储使用 `TIMESTAMPTZ`

#### 4.1.6 ID 规范

- 实体 ID 前缀：`usr_` · `team_` · `proj_` · `run_` · `stage_` · `subq_` · `ev_` · `conf_` · `rep_` · `ki_` · `conn_` · `tmpl_` · `audit_` · `evt_`（事件）· `ant_`（批注）· `men_`（@提及）· `dbt_`（存疑）· `exp_`（导出任务）· `shr_`（只读分享）· `ntf_`（站内通知）· `aud_`（审计条目）
- 生成方式：服务端 ULID（26 字符，时间序）

### 4.2 REST 端点分组

#### 4.2.1 鉴权 `/auth`

| 方法 | 路径 | 描述 | 鉴权 |
|---|---|---|---|
| POST | `/auth/login` | 邮箱密码登录 | 否 |
| POST | `/auth/refresh` | 刷新 access token | 否（refresh token） |
| POST | `/auth/logout` | 注销（撤销 refresh） | 是 |
| POST | `/auth/sso/callback` | SSO 回调（M3+） | 否 |

```jsonc
// POST /auth/login 请求
{
  "email": "alice@acme.com",
  "password": "********"
}
// 响应 200
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "rt_01HZ...",
  "expires_in": 1800,
  "user": { "id": "usr_01HZ...", "email": "alice@acme.com", "display_name": "Alice", "role": "researcher" }
}
```

**POST `/auth/refresh`**（与契约草案 §5.1 对齐）

```jsonc
// 请求
{ "refresh_token": "rt_01HZ..." }
// 响应 200
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "rt_01HZ...",     // 旧 refresh_token 一次性轮换；5min 内的过期宽限由服务端按"宽限表"识别
  "expires_in": 1800
}
// 401 错误：REFRESH_TOKEN_INVALID / REFRESH_TOKEN_EXPIRED
```

**POST `/auth/logout`**（与契约草案 §5.2 对齐）：撤销 `refresh_token`，单设备登出；可选 `{"everywhere": true}` 全设备撤销；服务端清 Redis 会话键并落审计条目。

**WS 鉴权通道**（与契约草案 §5.3 对齐）：采用 **Sec-WebSocket-Protocol 子协议**承载 access_token，客户端握手头发送 `Sec-WebSocket-Protocol: bearer, <jwt>`（协议名固定 `bearer`，jwt 作为第二协议位），服务端校验后回显 `bearer`；不在 URL 查询串中带 token，避免被网关日志/Nginx access log 落盘泄露。子协议协商失败直接 401。M1/M2-2 现网仍为查询参数方案，切换由前后端同分支窗口完成（当前未启用子协议）。

> **会话并发与标签页策略**（契约草案 §5.4）：同一用户允许多设备 + 多标签页；同一 run 的 WS 多订阅者共享推送（Redis Pub/Sub 扇出）。单 run 操作类指令（pause/cancel/intervene/verdict）以 `Idempotency-Key` 为准，重复提交返回上一次结果，避免多标签页重复触发。

#### 4.2.2 用户 `/users`

| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/users/me` | 当前用户信息 |
| PATCH | `/users/me` | 更新个人信息 |
| GET | `/users?team_id=...` | 团队成员列表（管理员） |

#### 4.2.3 团队 `/teams`

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/teams` | 创建团队（首创建者自动为 owner） |
| GET | `/teams/{team_id}` | 团队详情 |
| PATCH | `/teams/{team_id}` | 更新团队设置 |
| POST | `/teams/{team_id}/members` | 邀请成员 |
| DELETE | `/teams/{team_id}/members/{user_id}` | 移除成员 |

#### 4.2.4 项目 `/projects`

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/projects` | 创建项目（绑定默认模板 + 档位） |
| GET | `/projects` | 项目列表（支持筛选 status / owner） |
| GET | `/projects/{project_id}` | 项目详情 |
| PATCH | `/projects/{project_id}` | 更新项目 |
| DELETE | `/projects/{project_id}` | 归档（软删） |
| GET | `/projects/{project_id}/runs` | 项目下研究列表 |
| POST | `/projects/{project_id}/members` | 添加项目成员 |

#### 4.2.5 研究运行 `/runs`（核心）

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/runs` | 发起研究（在意图路由给出"研究"分类后调用） |
| GET | `/runs/{run_id}` | Run 详情（阶段进度、状态） |
| GET | `/runs/{run_id}/stages` | 6 阶段记录 |
| GET | `/runs/{run_id}/sub-questions` | 子问题列表 |
| GET | `/runs/{run_id}/evidence` | 证据池（分页） |
| POST | `/runs/{run_id}/cancel` | 取消（硬中断 + 清理中间态，请求体见 §4.2.5a） |
| POST | `/runs/{run_id}/pause` | 暂停（软暂停，请求体见 §4.2.5a） |
| POST | `/runs/{run_id}/resume` | 恢复（人类输入模型 HumanInput 三选一，见 §4.2.5b） |
| POST | `/runs/{run_id}/intervene` | 通用介入（追问/剔除证据/打回重审） |
| GET | `/runs/{run_id}/cost/snapshot` | 成本快照（WS 断连补齐用） |
| GET | `/runs/{run_id}/report` | 报告查询 |

**POST `/runs` 请求体**

```jsonc
{
  "project_id": "proj_01HZ...",
  "template_id": "tmpl_general_01",
  "tier": "standard",                  // quick | standard | deep | extreme
  "question": "2026 年中国新能源汽车市场趋势",
  "clarification_answers": [           // 可选；意图路由判定"研究"但 Clarifier 判定需追问时，前端先展示澄清题再随本请求提交
    { "key": "scope", "value": "乘用车" },
    { "key": "time_range", "value": "2026 全年" }
  ],
  "idempotency_key": "uuid-..."
}
```

**clarification_answers 契约**：

- **场景 A（一次成型）**：问题本身充分、Clarifier 判定 `requires_user_input=false` → 请求**可省略** `clarification_answers`，Clarifier 自动产出 `structured_question` 作为 `state.clarification`。
- **场景 B（前置澄清）**：前端已通过澄清卡片收集答案 → `clarification_answers` 传入后，服务层在初始化 state 时直接合并 `{"defaults": {}, "structured_question": {…}}` 写入 `ResearchState.clarification`，并置 `clarification.needs_user_input=false`，使 Clarifier 节点跳过追问直接进入 decompose。
- **结构对齐**：`clarification_answers[i].key` 与本文 §6.5.2 `ClarificationQuestion.key` 为同一命名空间；服务层透传时不做 key 改写。
- 若既无 `clarification_answers` 又判定需追问：图在 `await_human` 挂起（LangGraph interrupt，见 §6.5.2/§6.3），服务端经 WS `interrupt.requested`（§4.3.1）向前端推送澄清题；`research_runs.status` 保持 `running`、`current_stage="clarify"`（状态取值见 §5.3.4，无独立"待澄清"枚举，前端以该事件 + `current_stage` 呈现等待态）。用户经 `POST /runs/{id}/resume` 提交答案后继续。

> **resume / intervene 载荷归一**：`POST /runs/{id}/resume` 与 `POST /runs/{id}/intervene` 的请求体在 API 层统一转换为 `state.human_input` 后由 Orchestrator 恢复图（§6.6）：流程判定挂起（澄清/裁决）传 `{"answers": {...}}`；主动介入传 `{"action": "ask_followup|exclude_evidence|revert_stage", "payload": {...}}`（§6.5.9 消费）。intervene 的 `type` 字段映射为 `human_input.action`。

**响应 201**（异步创建；与冻结契约 docs/contract/openapi-m1.json 及实现一致。早期草案曾写 202，已废弃）

```jsonc
{
  "run_id": "run_01HZ...",
  "status": "pending",
  "stream_url": "/api/v1/ws/runs/run_01HZ.../stream",
  "report_stream_url": "/api/v1/runs/run_01HZ.../report/stream",
  "estimated_token_budget": 150000,
  "estimated_cost_grade": "medium"
}
```

**POST `/runs/{run_id}/intervene` 请求体**（统一介入通道）

```jsonc
{
  "type": "ask_followup",   // ask_followup | exclude_evidence | revert_stage（M4 的 mark_doubt/change_tier 另行接入，不入本通道）
  "payload": {
    "sub_question_id": "sq_01HZ...",  // ask_followup 必填：发起补查的子问题
    "question": "请补充...",          // ask_followup 必填
    "evidence_id": "ev_01HZ...",     // exclude_evidence 必填
    "stage": "retrieve",             // revert_stage 必填：回退目标阶段
    "reason": "..."                  // 可选：写入审计日志
  }
}
```

> **字段位置约定**：`payload` 自包含目标操作所需全部参数，API 层归一化时整体透传为 `state.human_input.payload`（见上方 resume / intervene 载荷归一），不做顶层字段再拼接；§6.5.9 逐键消费。
>
> **M4 扩展预留**：`mark_doubt`（标存疑，触发报告论断补查）与 `change_tier`（成本超限手动扩容/换档）为 PRD 定义的后续能力，届时经报告域 / 成本域接口接入，不扩展现有 `user_intervention` 节点枚举。

**POST `/runs/{run_id}/pause` 请求体**（与契约草案 §6.1 对齐）

```jsonc
{
  "reason": "user_pause",             // user_pause | cost_cap | rate_limit | system | ...
  "note": "等我提供补充资料",            // 可选，进入审计条目
  "idempotency_key": "uuid-..."
}
// 200 → { "status": "paused", "paused_at_stage": "retrieve" }
// 409 错误：RUN_NOT_PAUSEABLE（终态/已完成 run 不允许暂停）
```

**POST `/runs/{run_id}/cancel` 请求体**（与契约草案 §6.1 对齐）

```jsonc
{
  "reason": "user_cancel",
  "note": "研究方向错了",
  "idempotency_key": "uuid-..."
}
// 200 → { "status": "cancelled", "preserved_artifacts": { "evidence_count": 247, "sub_question_count": 5 } }
// 取消语义：硬中断 + 保留中间产物（Draft 报告、已收集证据），不删除。
```

**POST `/runs/{run_id}/resume` 请求体**（HumanInput 三选一，与契约草案 §6.2 对齐）

```jsonc
// 形态 A：澄清场景（answers）
{
  "kind": "clarify",
  "answers": [
    { "key": "scope", "value": "近三年" },
    { "key": "geo", "value": "全球" }
  ]
}
// 形态 B：裁决场景（action）—— 设计预案，M2-2 实现未采用本通道
// 实际实现：冲突裁决统一走 POST /api/v1/conflicts/{conflict_id}/verdict（见 §4.2.6），
// 末条 awaiting_human 冲突裁决后由服务层自动构造 human_input 恢复图，不存在独立「继续」接口；
// /runs/{id}/resume 实际只承载澄清答案回流。恢复时序以《M2-2 批判收敛与人机裁决技术方案》§3.2 为准。
{
  "kind": "verdict",
  "action": "evidence_a",              // evidence_a | evidence_b | both | reject
  "reason": "更权威",                  // 必填
  "additional_note": "..."             // 可选
}
// 形态 C：介入动作
{
  "kind": "intervene",
  "action": "ask_followup",            // ask_followup | exclude_evidence | revert_stage
  "payload": { "sub_question_id": "sq_...", "question": "请补充..." }
}
```

> **字段必填与互斥校验**：`kind` 决定三种形态二选一，其余两个键不能出现；任一形态缺关键字段返回 422 `UNKNOWN_HUMAN_INPUT`。`action` 与契约草案 §6.2 一致为强类型枚举。

**阶段失败重试/回溯**（与契约草案 §6.4 对齐，§18.2 #9 待产品确认）

- 字段形式：在 `intervene.action` 增补 `retry_stage`（向后兼容，不改既有枚举名），对应 payload `{stage, attempt, reason}`；服务端读 `state.attempt[stage]` 决定是否触发 LangGraph 的"阶段状态快照+重放"。当前实现为可选能力，M1-M3 维持"失败仅产出提示，前端 §11.3 失败入口不预造按钮"（与前端 §18.2 #9 现状一致）。

**子问题计划 gate**（与契约草案 §6.5 对齐，§18.3 #1 待产品确认）

- 预留接口 `POST /runs/{run_id}/subquestions/replace`，body 为完整的新子问题数组 `[{id?, text, intent, parent_id?, evidence_targets[]}]`；M1-M2 维持只读，M3 起打开。

#### 4.2.6 分歧 `/conflicts`

| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/runs/{run_id}/conflicts` | Run 下分歧列表 |
| GET | `/conflicts/{conflict_id}` | 分歧详情（含双方证据） |
| POST | `/conflicts/{conflict_id}/verdict` | 用户裁决（选择立场 + 原因） |

**裁决请求体**（与契约草案 §6.3 对齐）

```jsonc
{
  "choice": "evidence_a",            // evidence_a | evidence_b | both | reject
  "reason": "更权威",                // 必填，给系统/审计回看
  "additional_note": "..."           // 可选，面向团队的批注，不进入推理链路
}
```

> **字段语义锁定**：`choice` 决定推理分支走向；`reason` 作为审计字段强制写入；`additional_note` 仅用于团队协作展示，不参与 Critic 决策。若需在备注中指引后续处理，使用 §4.6 的批注接口而非扩展本字段。

#### 4.2.7 报告 `/reports`

| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/reports/{report_id}` | 报告完整内容 |
| GET | `/reports/{report_id}/citations` | 报告引用列表 |
| POST | `/reports/{report_id}/export` | 触发导出（M4 起） |

报告内容 schema（节选）：

```jsonc
{
  "id": "rep_01HZ...",
  "run_id": "run_01HZ...",
  "status": "draft | final",
  "outline": [
    { "id": "sec_1", "title": "研究背景", "type": "background" }
  ],
  "blocks": [
    {
      "type": "conclusion",            // conclusion | evidence | dispute | limitation
      "claim_id": "cl_01HZ...",
      "text": "2026 年中国新能源乘用车销量预计 1200 万辆",
      "confidence": "cross_verified",  // single_source | cross_verified | inferred
      "citations": [
        { "evidence_id": "ev_01HZ...", "marker": "[1]", "snippet": "..." }
      ]
    }
  ]
}
```

#### 4.2.8 意图路由 `/intent`

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/intent/classify` | 判别输入意图 |

```jsonc
// 请求
{ "text": "对比一下比亚迪和特斯拉的自动驾驶技术", "user_id": "usr_01HZ..." }
// 响应
{
  "intent": "research",                // chat | research | uncertain
  "confidence": 0.92,
  "recommended_template": "tmpl_competitive_01",
  "recommended_tier": "standard"
}
```

#### 4.2.9 知识库 `/knowledge`

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/knowledge/search` | 语义检索（混合） |
| GET | `/knowledge/items/{id}` | 知识条目详情 |

#### 4.2.10 连接器 `/connectors`（M5 起，路由占位）

| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/connectors` | 团队下连接器列表 |
| POST | `/connectors` | 创建连接器（写入凭证） |
| POST | `/connectors/{id}/sync` | 触发同步 |
| POST | `/connectors/{id}/authorize` | OAuth 跳转 |

#### 4.2.11 模板 `/templates`

| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/templates` | 模板列表 |
| GET | `/templates/{id}` | 模板详情（含报告骨架） |
| POST | `/templates` | 创建模板（管理员） |

#### 4.2.12 审计 `/audit`

| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/audit?target_type=&target_id=` | 查询审计条目（详见 §4.2.19） |

#### 4.2.13 报告批注 `/reports/{report_id}/annotations`（M4，与契约草案 §7 对齐）

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/reports/{report_id}/annotations` | 新增批注（含 @提及） |
| GET | `/reports/{report_id}/annotations?claim_id=&block_id=` | 列表（按锚点过滤） |
| DELETE | `/annotations/{annotation_id}` | 删除（创建者或 admin） |

**POST 请求体**

```jsonc
{
  "anchor": { "kind": "claim", "claim_id": "clm_...", "range": [12, 48] },
  "content_md": "这里的数据口径需要复核。",
  "mentioned_user_ids": ["usr_..."]        // 可选，进入 mentions 记录（M4 起触发站内通知 §4.2.17）
}
```

实体 ID 前缀：`ant_`（Annotation）/`men_`（Mention）；ORM 字段与索引见 §5.3.x（占位实现于 M4）。

#### 4.2.14 报告标存疑 `/reports/{report_id}/claims/{claim_id}/doubt`（M4，与契约草案 §8 对齐）

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/reports/{report_id}/claims/{claim_id}/doubt` | 标存疑（触发报告论断补查） |
| GET | `/reports/{report_id}/doubts` | 存疑清单 |

```jsonc
// POST 请求
{ "reason": "数据来源时效存疑", "note": "..." }
// 副作用：写入 dbt_... Doubt 记录；Critic 节点重评该 claim；触发 SSE report.replace（§4.4.1）。
```

实体 ID 前缀：`dbt_`；报告生命周期（`draft | final | superseded`）见契约草案 §8.3 与 §5.3 报告表对齐。

#### 4.2.15 报告导出 `/reports/{report_id}/export`（M4，与契约草案 §9 对齐）

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/reports/{report_id}/export` | 创建导出任务（异步） |
| GET | `/exports/{export_id}` | 查询导出任务状态 |
| GET | `/exports/{export_id}/file` | 下载（签名 URL 短期有效） |

```jsonc
// POST 请求
{ "format": "pdf", "template": "default", "include_annotations": true, "include_appendix": true }
// 200 → { "export_id": "exp_...", "status": "queued", "format": "pdf", "estimated_seconds": 30 }
// GET  → { "export_id": "exp_...", "status": "ready", "file_url": "https://...", "expires_at": "..." }
```

错误码：`EXPORT_TEMPLATE_NOT_FOUND` / `EXPORT_RENDER_FAILED`（§15.5）。完成/失败通知沿用 §4.2.17 站内通知。

#### 4.2.16 报告只读分享 `/reports/{report_id}/share`（M4，与契约草案 §10 对齐）

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/reports/{report_id}/share` | 创建只读分享链接 |
| GET | `/shares/{token}` | 公开只读访问（无鉴权） |
| DELETE | `/shares/{share_id}` | 撤销分享 |

```jsonc
// POST 请求
{ "expires_in_hours": 168, "include_evidence_appendix": true, "password_required": false }
// 200 → { "share_id": "shr_...", "token": "...", "url": "https://.../s/...", "expires_at": "..." }
```

实体 ID 前缀：`shr_`；分享公开页只读、无任何写入入口；点击统计异步上报不阻塞渲染。

#### 4.2.17 站内通知 `/notifications`（M4 基础，M6 扩展，与契约草案 §12 对齐）

| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/notifications?unread_only=&page=` | 列表（红点驱动） |
| POST | `/notifications/{id}/read` | 标记已读 |
| POST | `/notifications/read_all` | 全部已读 |

实体 ID 前缀：`ntf_`；`kind` 目录与触发源（@提及/标存疑/导出完成/分享访问异常）见契约草案 §12.4。

#### 4.2.18 知识库列表与详情补齐 `/knowledge`（M5，与契约草案 §11 对齐）

| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/knowledge/items` | 列表（按 visibility / type / 时间过滤，分页） |
| GET | `/knowledge/items/{id}` | 详情 |
| POST | `/knowledge/search` | 语义检索（已有，补齐契约） |
| POST | `/knowledge/items` | 创建（M5 起，沉淀报告/证据条目） |
| POST | `/knowledge/items/{id}/link` | 关联到 run（双向回写） |

错误码：`KNOWLEDGE_QUERY_EMPTY` / `KNOWLEDGE_VISIBILITY_DENIED`（§15.6）。

#### 4.2.19 审计列表细化 `/audit`（M6/管理员，与契约草案 §13 对齐）

```jsonc
// GET /audit?target_type=run&target_id=run_...&action=intervene.*&user_id=usr_...&from=&to=&page=1
// → {
//   "items": [{
//     "audit_id": "aud_01HZ...", "user_id": "usr_...", "action": "intervene.exclude_evidence",
//     "target_type": "run", "target_id": "run_...", "payload": { "evidence_id": "ev_..." },
//     "created_at": "..."
//   }],
//   "total": 1, "has_more": false
// }
```

action 命名空间（契约草案 §13.3）：`<domain>.<verb>`，例如 `run.start` / `run.pause` / `run.cancel` / `intervene.ask_followup` / `intervene.exclude_evidence` / `intervene.revert_stage` / `intervene.retry_stage` / `verdict.submit` / `report.doubt` / `report.replace` / `report.export` / `report.share_create` / `report.share_revoke` / `annotation.create` / `annotation.delete` / `mention.create` / `auth.login` / `auth.refresh` / `auth.logout` / `audit.view`。

#### 4.2.20 前端 Telemetry 批量上报 `/telemetry/batch`（M2 起可后置，与契约草案 §14 对齐）

```jsonc
// POST 请求
{
  "events": [
    { "event": "report.citation.open", "ts": 1736486400123, "run_id": "run_...", "page": "report", "props": {} }
  ]
}
// 200 → { "accepted": 1, "dropped": 0 }
// 错误码：TELEMETRY_BATCH_TOO_LARGE（>500 条/批）/ TELEMETRY_PAYLOAD_INVALID
```

### 4.3 WebSocket 端点

#### 4.3.1 `/ws/runs/{run_id}/stream`

- 鉴权：JWT（查询参数或子协议）
- 方向：双向
- 用途：指挥舱实时事件（阶段、证据、冲突、token）
- 心跳：服务端每 30s 发 `{"type": "ping"}`，客户端 60s 内须回 `{"type": "pong"}`，否则断开

**服务端推送**（外层 envelope 严格遵循 HLD §7.4）：

```jsonc
{
  "v": "1.0",
  "event_id": "evt_01HZ...",
  "ts": 1736486400123,
  "run_id": "run_01HZ...",
  "stage": "retrieve",
  "type": "evidence.fetched",
  "payload": {
    "sub_question_id": "subq_01HZ...",
    "evidence": {
      "id": "ev_01HZ...",
      "title": "...",
      "url": "https://...",
      "domain": "...",
      "snippet": "...",
      "source_level": "primary",
      "source_type": "official_doc",
      "credibility": "A",
      "fetched_at": "..."
    }
  }
}
```

事件类型清单：

| `type` | 节流 | payload 关键字段 |
|---|---|---|
| `stage.started` | 即时 | stage, attempt |
| `stage.finished` | 即时 | stage, status, duration_ms, token_used |
| `stage.failed` | 即时 | stage, error_code, error_message |
| `sub_question.created` | 即时 | sub_question |
| `sub_question.started` | 即时 | sub_question_id |
| `sub_question.finished` | 即时 | sub_question_id, evidence_count |
| `evidence.fetched` | 500ms | evidence |
| `interrupt.requested` | 即时 | 见 §4.3.3（沿用契约草案 §4.1：reason/questions[]/defaults/expires_in_seconds） |
| `conflict.detected` | 即时 | 冲突对象**嵌套在 payload 内**（payload 含 id/run_id/claim/evidence_a_id/evidence_b_id/type/severity/status），避免与帧 type 同名覆盖；M2-2 已冻结，见《M2-2 批判收敛与人机裁决技术方案》§5 |
| `token.usage.update` | 1s | used, budget, model_breakdown |
| `report.chunk` | 即时 | chunk_id, delta, position |
| `report.finished` | 即时 | report_id, summary |
| `cost.warning` | 即时 | 见 §4.3.3（沿用契约草案 §4.2：level/used/budget/ratio） |
| `report.replace` | 即时 | 见 §4.3.3（沿用契约草案 §4.4：reason/previous_report_id/new_report_id/changed_claim_ids） |
| `annotation.created` `annotation.updated` `annotation.deleted` | 即时 | 沿用契约草案 §4.5（是否启用由后端评审决定，前端两侧均兼容） |
| `run.finished` | 即时 | status, summary |

> **事件实现状态（2026-09-13 核对）**：M1/M2-2 实际推送的帧为 stage.started、run.finished（含 status=paused 语义）、conflict.detected、conflict.verdicts；表中 stage.finished/failed、sub_question.*、evidence.fetched、token.usage.update、cost.warning 为 M2-3/M2-4 设计目标，report.chunk/report.finished 为 M2-7 数据点级溯源设计目标；以各阶段技术方案与届时冻结契约为准。其中 **report.chunk 报告流 SSE 未进入 M1 冻结契约、M2 不实现**（见 §4.4 状态说明）。

**客户端发送**（受控消息）：

| `type` | 用途 |
|---|---|
| `pong` | 心跳应答 |
| `intervene` | 触发介入（等价于 POST `/runs/{id}/intervene`） |
| `cancel` | 取消运行 |

#### 4.3.2 客户端消息格式

```jsonc
{
  "v": "1.0",
  "type": "intervene",
  "request_id": "req_01HZ...",       // 用于服务端响应匹配
  "payload": { ... }
}
```

服务端响应（成功）：

```jsonc
{
  "v": "1.0",
  "type": "intervene.ack",
  "request_id": "req_01HZ...",
  "ts": 1736486400456,
  "payload": { "queued": true }
}
```

服务端响应（失败）：

```jsonc
{
  "v": "1.0",
  "type": "intervene.error",
  "request_id": "req_01HZ...",
  "ts": 1736486400456,
  "payload": { "code": "INTERVENE_NOT_ALLOWED", "message": "当前阶段不允许追问" }
}
```

### 4.4 SSE 端点

> **实现状态（2026-09-13）**：报告流 SSE **未进入 M1 冻结契约，M1/M2-2 均未实现**（M1 契约对齐时已移除；前端 M1 同步移除 SSE 报告通道，生成中以 run 状态 + WS 驱动、终稿一次性拉取）。本节保留为设计预案：M2-7 数据点级溯源（结构化报告）落地时再评审是否恢复，若恢复须重新冻结契约并同步前端，不得直接按本节开发。已实现的 SSE 仅 M2-1 闲聊 `/api/v1/assistant/chat`（帧形态见冻结契约与《M2-1意图路由与闲聊接口交接》§3.2）。

#### 4.4.1 `/runs/{run_id}/report/stream`（设计预案，未实现）

- 鉴权：JWT（Bearer）
- 方向：单向（服务端 → 客户端）
- 用途：报告片段流式推送
- Content-Type：`text/event-stream`

事件格式：

```
event: report.chunk
id: evt_01HZ...
data: {"v":"1.0","event_id":"evt_01HZ...","ts":1736486400000,"run_id":"run_...","stage":"report","type":"report.chunk","payload":{"chunk_id":"rep_chk_...","delta":"2026 年中国新能源","position":42}}

event: report.chunk
id: evt_01HZ...
data: {"v":"1.0","event_id":"evt_01HZ...","ts":1736486400000,"run_id":"run_...","stage":"report","type":"report.chunk","payload":{"chunk_id":"rep_chk_...","delta":"乘用车销量...","position":48}}

event: done
id: evt_01HZ...
data: {"v":"1.0","event_id":"evt_01HZ...","ts":1736486400000,"run_id":"run_...","stage":"report","type":"done","payload":{"report_id":"rep_01HZ...","status":"final"}}
```

事件类型：`report.chunk` · `report.replace`（用户标存疑触发重生成，§4.6.3） · `done` · `error`（`error.data.payload` 为 `{code, message, trace_id}`）。

> **帧形态定版**：所有事件帧 `data` 都是完整 envelope 的 JSON，`event` 字段与 `data.type` 保持一致（冗余便于事件源过滤），`id` 字段为 `event_id`。`done` 后服务端主动关闭流。详见契约草案 §4.3。

### 4.5 OpenAPI 导出

FastAPI 自动生成 OpenAPI 3.1 规范，由 `backend/app/export_openapi.py` 导出里程碑冻结快照到 `docs/contract/openapi-*.json`（累积超集：M1 快照 `openapi-m1.json` 历史冻结，当前 `openapi-m2-2.json` 含 M1 + M2-1 + M2-2 共 15 端点；快照必须随里程碑批次累积，禁止选择性白名单）。前端通过 `openapi-generator-cli generate -i docs/contract/openapi-m2-2.json -g typescript-fetch` 生成 TypeScript 客户端类型与请求封装（切换清单见 frontend/README「M2-9 生成客户端切换清单」）。

---

## 5. 数据访问层设计

### 5.1 SQLAlchemy 异步配置

```python
# backend/app/db/session.py
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings

engine = create_async_engine(
    str(settings.database_url),
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
```

### 5.2 Base 与约定

```python
# backend/app/db/base.py
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass
from datetime import datetime
from sqlalchemy import func, text
from sqlalchemy.orm import Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)
```

- 主键：统一 ULID（`CHAR(26)`），由应用层生成
- 时间戳：`TIMESTAMPTZ`，数据库侧 `now()` 生成
- 软删：业务表一律含 `deleted_at: TIMESTAMPTZ NULL`
- 租户隔离：核心表含 `team_id`（项目隔离通过 `team_id + project_id` 联合查询实现）

### 5.3 ORM 模型骨架

每个模型文件包含：表名、主键、必填字段、关系（`relationship`）、索引。

#### 5.3.1 User

```python
class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(26), ForeignKey("teams.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[Literal["owner", "admin", "researcher", "reviewer"]]
    last_login_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
```

#### 5.3.2 Team

```python
class Team(Base, TimestampMixin):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    plan: Mapped[Literal["free", "pro", "enterprise"]]
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
```

#### 5.3.3 Project

```python
class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(26), ForeignKey("teams.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None]
    default_template_id: Mapped[str | None] = mapped_column(String(26))
    default_tier: Mapped[Literal["quick", "standard", "deep", "extreme"]] = "standard"
    status: Mapped[Literal["active", "archived"]] = "active"
    deleted_at: Mapped[datetime | None]
    __table_args__ = (Index("ix_projects_team_status", "team_id", "status"),)
```

#### 5.3.4 ResearchRun

```python
class ResearchRun(Base, TimestampMixin):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(26), ForeignKey("projects.id"), index=True)
    creator_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"))
    template_id: Mapped[str] = mapped_column(String(26))
    tier: Mapped[Literal["quick", "standard", "deep", "extreme"]]
    question: Mapped[str] = mapped_column(Text)
    clarification: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[Literal["pending", "running", "paused", "succeeded", "failed", "cancelled"]]
    current_stage: Mapped[str | None] = mapped_column(String(32))
    orchestrator_state: Mapped[dict | None] = mapped_column(JSONB)  # LangGraph checkpoint 引用
    token_used: Mapped[int] = mapped_column(default=0)
    token_budget: Mapped[int]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    __table_args__ = (
        Index("ix_runs_project_status", "project_id", "status"),
        Index("ix_runs_creator", "creator_id"),
    )
```

#### 5.3.5 Stage

```python
class Stage(Base, TimestampMixin):
    __tablename__ = "stages"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(26), ForeignKey("research_runs.id"), index=True)
    name: Mapped[Literal["clarify", "decompose", "retrieve", "standardize", "critique", "report"]]
    status: Mapped[Literal["pending", "running", "succeeded", "failed", "skipped"]]
    attempt: Mapped[int] = mapped_column(default=1)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    token_used: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None]
    output: Mapped[dict | None] = mapped_column(JSONB)
```

#### 5.3.6 SubQuestion

```python
class SubQuestion(Base, TimestampMixin):
    __tablename__ = "sub_questions"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(26), ForeignKey("research_runs.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    depends_on: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[Literal["pending", "queued", "running", "succeeded", "failed", "evidence_short"]]
    evidence_count: Mapped[int] = mapped_column(default=0)
```

#### 5.3.7 Evidence

```python
class Evidence(Base, TimestampMixin):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(26), ForeignKey("research_runs.id"), index=True)
    sub_question_id: Mapped[str] = mapped_column(String(26), ForeignKey("sub_questions.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str]
    title: Mapped[str]
    snippet: Mapped[str] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    raw_storage_key: Mapped[str | None]  # MinIO key
    source_type: Mapped[Literal["official_doc", "news", "community", "search", "internal"]]
    source_level: Mapped[Literal["primary", "secondary", "tertiary"]]
    credibility: Mapped[Literal["A", "B", "C", "D"]]
    relevance_score: Mapped[float]
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)  # URL + content hash 去重
    published_at: Mapped[datetime | None]
    fetched_at: Mapped[datetime]
    metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    excluded_by_user: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        Index("ix_evidence_run_subq", "run_id", "sub_question_id"),
        Index("ix_evidence_fingerprint", "fingerprint"),
    )
```

#### 5.3.8 Conflict + Verdict

```python
class Conflict(Base, TimestampMixin):
    __tablename__ = "conflicts"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(26), ForeignKey("research_runs.id"), index=True)
    claim: Mapped[str] = mapped_column(Text)
    evidence_a_id: Mapped[str] = mapped_column(String(26), ForeignKey("evidence.id"))
    evidence_b_id: Mapped[str] = mapped_column(String(26), ForeignKey("evidence.id"))
    type: Mapped[Literal["factual", "methodological", "temporal", "perspective"]]
    severity: Mapped[Literal["low", "medium", "high"]]
    status: Mapped[Literal["detected", "awaiting_human", "resolved", "abandoned"]]
    resolved_at: Mapped[datetime | None]

class Verdict(Base):
    __tablename__ = "verdicts"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    conflict_id: Mapped[str] = mapped_column(String(26), ForeignKey("conflicts.id"), unique=True)
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"))
    choice: Mapped[Literal["evidence_a", "evidence_b", "both", "reject"]]
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

#### 5.3.9 Report + Citation

```python
class Report(Base, TimestampMixin):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(26), ForeignKey("research_runs.id"), unique=True)
    template_id: Mapped[str]
    status: Mapped[Literal["draft", "final", "superseded"]]
    content_md: Mapped[str] = mapped_column(Text)
    content_json: Mapped[dict] = mapped_column(JSONB)
    token_used: Mapped[int] = mapped_column(default=0)

class ReportCitation(Base):
    __tablename__ = "report_citations"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(26), ForeignKey("reports.id"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(26), ForeignKey("evidence.id"))
    claim_id: Mapped[str] = mapped_column(String(64))
    position: Mapped[int]
    snippet: Mapped[str] = mapped_column(Text)
    __table_args__ = (Index("ix_citation_claim", "report_id", "claim_id"),)
```

#### 5.3.10 KnowledgeItem + Embedding

```python
class KnowledgeItem(Base, TimestampMixin):
    __tablename__ = "knowledge_items"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(26), index=True)
    project_id: Mapped[str | None] = mapped_column(String(26), index=True)
    created_by: Mapped[str] = mapped_column(String(26), ForeignKey("users.id"))
    type: Mapped[Literal["evidence", "report_section", "external_doc", "user_note"]]
    title: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None]
    source_type: Mapped[str | None]
    visibility: Mapped[Literal["private", "team", "project"]] = "project"
    deleted_at: Mapped[datetime | None]

class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"
    item_id: Mapped[str] = mapped_column(String(26), ForeignKey("knowledge_items.id"), primary_key=True)
    chunk_index: Mapped[int] = mapped_column(primary_key=True)
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(1536))  # pgvector
    __table_args__ = (Index("ix_embedding_hnsw", "embedding", postgresql_using="hnsw", postgresql_with={"m": 16, "ef_construction": 64}),)
```

#### 5.3.11 AuditEntry

```python
class AuditEntry(Base):
    __tablename__ = "audit_entries"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(26), index=True)
    user_id: Mapped[str | None] = mapped_column(String(26), index=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(26), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
```

#### 5.3.12 Connector（M5 起）

```python
class Connector(Base, TimestampMixin):
    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(26), index=True)
    type: Mapped[Literal["notion", "confluence", "crm", "internal_sales", "upload"]]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    encrypted_credential: Mapped[bytes]  # Fernet ciphertext
    auth_status: Mapped[Literal["pending", "active", "expired", "error"]]
    last_sync_at: Mapped[datetime | None]
    last_error: Mapped[str | None]
```

其余表（run_collaborators · annotations · mentions · project_origins · templates · quota_usage · notification）建模一致，遵循相同约定。

### 5.4 Alembic 迁移骨架

#### 5.4.1 配置

```python
# backend/app/db/migrations/env.py（关键片段）
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from app.db.base import Base
from app.config import settings
import app.db.models  # noqa: F401  触发模型注册

config = context.config
config.set_main_option("sqlalchemy.url", str(settings.database_url).replace("postgresql+asyncpg", "postgresql"))

target_metadata = Base.metadata
```

#### 5.4.2 初始迁移骨架 `0001_initial.py`

```python
"""initial schema
Revision ID: 0001
Revises:
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 启用扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgvector;")

    # teams / users
    op.create_table("teams", ...)
    op.create_table("users", ...)
    # projects / runs / stages / sub_questions / evidence / conflicts / verdicts
    op.create_table("projects", ...)
    op.create_table("research_runs", ...)
    # 索引...
    # reports / report_citations
    # knowledge_items / knowledge_embeddings
    # audit_entries
    # connectors

def downgrade() -> None:
    op.drop_table("audit_entries")
    # ... 反序
```

#### 5.4.3 后续迁移示例 `0002_research_orchestration.py`

```python
"""add LangGraph checkpoint table
Revision ID: 0002
"""
revision = "0002"
down_revision = "0001"

def upgrade() -> None:
    op.create_table(
        "langgraph_checkpoints",
        sa.Column("thread_id", sa.String(128), primary_key=True),
        sa.Column("checkpoint_ns", sa.String(64), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("parent_checkpoint_id", sa.String(64), nullable=True),
        sa.Column("type", sa.String(32)),
        sa.Column("checkpoint", sa.JSON),
        sa.Column("metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_lg_thread", "langgraph_checkpoints", ["thread_id"])
```

### 5.5 查询模式

#### 5.5.1 租户隔离查询

```python
# 所有跨用户查询必须携带 team_id 过滤
async def list_projects(session: AsyncSession, team_id: str, user_id: str) -> list[Project]:
    stmt = (
        select(Project)
        .where(Project.team_id == team_id, Project.deleted_at.is_(None))
        .where(or_(Project.owner_id == user_id, Project.id.in_(
            select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
        )))
        .order_by(Project.updated_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()
```

#### 5.5.2 证据分页

```python
async def list_evidence(session: AsyncSession, run_id: str, sub_question_id: str | None,
                        page: int, page_size: int) -> tuple[list[Evidence], int]:
    base = select(Evidence).where(Evidence.run_id == run_id, Evidence.excluded_by_user.is_(False))
    if sub_question_id:
        base = base.where(Evidence.sub_question_id == sub_question_id)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await session.execute(base.order_by(Evidence.fetched_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return items, total
```

---

## 6. Agent 编排层详细设计

### 6.1 设计目标

1. 严格遵循 HLD §7.1 状态图骨架
2. 与 LangGraph 节点耦合度低（业务在 `app/agents/`，编排仅负责调度）
3. checkpoint 持久化到 PostgreSQL（崩溃可恢复）
4. 实时事件经 `app.realtime` 推送，不阻塞图执行
5. 用户介入走 `interrupt` API，不修改 Agent 内部状态

### 6.2 ResearchState 定义

```python
# backend/app/orchestrator/state.py
from typing import Literal, TypedDict, NotRequired
from datetime import datetime

SubQuestionStatus = Literal["pending", "queued", "running", "succeeded", "failed", "evidence_short"]
StageName = Literal["clarify", "decompose", "retrieve", "standardize", "critique", "report"]

class SubQuestionDict(TypedDict):
    id: str
    question: str
    depends_on: list[str]
    status: SubQuestionStatus
    evidence_ids: list[str]

class EvidenceDict(TypedDict):
    id: str
    sub_question_id: str
    url: str
    domain: str
    title: str
    snippet: str
    source_type: str
    source_level: str
    credibility: str
    fingerprint: str
    published_at: str | None
    fetched_at: str

class ConflictDict(TypedDict):
    id: str
    claim: str
    evidence_a_id: str
    evidence_b_id: str
    type: str
    severity: str
    status: Literal["detected", "awaiting_human", "resolved", "abandoned"]

class VerdictDict(TypedDict):
    conflict_id: str
    user_id: str
    choice: str
    reason: str | None

class ReportClaim(TypedDict):
    id: str
    text: str
    confidence: Literal["single_source", "cross_verified", "inferred"]
    citations: list[dict]

class ResearchState(TypedDict, total=False):
    run_id: str
    project_id: str
    question: str
    clarification: dict | None
    template_id: str
    tier: str

    sub_questions: list[SubQuestionDict]
    evidence: list[EvidenceDict]
    standardized_evidence: list[EvidenceDict]
    conflicts: list[ConflictDict]
    verdicts: list[VerdictDict]

    report_outline: list[dict]
    report_claims: list[ReportClaim]
    report_draft: str

    current_stage: StageName
    stage_attempts: dict[str, int]
    needs_clarification: NotRequired[bool]      # 阶段1 澄清 HITL 标记（见 §6.3 / §6.5.2）
    interrupt_reason: NotRequired[str]          # clarify | critique —— 区分 await_human 回流路径
    interrupt_payload: NotRequired[dict]        # await_human 挂起时向用户展示的上下文
    human_input: dict | None

    token_used: int
    token_budget: int
    started_at: str
    finished_at: str | None

    trace_id: str                       # 链路追踪 ID（§12.1.2），由 API 层写入
    failure_reason: str | None          # 节点异常兜底记录（§6.5.1 / nodes._base.instrument）
    updated_at: str                     # 最近一次状态更新时间（UTC ISO 8601）
```

### 6.3 状态图构建

```python
# backend/app/orchestrator/graph.py
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.orchestrator.state import ResearchState
from app.orchestrator.nodes import (
    failure_recovery, clarifier, sub_questioner, researcher_fan_out,
    standardizer, critic, reporter, cost_checkpoint, user_intervention,
)

async def build_graph(checkpointer: AsyncPostgresSaver):
    g = StateGraph(ResearchState)

    g.add_node("failure_recovery", failure_recovery)
    g.add_node("clarify", clarifier)
    g.add_node("decompose", sub_questioner)
    g.add_node("retrieve", researcher_fan_out)
    g.add_node("standardize", standardizer)
    g.add_node("critique", critic)
    g.add_node("report", reporter)
    g.add_node("cost_checkpoint", cost_checkpoint)
    g.add_node("user_intervention", user_intervention)
    g.add_node("await_human", await_human_node)

    g.add_edge(START, "failure_recovery")
    g.add_edge("failure_recovery", "clarify")
    # 阶段1 澄清：若 clarifier 判定需用户追问，则挂起到 await_human；用户回答后回流重新并入
    g.add_conditional_edges(
        "clarify",
        lambda s: "await_human" if s.get("needs_clarification") else "decompose",
        {"await_human": "await_human", "decompose": "decompose"},
    )
    g.add_edge("decompose", "retrieve")
    g.add_edge("retrieve", "standardize")
    g.add_edge("standardize", "critique")

    g.add_conditional_edges(
        "critique",
        lambda s: "await_human" if s.get("conflicts") and not s.get("verdicts") else "cost_checkpoint",
        {"await_human": "await_human", "cost_checkpoint": "cost_checkpoint"},
    )
    # await_human 为统一 HITL 挂起点：按 interrupt_reason 回流（clarify → 澄清合并；critique → 裁决收敛）
    g.add_conditional_edges(
        "await_human",
        lambda s: "clarify" if s.get("interrupt_reason") == "clarify" else "critique",
        {"clarify": "clarify", "critique": "critique"},
    )
    g.add_conditional_edges(
        "cost_checkpoint",
        lambda s: "user_intervention" if s.get("token_used", 0) > s.get("token_budget", 0) * 0.9 else "report",
        {"user_intervention": "user_intervention", "report": "report"},
    )
    g.add_edge("user_intervention", "report")
    g.add_edge("report", END)

    return g.compile(checkpointer=checkpointer, interrupt_before=["await_human", "user_intervention"])
```

> **对齐说明（§4.2.8 / HLD §7.1）**：`intent_router` **不作为图节点**。意图路由能力属 API 层独立接口（§4.2.8 `/intent/classify`，M2 接线），仅当判别结果为"研究"时才创建 ResearchRun 并启动本图。M0 占位实现单元在 `nodes/planner.py`（`intent_router` 函数，不入编排图）；M2 落地时独立为 `nodes/intent_router.py` 供 `/intent/classify` 复用。

### 6.4 节点实现规范

每个节点函数遵循统一签名：

```python
async def <node_name>(state: ResearchState, *, deps: NodeDeps) -> dict[str, Any]:
    """节点入口。返回对 state 的部分更新（merge 到全局 state）。"""
```

> **M0 占位签名**：M0 阶段节点为 `async def run(state: ResearchState) -> dict[str, Any]`（不注入 `deps`，无真实 LLM 调用），由 `_base.instrument` 统一包装；M1 按本节签名升级为 `async def <node>(state, *, deps: NodeDeps) -> dict` 并接通 §7.5 `LLMClient`。

`NodeDeps` 通过 `RunnableConfig` 的 `config["configurable"]` 注入：

```python
# 引用 §7.5 LLMClient（业务门面）—— 节点唯一 LLM 入口
from app.provider.client import LLMClient

class NodeDeps(BaseModel):
    run_id: str
    team_id: str
    trace_id: str
    db_session: AsyncSession
    event_bus: EventBus
    llm: LLMClient                     # §7.5 门面：complete / complete_structured / stream_section
    quota_tracker: QuotaTracker
    audit: AuditLogger
    prompt_registry: PromptRegistry
    sse: ReportSSE | None = None       # 可选：Reporter 流式推送通道
    langchain_tracer: BaseCallbackHandler | None = None
```

> **P1 对齐说明**：节点不再持有 `provider_factory`。主备切换、熔断、用量归集全部封装在 `LLMClient._chain` 内部（装配见 §7.4 `ProviderFactory.build_client`），对节点透明；配置加载见 §2.2.4 `settings.llm_primary_model`。

节点执行前统一包装器（`run_stage`）负责：

1. 绑定日志上下文（`log_context(run_id=..., team_id=..., stage=...)`，退出复位，见 §12.1.2）
2. 写 `stages` 行（status=running）
3. 打 `stage.started` 日志并推同名 WS 事件
4. 设置 `current_stage`
5. 调用节点函数
6. 合并 state 更新
7. token 用量累加（从 `LLMResponse.token_usage`）
8. 成功打 `stage.finished`、异常打 `stage.failed`（含 error_code）并推 WS；重试打 `node.retry`
9. 写 `stages` 行（status 更新）

> **统一埋点要求（§12.1）**：节点函数内**不得**自创日志事件名或 `print`，只打 §12.1.4 事件字典内事件；高频/过程细节用 DEBUG，生命周期与降级用 INFO/WARNING。Clarifier/Critic 等含"判定"的节点，除工程日志外还须经 `deps.audit.log("decision.xxx", ...)` 落**决策留档**（§9.3），供结论溯源。LLM 调用的元数据/失败留痕由 `LLMClient` 统一打点，节点不重复记录。

NodeDeps 组装由 OrchestratorService 完成——运行前从全局装配取 `app.state.llm_client`（§6.7），同一 run 的所有节点共享同一 `LLMClient` 实例；`team_id` / `trace_id` 由 run 上下文写入。Celery worker 侧（researcher 任务）通过 `get_worker_llm_client()` 获取常驻门面（§6.5.4），日志上下文由 `TracedTask` 基类从消息头恢复（§12.1.2）。

### 6.5 关键节点详细说明

#### 6.5.1 failure_recovery

启动时检查上一轮是否中断：

```python
async def failure_recovery(state: ResearchState, *, deps: NodeDeps) -> dict:
    last = await deps.db_session.get(ResearchRun, state["run_id"])
    if last.status == "running" and last.current_stage:
        # 上一轮崩溃，从 checkpoint 恢复
        await deps.audit.log("recovery.detected", target_id=state["run_id"], payload={"stage": last.current_stage})
    return {"current_stage": "clarify"}
```

#### 6.5.2 clarifier

澄清判定采用"标记 + 统一 await_human 挂起"模型：节点**不**自行调用 `interrupt()`，而是当需要追问时设置 `needs_clarification=True` 返回，由状态图（§6.3）路由到 `await_human`；用户回答后经 `resume` 恢复，回流到 clarify 节点合并答案。

```python
async def clarifier(state: ResearchState, *, deps: NodeDeps) -> dict:
    if state.get("clarification"):
        return {"needs_clarification": False, "current_stage": "clarify"}  # 已澄清，跳过

    prompt = deps.prompt_registry.get("clarifier.v1")   # locale 已并入 registry key（clarifier.v1.zh-CN）
    result = await deps.llm.complete_structured(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": state["question"]}],
        schema=ClarificationSchema,
        max_tokens=800,
        tags=["clarify"],
    )
    if result.requires_user_input:
        # 交给状态图挂起（await_human + interrupt_before），用户响应经 resume 注入 human_input
        interrupt_payload = {"questions": [q.model_dump() for q in result.questions], "defaults": result.defaults}
        await deps.event_bus.publish(stage="clarify", type="interrupt.requested", payload=interrupt_payload)
        return {
            "needs_clarification": True,
            "interrupt_reason": "clarify",
            "interrupt_payload": interrupt_payload,
            "current_stage": "clarify",
        }

    clarification = {**result.defaults, **result.structured_question}
    return {
        "clarification": clarification,
        "needs_clarification": False,
        "current_stage": "clarify",
    }
```

Clarifier 必须产出 `ClarificationSchema`：

```python
class ClarificationSchema(BaseModel):
    requires_user_input: bool
    questions: list[ClarificationQuestion] = []
    defaults: dict = {}
    structured_question: dict   # 目标、范围、关键概念、约束

class ClarificationQuestion(BaseModel):
    key: str
    text: str
    options: list[str] = []
    recommended: int | None = None
```

#### 6.5.3 sub_questioner

```python
async def sub_questioner(state: ResearchState, *, deps: NodeDeps) -> dict:
    prompt = deps.prompt_registry.get("sub_questioner.v1")
    template = await deps.db_session.get(Template, state["template_id"])
    result = await deps.llm.complete_structured(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "question": state["clarification"],
                "template": template.outline,
                "tier": state["tier"],
            })},
        ],
        schema=SubQuestionListSchema,
    )
    # 落库
    sq_records = []
    for sq in result.sub_questions:
        rec = SubQuestion(id=generate_ulid("subq"), run_id=state["run_id"], question=sq.question,
                          depends_on=sq.depends_on, status="pending")
        sq_records.append(rec)
        deps.db_session.add(rec)
    await deps.db_session.commit()
    return {
        "sub_questions": [serialize(r) for r in sq_records],
        "current_stage": "decompose",
    }
```

约束：子问题数受档位控制（快速 ≤3 · 标准 ≤5 · 深度 ≤8 · 极致 ≤12）。

#### 6.5.4 researcher_fan_out（扇出）

```python
async def researcher_fan_out(state: ResearchState, *, deps: NodeDeps) -> dict:
    """将子问题按依赖图分批并行派发给 Celery 任务。"""
    subqs = state["sub_questions"]
    # 拓扑分层
    layers = topological_layers(subqs, key=lambda s: s["id"], deps=lambda s: s["depends_on"])

    all_evidence: list[EvidenceDict] = list(state.get("evidence", []))
    for layer in layers:
        # 每层并行 dispatch
        tasks = [
            celery_app.send_task(
                "researcher.run",
                kwargs={
                    "run_id": state["run_id"],
                    "sub_question": sq,
                    "tier": state["tier"],
                    "checkpoint_token_budget": remaining_budget(state, all_evidence),
                },
                queue="retrieval",
            )
            for sq in layer
        ]
        results = await asyncio.gather(*[task.get(timeout=300) for task in tasks])
        all_evidence.extend([r for r in results if r])

    return {"evidence": all_evidence, "current_stage": "retrieve"}
```

`tasks_retrieval.py` 中的 `researcher.run` 任务：

```python
@celery_app.task(name="researcher.run", bind=True, max_retries=1)
def researcher_run(self, *, run_id: str, sub_question: dict, tier: str, checkpoint_token_budget: int):
    """每个 Researcher 实例独立执行搜索-读-追问循环。"""
    return asyncio.run(_researcher_run_async(run_id, sub_question, tier, checkpoint_token_budget))

async def _researcher_run_async(run_id, sub_question, tier, budget) -> dict:
    db = AsyncSessionLocal()
    bus = EventBus()
    # Celery worker 启动时用 ProviderFactory.build_client(settings, usage_tracker) 装配并常驻
    llm: LLMClient = get_worker_llm_client()
    max_rounds = TIER_MAX_ROUNDS[tier]
    sub_evidence: list[dict] = []
    seen_fingerprints: set[str] = set()
    current_query = sub_question["question"]

    for round_idx in range(max_rounds):
        if budget_exhausted(budget, sub_evidence):
            break
        results = await search.search(current_query, limit=10)
        for r in results:
            content = await fetcher.fetch(r.url)
            fp = fingerprint(r.url, content)
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
            ev = await persist_evidence(db, run_id, sub_question["id"], r, content)
            sub_evidence.append(serialize(ev))
            await bus.publish(
                stage="retrieve",
                type="evidence.fetched",
                payload={"sub_question_id": sub_question["id"], "evidence": sub_evidence[-1]},
            )
        if not await should_followup(llm, sub_question["question"], sub_evidence):
            break
        resp = await llm.complete(LLMRequest(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_registry.get("researcher.followup")},
                {"role": "user", "content": json.dumps({
                    "original": sub_question["question"],
                    "evidence": sub_evidence[-5:],
                }, ensure_ascii=False)},
            ],
            max_tokens=300,
        ))
        current_query = resp.content
    return sub_evidence
```

> `should_followup` 辅助函数内部同样经 `llm.complete_structured(schema=FollowUpDecision, ...)` 判定（三档：已充分 / 需换角度追问 / 需新来源），判定结果落 `audit_log`。

#### 6.5.5 standardizer

```python
async def standardizer(state: ResearchState, *, deps: NodeDeps) -> dict:
    raw = state.get("evidence", [])
    # 跨子问题去重（按 fingerprint）
    deduped = dedupe_by_fingerprint(raw)
    classified: list[dict] = []
    for ev in deduped:
        credibility = classify_credibility(ev)
        source_level = classify_source_level(ev["domain"])
        classified.append({**ev, "credibility": credibility, "source_level": source_level})
    classified.sort(key=lambda e: (e["credibility"], e["relevance_score"]), reverse=True)
    return {"standardized_evidence": classified, "current_stage": "standardize"}
```

#### 6.5.6 critic（循环收敛）

```python
CRITIC_BUDGET_RATIO = 0.30  # 占档位 token 预算 30%
MAX_CRITIC_ITERATIONS = 3

async def critic(state: ResearchState, *, deps: NodeDeps) -> dict:
    material = state["standardized_evidence"]
    claims = state.get("report_claims") or await generate_draft_claims(
        state["report_outline"], material, deps.llm,
    )
    conflicts: list[dict] = list(state.get("conflicts", []))
    verdicts: list[dict] = list(state.get("verdicts", []))

    for iteration in range(MAX_CRITIC_ITERATIONS):
        new_conflicts = await detect_conflicts(claims, material, deps.llm)
        for c in new_conflicts:
            if is_resolvable(c):
                claims = resolve_by_authority(claims, c, material)
            else:
                conflicts.append(serialize_conflict(c))
                await persist_conflict(deps.db_session, state["run_id"], c)
                await deps.event_bus.publish(
                    stage="critique", type="conflict.detected", payload=serialize_conflict(c),
                )
        # 三类收敛信号（任一触发即停）
        if not new_conflicts:
            break
        if state.get("token_used", 0) > state["token_budget"] * CRITIC_BUDGET_RATIO:
            break

    if conflicts and not verdicts:
        # 需要用户裁决 → 中断（await_human 依据 interrupt_reason="critique" 回流本节点收敛）
        return {
            "conflicts": conflicts,
            "report_claims": claims,
            "interrupt_reason": "critique",
            "interrupt_payload": {"conflict_ids": [c["id"] for c in conflicts]},
            "current_stage": "critique",
        }
    return {"conflicts": conflicts, "verdicts": verdicts, "report_claims": claims, "current_stage": "critique"}
```

#### 6.5.7 reporter（流式生成）

```python
async def reporter(state: ResearchState, *, deps: NodeDeps) -> dict:
    outline = state.get("report_outline") or default_outline(state["template_id"])
    accumulated = ""
    for section in outline:
        async for text in deps.llm.stream_section(
            model="gpt-4o",
            messages=build_section_messages(
                prompt=deps.prompt_registry.get("reporter.section"),
                section=section,
                claims=state["report_claims"],
                conflicts=state.get("conflicts", []),
                limitations=collect_limitations(state),
            ),
            tags=["report"],
            on_token=lambda delta: push_report_chunk(deps, state["run_id"], delta),
        ):
            accumulated += text
    report = await persist_report(
        deps.db_session, state["run_id"], outline, state, accumulated,
    )
    await deps.event_bus.publish(
        stage="report", type="report.finished", payload={"report_id": report.id},
    )
    return {"report_draft": accumulated, "current_stage": "report"}
```

> `stream_section` 每次产出为一个文本块（`str`）；逐 token 实时推送通过 `on_token` 回调挂接 `deps.sse`（§8.4）实现，`push_report_chunk` 内部维护已推送字数以计算 position。

#### 6.5.8 cost_checkpoint

```python
async def cost_checkpoint(state: ResearchState, *, deps: NodeDeps) -> dict:
    used = state.get("token_used", 0)
    budget = state.get("token_budget", 1)
    ratio = used / budget
    if ratio > 0.9:
        await deps.event_bus.publish(
            stage="cost_checkpoint", type="cost.warning",
            payload={"level": "danger", "used": used, "budget": budget, "ratio": ratio},
        )
    elif ratio > 0.7:
        await deps.event_bus.publish(
            stage="cost_checkpoint", type="cost.warning",
            payload={"level": "warning", "used": used, "budget": budget, "ratio": ratio},
        )
    return {}
```

#### 6.5.9 user_intervention

```python
async def user_intervention(state: ResearchState, *, deps: NodeDeps) -> dict:
    action = (state.get("human_input") or {}).get("action")
    payload = (state.get("human_input") or {}).get("payload", {})
    if action == "ask_followup":
        await celery_app.send_task(
            "researcher.followup",
            kwargs={
                "run_id": state["run_id"],
                "sub_question_id": payload["sub_question_id"],
                "question": payload["question"],
            },
        )
    elif action == "exclude_evidence":
        await deps.db_session.execute(
            update(Evidence)
            .where(Evidence.id == payload["evidence_id"])
            .values(excluded_by_user=True)
        )
        await deps.db_session.commit()
    elif action == "revert_stage":
        await deps.db_session.execute(
            update(Stage)
            .where(Stage.run_id == state["run_id"], Stage.name == payload["stage"])
            .values(status="pending")
        )
        await deps.db_session.commit()
    return {}
```

#### 6.5.10 await_human（统一 HITL 挂起：澄清 / 分歧裁决）

`await_human` 是**研究主链上的用户介入挂起点**，经 `interrupt_before` 触发，依据 `interrupt_reason` 分流：`clarify` 时合并用户澄清答案；`critique` 时合并裁决（verdicts）。随后图按 §6.3 条件回流。

> 与 [§6.5.9 `user_intervention`](#659-user_intervention) 的分工：`await_human` 响应**流程自身判定**的挂起（澄清追问、分歧裁决）；`user_intervention` 响应**用户主动介入**（追问/剔除证据/打回重审，经 `intervene` 或成本超限触发）。两者读同一 `state.human_input`，由 `resume` 注入。

```python
async def await_human_node(state: ResearchState, *, deps: NodeDeps) -> dict:
    reason = state.get("interrupt_reason", "critique")
    answers = (state.get("human_input") or {}).get("answers", {})

    if reason == "clarify":
        # 用户回答了澄清问题 → 回流 clarify 合并结构化问题
        defaults = state.get("interrupt_payload", {}).get("defaults", {})
        return {
            "clarification": {**defaults, **answers},
            "needs_clarification": False,
            "current_stage": "clarify",
        }

    # reason == "critique"：用户裁决分歧 → 回流 critique 收敛
    verdicts = parse_verdicts(answers, run_id=state["run_id"], user_id=deps.audit.current_user_id())
    await deps.event_bus.publish(stage="critique", type="conflict.verdicts", payload={"count": len(verdicts)})
    return {"verdicts": verdicts, "current_stage": "critique"}
```

> `interrupt_reason` 由上游节点写入：clarifier 置 `"clarify"`（§6.5.2），critic 在 `conflicts and not verdicts` 时默认保持 `"critique"` 分支进入（critic 返回时需携带 `interrupt_reason="critique"`，见 §6.5.6 收敛出口注释）。

### 6.6 Orchestrator 服务入口

> 实现落位：服务入口实际为 `backend/app/orchestrator/executor.py`（`run_research_async` / `resume_research_async`），checkpointer 工厂在 `app/orchestrator/checkpoint.py`；下方为设计期骨架，函数签名以代码为准。

```python
# backend/app/orchestrator/executor.py（设计期文件名 runner.py，已更名）
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from app.orchestrator.graph import build_graph
from app.provider.client import LLMClient

class OrchestratorService:
    def __init__(self, *, db_session_factory, event_bus, llm_client: LLMClient):
        self.db_session_factory = db_session_factory
        self.event_bus = event_bus
        self.llm_client = llm_client        # §7.5 门面；供 NodeDeps 注入
        self._graph = None

    async def start(self):
        saver = AsyncPostgresSaver.from_conn_string(str(settings.database_url))
        await saver.setup()  # 首次运行建表
        self._graph = await build_graph(saver)

    async def run(self, run_id: str, inputs: dict):
        config = {"configurable": {"thread_id": run_id}}
        await self._graph.ainvoke(inputs, config=config)

    async def resume(self, run_id: str, human_input: dict):
        config = {"configurable": {"thread_id": run_id}}
        # human_input 写入 state.human_input；await_human 恢复后按 interrupt_reason 分流
        await self._graph.ainvoke(Command(resume={"human_input": human_input}), config=config)

    async def cancel(self, run_id: str):
        async with self.db_session_factory() as db:
            await db.execute(
                update(ResearchRun).where(ResearchRun.id == run_id).values(
                    status="cancelled", finished_at=func.now(),
                )
            )
            await db.commit()
        await celery_app.send_task("orchestrator.cancel_run", kwargs={"run_id": run_id})
```

### 6.7 编排层生命周期

```python
# backend/app/main.py
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # LLMClient 全局装配：主备切换 + 熔断 + 用量归集对业务透明（§7.4 build_client）
    llm_client = ProviderFactory.build_client(settings, app.state.usage_tracker)
    orchestrator = OrchestratorService(
        db_session_factory=async_sessionmaker(engine, expire_on_commit=False),
        event_bus=app.state.event_bus,
        llm_client=llm_client,
    )
    await orchestrator.start()
    app.state.orchestrator = orchestrator
    app.state.llm_client = llm_client
    yield
    await orchestrator.shutdown()
```

发起 Run 由 API 层调用 `orchestrator.run(run_id, initial_state)`；LangGraph 自动从 PostgreSQL checkpoint 恢复崩溃前状态。

---

## 7. Provider Adapter 详细设计

> **选型基线（2026-09 决策）**：LLM Provider 层以 LangChain v1+ `BaseChatModel` 作为底层适配，业务边界由自研 `LLMProvider` Protocol 收敛；自研层只保留 LangChain 不提供的能力（主备熔断、团队级用量归集、成本守门联动）。决策记录见 §16.3。

### 7.1 协议抽象

```python
# backend/app/provider/base.py
from typing import Protocol, AsyncIterator, runtime_checkable, Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class LLMRequest(BaseModel):
    """业务层对 LLM 的统一请求 DTO，与具体 provider 解耦。"""
    model: str | None = None            # None 表示使用 Provider 默认模型
    messages: list[dict[str, Any]]      # LangChain Message 字典格式
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[dict] | None = None     # OpenAI tools schema
    response_schema: type[BaseModel] | None = None
    trace_id: str | None = None
    team_id: str | None = None          # 用于用量归集

class LLMResponse(BaseModel):
    content: str
    structured: BaseModel | None = None
    tool_calls: list[dict] = []
    token_usage: TokenUsage
    model: str
    finish_reason: str
    latency_ms: int

class StreamChunk(BaseModel):
    delta: str
    finish_reason: str | None = None
    token_usage: TokenUsage | None = None

@runtime_checkable
class LLMProvider(Protocol):
    """业务层只依赖此 Protocol；不直接引用 LangChain 类型。"""
    name: str
    def get_runnable(self) -> Runnable: ...
    async def complete(self, req: LLMRequest) -> LLMResponse: ...
    def stream(self, req: LLMRequest) -> AsyncIterator[StreamChunk]: ...
    async def health(self) -> bool: ...
```

### 7.2 OpenAI 适配器（基于 LangChain v1）

```python
# backend/app/provider/openai.py
import time
from langchain_openai import ChatOpenAI
from langchain_core.messages import convert_to_messages
from langchain_core.runnables import Runnable
from app.provider.base import LLMProvider, LLMRequest, LLMResponse, TokenUsage

class OpenAIProvider:
    name = "openai"

    def __init__(self, *, model: str = "gpt-4o", api_key: str | None = None,
                 base_url: str | None = None, timeout: float = 60.0):
        self.chat: ChatOpenAI = ChatOpenAI(
            model=model, api_key=api_key, base_url=base_url,
            timeout=timeout, temperature=0.7,
        )

    def get_runnable(self) -> Runnable:
        return self.chat

    async def complete(self, req: LLMRequest) -> LLMResponse:
        runnable: Runnable = self.chat
        if req.tools:
            runnable = runnable.bind_tools(req.tools)
        if req.response_schema:
            # LangChain v1 原生 JSON Schema 结构化输出
            runnable = runnable.with_structured_output(req.response_schema)
        if req.max_tokens:
            runnable = runnable.with_config({"max_tokens": req.max_tokens})

        msgs = convert_to_messages(req.messages)
        started = time.monotonic()
        result = await runnable.ainvoke(msgs, config={"run_id": req.trace_id})
        latency_ms = int((time.monotonic() - started) * 1000)

        # LangChain v1 AIMessage 自带 usage_metadata
        usage_meta = getattr(result, "usage_metadata", None) or {}
        token_usage = TokenUsage(
            prompt_tokens=usage_meta.get("input_tokens", 0),
            completion_tokens=usage_meta.get("output_tokens", 0),
            total_tokens=usage_meta.get("total_tokens", 0),
        )

        if req.response_schema and isinstance(result, BaseModel):
            structured = result
            content = result.model_dump_json()
        else:
            structured = None
            content = getattr(result, "content", "") or ""

        tool_calls = [tc.model_dump() for tc in (getattr(result, "tool_calls", []) or [])]
        return LLMResponse(
            content=content,
            structured=structured,
            tool_calls=tool_calls,
            token_usage=token_usage,
            model=self.chat.model_name,
            finish_reason=(getattr(result, "response_metadata", {}) or {}).get("finish_reason", "stop"),
            latency_ms=latency_ms,
        )
```

### 7.3 Anthropic 适配器

```python
# backend/app/provider/anthropic.py
from langchain_anthropic import ChatAnthropic

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, model: str = "claude-sonnet-4-5", **kwargs):
        self.chat = ChatAnthropic(model=model, **kwargs)

    def get_runnable(self):
        return self.chat

    async def complete(self, req: LLMRequest) -> LLMResponse:
        # 与 OpenAIProvider 结构对齐，复用同一 Usage / Structured 模式
        ...
```

### 7.4 主备切换、熔断与用量归集

> 实现落位：主备配对装配实际在 `backend/app/provider/registry.py`（`_ProviderRegistry.build_pair`，由 `lifespan` 注入）；熔断在 `app/provider/circuit_breaker.py`，用量归集在 `app/provider/usage.py`（无独立 observability 包）。

```python
# backend/app/provider/registry.py（设计期文件名 factory.py，已更名）
from langchain_core.runnables import Runnable, RunnableConfig
from app.provider.usage import UsageTracker
from app.provider.circuit_breaker import CircuitBreaker
from app.provider.client import LLMClient

class ProviderFactory:
    """构造带主备、熔断、用量归集的可运行链，并装配为业务门面 LLMClient。"""

    @staticmethod
    def build_chain(
        *,
        primary: LLMProvider,
        backup: LLMProvider | None,
        usage_tracker: UsageTracker,
        team_id: str,
    ) -> Runnable:
        primary_runnable = primary.get_runnable()
        runnables = [primary_runnable]
        if backup:
            runnables.append(backup.get_runnable())
        # LangChain 原生 with_fallbacks + 我们自研的 CircuitBreaker 包装
        chain = primary_runnable.with_fallbacks(runnables[1:])
        chain = CircuitBreaker.wrap(chain, fail_threshold=5, reset_seconds=30)
        chain = UsageTracker.wrap(chain, usage_tracker, team_id=team_id)
        return chain

    @staticmethod
    def build_client(settings, usage_tracker) -> LLMClient:
        """lifespan / Celery worker 的统一装配入口，返回业务门面。"""
        primary = OpenAIProvider(model=settings.llm_primary_model)
        backup = (
            AnthropicProvider(model=settings.llm_backup_model)
            if settings.llm_backup_model else None
        )
        chain = ProviderFactory.build_chain(
            primary=primary, backup=backup,
            usage_tracker=usage_tracker, team_id=settings.team_id or "default",
        )
        return LLMClient(chain=chain, primary=primary, backup=backup)
```

熔断策略：

- 失败阈值：连续 5 次失败 / 1 分钟
- 恢复探测：冷却 30 秒后放行 1 个请求
- 触发熔断后主备自动切换，记录 `audit_log`

### 7.5 业务门面 LLMClient（Orchestrator 节点唯一 LLM 入口）

**背景**：§7.7 约束业务层不得直接 import `langchain_core`。为此 Orchestrator 节点不直接持有 Runnable，而是统一通过 `LLMClient` 门面调用。节点代码中的 `deps.llm` 即此类型（见 §6.4 NodeDeps）。

```python
# backend/app/provider/client.py
from typing import Any, AsyncIterator, Awaitable, Callable
from langchain_core.messages import convert_to_messages
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel
from app.provider.base import LLMProvider, LLMRequest, LLMResponse, TokenUsage

class LLMClient:
    """业务门面：包装主备 Runnable 链，屏蔽 LangChain 类型与调用细节。

    唯一允许 import langchain_core 的出口；节点函数只面向本类 API。
    """

    def __init__(self, *, chain: Runnable, primary: LLMProvider, backup: LLMProvider | None = None):
        self._chain = chain                # ProviderFactory.build_chain() 产物（主备+熔断+用量）
        self._primary = primary
        self._backup = backup

    async def complete(self, req: LLMRequest) -> LLMResponse:
        """非结构化/单轮调用。req.messages 由节点按 LangChain Message 字典约定传入。"""
        msgs = convert_to_messages(req.messages)
        config = self._config(req)
        result = await self._chain.ainvoke(msgs, config=config)
        return self._to_response(result, req)

    async def complete_structured(
        self, *, model: str, messages: list[dict], schema: type[BaseModel],
        max_tokens: int | None = None, tags: list[str] | None = None,
    ) -> BaseModel:
        """结构化输出：内部走 with_structured_output，返回 schema 实例。"""
        req = LLMRequest(
            model=model, messages=messages, response_schema=schema,
            max_tokens=max_tokens, trace_id=self._current_trace_id(),
        )
        config = self._config(req, extra_tags=tags)
        msgs = convert_to_messages(messages)
        # chain 已包装 with_fallbacks，此处临时叠加 schema 绑定
        runnable = self._chain.with_structured_output(schema)
        result = await runnable.ainvoke(msgs, config=config)
        return result if isinstance(result, schema) else schema.model_validate(result)

    def stream_section(
        self, *, model: str, messages: list[dict], tags: list[str] | None = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> AsyncIterator[str]:
        """节选流式生成：逐 token 产出；on_token 可选挂接到 LangGraph stream writer。"""

        async def _gen():
            req = LLMRequest(model=model, messages=messages, trace_id=self._current_trace_id())
            msgs = convert_to_messages(messages)
            config = self._config(req, extra_tags=tags)
            async for chunk in self._chain.astream(msgs, config=config):
                delta = getattr(chunk, "content", "") or ""
                if not delta:
                    continue
                if on_token:
                    await on_token(delta)
                yield delta

        return _gen()

    def _config(self, req: LLMRequest, *, extra_tags: list[str] | None = None) -> RunnableConfig:
        tags = ["node"] + (extra_tags or [])
        return RunnableConfig(
            run_id=req.trace_id,
            tags=tags,
            metadata={"team_id": req.team_id, "model": req.model},
        )

    @staticmethod
    def _current_trace_id() -> str | None:
        return get_current_trace_id()          # 从 ContextVar 取 OpenTelemetry span 关联

    @staticmethod
    def _to_response(result, req: LLMRequest) -> LLMResponse:
        # 与 §7.2 OpenAIProvider.complete 相同的解析逻辑，抽取为共享函数
        ...
```

**设计要点**：

- 节点函数只 import `LLMClient`，传入/获取 `deps.llm`，三套调用：`complete`（非结构化）、`complete_structured`（JSON Schema）、`stream_section`（流式节选，供 Reporter 用）
- 主备切换、熔断、用量归集封装在 `chain` 内，对节点透明
- 节点需要向 LangGraph stream writer 推 token 时，通过 `on_token` 回调挂接，不在节点内手写 `astream`（见下节示例）
- `ProviderFactory.build_client()` 装配 LLMClient 供 lifespan / Celery worker 注入

### 7.6 与 LangGraph 节点的协作（经 LLMClient）

```python
# backend/app/orchestrator/nodes/report_helpers.py
from app.provider.client import LLMClient

async def stream_report_section(state: ResearchState, *, deps: NodeDeps, section: dict) -> str:
    """Reporter 节点调用示例：LLMClient + LangGraph stream writer 双通道。"""
    writer = get_stream_writer()

    async def on_token(delta: str):
        writer({"stage": "report", "type": "token.delta", "delta": delta})

    chunks: list[str] = []
    async for chunk in deps.llm.stream_section(
        model=state.get("report_model", "gpt-4o"),
        messages=build_section_messages(section, state),
        tags=["report"],
        on_token=on_token,
    ):
        chunks.append(chunk)
    return "".join(chunks)
```

结构化节点调用示例：

```python
# backend/app/orchestrator/nodes/_helpers.py
async def llm_structured(deps: NodeDeps, *, schema, messages, tags=None):
    return await deps.llm.complete_structured(
        model="gpt-4o-mini", messages=messages, schema=schema,
        max_tokens=1200, tags=tags or ["stage_node"],
    )
```

### 7.7 Token 计量与可观测

```python
# backend/app/provider/usage.py（设计期规划为 app/observability/usage.py，实际归入 provider 包）
from langchain_core.callbacks import BaseCallbackHandler
from prometheus_client import Counter

class UsageTracker:
    def __init__(self, prom_registry):
        self.usage_total = Counter(
            "llm_token_usage_total",
            "累计 token 用量",
            ["model", "stage", "team_id", "run_id"],
            registry=prom_registry,
        )

    @staticmethod
    def wrap(runnable: Runnable, tracker: "UsageTracker", *, team_id: str) -> Runnable:
        class _CB(BaseCallbackHandler):
            def on_llm_end(self, response, *, run_id, **kwargs):
                usage = (response.llm_output or {}).get("token_usage") or {}
                meta = getattr(response, "usage_metadata", None) or {}
                tracker.usage_total.labels(
                    model=meta.get("model", "unknown"),
                    stage=kwargs.get("tags", ["unknown"])[0] if kwargs.get("tags") else "unknown",
                    team_id=team_id,
                    run_id=str(run_id),
                ).inc(meta.get("total_tokens", usage.get("total_tokens", 0)))
        return runnable.with_config({"callbacks": [_CB()]})
```

实时成本链路遵循 HLD §10.1：Provider Adapter → Pushgateway → Prometheus → Metrics Exporter（5s 间隔 PromQL 聚合）→ EventBus → WebSocket。

### 7.8 LangChain 范围裁剪

| 引入 | 不引入 |
|---|---|
| `langchain_core.BaseChatModel` / `Runnable` / `AIMessage` | `langchain_community.document_loaders`（M5 再引） |
| `langchain_openai.ChatOpenAI` | `langchain.agents.AgentExecutor`（我们自己用 LangGraph 编排） |
| `langchain_anthropic.ChatAnthropic` | `langchain.vectorstores.*`（M3 pgvector 直连） |
| `langchain_core.runnables.with_fallbacks` | `langchain.retrievers.*`（M3 再评估） |
| `langchain_core.runnables.with_structured_output` | `langchain_community.tools.*`（M5 再评估） |

业务层（Orchestrator / Agent 节点 / Repository / API Handler）**不直接 import langchain_core**；唯一允许 import 的位置是 `backend/app/provider/` 目录内部。

---

## 8. 实时推送层详细设计

### 8.1 整体架构

```
                  ┌─────────────────────────────┐
                  │      EventBus (内存)          │
                  │  Redis Pub/Sub 跨进程转发     │
                  └──────────────┬───────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            ↓                    ↓                    ↓
       WS Hub (API 节点)    WS Hub (API 节点)     ...
            │                    │
            ↓                    ↓
       浏览器连接           浏览器连接
```

- 单进程内：进程内 `EventBus`（asyncio.Queue + dict）
- 跨进程：Redis Pub/Sub（频道命名 `ra:run:{run_id}`）
- EventBus 由 Provider Adapter、Orchestrator 节点、Worker 任务统一调用 `publish()`

### 8.2 Envelope

```python
# backend/app/realtime/envelope.py
class EventEnvelope(BaseModel):
    v: Literal["1.0"] = "1.0"
    event_id: str
    ts: int = Field(default_factory=lambda: int(time.time() * 1000))
    run_id: str
    stage: str
    type: str
    payload: dict
```

### 8.3 WebSocket Hub

> 实现落位：进程内 Hub 实际为 `backend/app/realtime/hub.py`（`RealtimeHub`，频道键 `runs:{run_id}`）；WS 端点在 `app/realtime/ws.py`，另有 `app/realtime/sse.py` 备用。

```python
# backend/app/realtime/hub.py（设计期文件名 ws_hub.py，已更名；类名 RealtimeHub）
class RealtimeHub:
    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = {}  # run_id -> sockets
        self._lock = asyncio.Lock()

    async def register(self, run_id: str, ws: WebSocket):
        async with self._lock:
            self._connections.setdefault(run_id, set()).add(ws)

    async def unregister(self, run_id: str, ws: WebSocket):
        async with self._lock:
            self._connections.get(run_id, set()).discard(ws)

    async def broadcast(self, run_id: str, env: EventEnvelope):
        conns = list(self._connections.get(run_id, []))
        dead = []
        for ws in conns:
            try:
                await ws.send_text(env.model_dump_json())
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(run_id, ws)
```

#### 跨进程转发

API 节点启动时订阅 Redis 频道 `ra:run:*`，收到消息后查找本地连接并转发。本地连接为空时直接丢弃（HTTP 端点的补齐走 `cost/snapshot`）。

#### 断连补齐

客户端断连重连后，先调用 `GET /runs/{id}/cost/snapshot` 补齐成本数据，再订阅 WS 接收后续事件。事件自身用 `event_id` + `ts` 客户端去重。

### 8.4 SSE 推送

```python
# backend/app/realtime/sse.py
from sse_starlette.sse import EventSourceResponse

async def report_stream(request: Request, run_id: str):
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    token = await sse_registry.subscribe(run_id, queue)
    try:
        async def gen():
            while True:
                env = await queue.get()
                if env.type == "done":
                    yield {"event": "done", "data": env.payload, "id": env.event_id}
                    break
                yield {"event": env.type, "data": env.model_dump_json(), "id": env.event_id}
        return EventSourceResponse(gen())
    finally:
        await sse_registry.unsubscribe(run_id, token)
```

---

## 9. 横切模块详细设计

### 9.1 鉴权（JWT + 刷新令牌）

```python
# backend/app/core/security.py
from datetime import datetime, timedelta, timezone
import jwt

ACCESS_TOKEN_TTL = timedelta(minutes=30)
REFRESH_TOKEN_TTL = timedelta(days=7)
JWT_ALG = "HS256"

def create_access_token(user_id: str, team_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "team": team_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + ACCESS_TOKEN_TTL).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALG)

def create_refresh_token(user_id: str, jti: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + REFRESH_TOKEN_TTL).timestamp()),
        "typ": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALG)
```

Refresh token 撤销：维护 Redis 黑名单 `revoked_refresh:{jti}`，TTL = token 剩余寿命。

### 9.2 配额与限流

> 实现落位：档位定义在 `backend/app/quota/tiers.py`（quick/standard/deep/extreme 四档预算，环境变量 `QUOTA_TIER_*` 可调）；预算闸门为编排节点 `app/orchestrator/nodes/cost_checkpoint.py`（超 90% 挂起 user_intervention）。设计期的 `QuotaTracker` 类未单独实现，M2-3 实时成本推送前不建该模块。

```python
# backend/app/quota/tiers.py + app/orchestrator/nodes/cost_checkpoint.py（设计期规划为 quota/tracker.py）
class QuotaTracker:
    """按租户 + 时间窗 + 档位追踪 token 消耗；超阈值阻断研究。"""

    async def check_and_consume(self, *, team_id: str, model: str, estimated_tokens: int) -> bool:
        bucket_key = f"quota:{team_id}:{model}:{current_window()}"
        pipe = redis.pipeline()
        pipe.incrby(bucket_key, estimated_tokens)
        pipe.expire(bucket_key, window_seconds)
        used, _ = await pipe.execute()
        return used <= self._limit(team_id, model)

    def _limit(self, team_id: str, model: str) -> int:
        # 从 team.settings 或 Plan 默认值取
        ...
```

研究发起前预估 token 用量调用 `check_and_consume`；运行中 Provider Adapter 在每次响应回调 `record_usage`（追加实际值，不阻断）。

### 9.3 审计日志

```python
# backend/app/audit/logger.py
class AuditLogger:
    async def log(self, action: str, *, target_type: str, target_id: str,
                  user_id: str | None = None, team_id: str | None = None,
                  payload: dict | None = None, trace_id: str | None = None):
        entry = AuditEntry(
            id=generate_ulid("audit"),
            team_id=team_id or self._current_team_id(),
            user_id=user_id or self._current_user_id(),
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload or {},
            trace_id=trace_id or self._current_trace_id(),
        )
        async with self._session_factory() as db:
            db.add(entry)
            await db.commit()
```

记录范围：

- 用户介入（追问/剔除/打回/裁决，action 见 §12.1.4 `human.intervened`）
- 私域凭证读写
- 模板变更
- 研究运行生命周期（创建/暂停/恢复/取消/完成）
- 鉴权事件（登录/登出/SSO）
- LLM 主备切换与熔断（`llm.fallback.switched` / `llm.circuit.opened`）
- **AI 决策留档**（`decision.*`，M2-8 落地）

**AI 决策留档（结论溯源）**：Clarifier/Critic 等判定节点在产出结论的同时，把"判定输入摘要 + 判定结果 + 理由 + 关联证据/分歧 id"结构化写入 `audit_entries.payload`，`action` 取 `decision.clarify` / `decision.critic` 等（事件字段见 §12.1.4）。它回答"为什么追问 / 为什么收敛成这个结论 / 为什么保留分歧"，与报告引文（M2-7 数据点级溯源）互补：引文解决"结论出自哪条信源"，决策留档解决"Agent 为什么这么判"。

> 审计日志是**合规与溯源的权威记录**，独立于日志采集链路（即使 Loki 不在线也落库），保留 1 年、经 §4.2.12 `/audit` 查询；与工程运行日志、实时事件的分工见 §12.1.7。

### 9.4 通知服务

通知渠道：站内（WebSocket 推送到用户所有打开的页面）、邮件（SendGrid/邮件推送）、Webhook。

```python
# backend/app/notifications/dispatcher.py
class NotificationDispatcher:
    async def notify(self, *, user_id: str, channel: Literal["in_app", "email", "webhook"],
                     template: str, context: dict):
        sub = await self._get_subscription(user_id, channel)
        if not sub:
            return
        if channel == "in_app":
            await in_app_hub.send(user_id, message=render(template, context))
        elif channel == "email":
            await email_client.send(to=sub.address, subject=..., body=render(template, context))
        elif channel == "webhook":
            await httpx.post(sub.url, json=render(template, context))
```

通知场景：

- 研究完成（in_app + email）
- 分歧等待裁决（in_app）
- 配额告警（in_app + email）
- 私域连接器同步失败（email）

---

## 10. 私域连接器设计（M5）

### 10.1 抽象协议

```python
# backend/app/connectors/base.py
class Connector(Protocol):
    type: str
    async def authorize(self, config: dict) -> dict: ...   # 返回授权 URL 或凭证
    async def fetch(self, query: str, scope: dict) -> list[RawDoc]: ...
    async def list_documents(self) -> list[RawDoc]: ...
    async def health_check(self) -> bool: ...
```

### 10.2 凭证加密

> 实现状态（2026-09-13）：连接器为 M5 能力，`app/core/crypto.py` **当前不存在**；仅有配置位 `app/core/config.py` 的 `ENCRYPTION_KEY`（默认空）。下方 Fernet 实现随 M5 连接器落地时在 `app/core/security.py` 或新模块中实现，M1-M2 不提前开发。

```python
# 计划落位：backend/app/core/security.py（M5 随连接器落地；设计期路径 core/crypto.py 不采用）
from cryptography.fernet import Fernet

def encrypt_credential(plain: str) -> bytes:
    f = Fernet(settings.encryption_key.get_secret_value().encode())
    return f.encrypt(plain.encode())

def decrypt_credential(cipher: bytes) -> str:
    f = Fernet(settings.encryption_key.get_secret_value().encode())
    return f.decrypt(cipher).decode()
```

M3+ 切到 KMS（见 HLD §9.2.1）：`CryptoAdapter` 接口保留，注入实现替换。

### 10.3 同步策略

- 全量同步：用户触发 → Celery 任务 → 拉取所有文档 → 写入 `knowledge_items` + 嵌入 → 标记 `last_sync_at`
- 增量同步：每 6 小时 Celery beat → 比对 `updated_at` 增量拉取
- 失败处理：连续 3 次失败标记 `auth_status=expired`，前端提示用户重新授权

---

## 11. 错误处理与重试策略

### 11.1 异常体系

```python
# backend/app/core/exceptions.py（设计期文件名 app/errors.py，实际归入 core 包）
class AppError(Exception):
    code: str
    status: int
    title: str

class NotFoundError(AppError):
    code = "NOT_FOUND"
    status = 404

class QuotaExceededError(AppError):
    code = "QUOTA_EXCEEDED"
    status = 402

class InterventionNotAllowedError(AppError):
    code = "INTERVENE_NOT_ALLOWED"
    status = 409

class ProviderUnavailableError(AppError):
    code = "PROVIDER_UNAVAILABLE"
    status = 503
```

### 11.2 重试策略

| 场景 | 策略 |
|---|---|
| Provider 5xx / 超时 | 指数退避（1s/3s/9s），最多 3 次，失败切熔断 |
| Provider 4xx（如 429 限流） | 退避 30s 后重试 1 次 |
| Celery 任务失败 | 默认重试 1 次，第二次失败入 DLQ + 触发 Orchestrator 异常路径 |
| 数据库连接失败 | SQLAlchemy `pool_pre_ping` 自动重连，无需应用层介入 |
| 检索任务单源抓取失败 | 单条证据失败不影响其他证据，标记 `error` 入库 |
| 用户介入参数错误 | 立即返回 400，不重试 |

### 11.3 LangGraph 错误恢复

任何节点未捕获异常 → 框架写入 checkpoint → 进入 `failure_recovery` 节点：

- 瞬态错误（网络、超时）→ 自动重试当前节点（attempts ≤ 2）
- 业务错误 → 写入 `stages.status=failed` + `error_code`，推 `stage.failed` 事件，暂停 Run 等待用户决策
- 致命错误（如 checkpoint 损坏）→ 标记 Run `failed`，推 `run.finished { status: failed }`，记录 audit

---

## 12. 可观测性

### 12.1 统一日志管理

> 目标：**任意一条 run，凭 `run_id` 可在日志系统一屏捞齐全链路日志**（HTTP → 节点 → Celery 检索任务 → LLM 调用），且每行都带 `trace_id / run_id / team_id / user_id`。日志不是"各节点随手打"，而是全项目统一的横切工程基线，由框架、上下文、事件字典、留痕、采集、治理六部分构成。

#### 12.1.1 框架与统一格式

全项目（API 进程、Celery worker、脚本）统一使用 structlog，单行 JSON 输出到 stdout，由采集侧收集；**禁止** `print` 与裸 `logging` 直接散落使用。

```python
# backend/app/core/logging.py
import structlog

def redact_processor(logger, name, event_dict):
    # 敏感键脱敏：命中即整体掩码，值永不落日志
    sensitive_keys = {"api_key", "authorization", "jwt", "token", "secret",
                      "password", "encryption_key", "credential"}
    for k in list(event_dict.keys()):
        if k.lower() in sensitive_keys:
            event_dict[k] = "***redacted***"
    return event_dict

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,   # 合并 §12.1.2 绑定的上下文字段
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        redact_processor,                          # 统一脱敏，置于渲染之前
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
    logger_factory=structlog.PrintLoggerFactory(),
)

def get_logger(name: str = "research"):
    # 统一命名空间 research.*，便于按业务/第三方区分级别
    return structlog.get_logger(name)
```

#### 12.1.2 上下文贯通（trace_id / run_id 全链路注入）

上下文字段经 `contextvars.ContextVar` 承载，structlog 的 `merge_contextvars` 自动附加到每行日志。**绑定与清理必须成对出现**，用上下文管理器保证异步任务间不串号。

```python
# backend/app/core/context.py
import contextlib
from contextvars import ContextVar

_VARS = {
    "trace_id": ContextVar("trace_id", default=None),
    "run_id": ContextVar("run_id", default=None),
    "team_id": ContextVar("team_id", default=None),
    "user_id": ContextVar("user_id", default=None),
    "stage": ContextVar("stage", default=None),
    "sub_question_id": ContextVar("sub_question_id", default=None),
    "call_id": ContextVar("call_id", default=None),       # 单次 LLM 调用
}

@contextlib.asynccontextmanager
async def log_context(**kwargs):
    # 仅绑定非空字段，退出时按 token 复位，避免跨请求/跨任务污染
    tokens = {k: _VARS[k].set(v) for k, v in kwargs.items() if v is not None and k in _VARS}
    try:
        yield
    finally:
        for k, t in tokens.items():
            _VARS[k].reset(t)
```

三个注入点覆盖全部执行路径：

1. **HTTP 中间件**（API 进程）：请求进入时从 OTEL span 取 `trace_id`，从 auth 中间件取 `user_id/team_id`，从路径/体解析 `run_id`，统一 bind；响应返回后复位。

   ```python
   @app.middleware("http")
   async def context_middleware(request, call_next):
       async with log_context(
           trace_id=get_current_trace_id(),
           user_id=getattr(request.state, "user_id", None),
           team_id=getattr(request.state, "team_id", None),
           run_id=extract_run_id(request),     # /runs/{run_id}/... 路径参数
       ):
           return await call_next(request)
   ```

2. **Celery 任务基类**（worker 进程，无 HTTP 上下文）：发布任务时把 `traceparent` 与业务上下文写入消息头，worker 执行前取出并 bind，保证检索任务日志携带同一 `run_id/trace_id`。

   ```python
   # backend/app/workers/task.py（worker 复用 app 包，见 §3.1 目录约定）
   class TracedTask(Task):
       def apply_async(self, args=None, kwargs=None, **options):
           headers = options.setdefault("headers", {})
           headers["x-traceparent"] = get_current_traceparent()
           headers["x-log-context"] = {"run_id": kwargs.get("run_id"),
                                       "team_id": kwargs.get("team_id")}
           return super().apply_async(args, kwargs, **options)

       def __call__(self, *args, **kwargs):
           ctx = (self.request.headers or {}).get("x-log-context", {})
           with log_context(trace_id=trace_id_from_headers(self.request.headers), **ctx):
               return super().__call__(*args, **kwargs)
   ```

3. **节点包装器 `run_stage`**（§6.4）：调用节点函数前后 bind/复位 `stage`、`sub_question_id`；`LLMClient` 每次调用 bind `call_id`。

> **resume / intervene 的关联**：用户恢复是一次**新 HTTP 请求**（新 `trace_id`），但 `run_id` 不变。跨挂起的关联主键是 `run_id`——同一 run 的澄清前、挂起、恢复后日志在 Loki 中按 `run_id` 聚合；`trace_id` 仅标识单次请求/span。

#### 12.1.3 级别与事件命名规范

级别约定（只描述"要不要人介入/是否异常"，不描述重要性）：

| 级别 | 用于 |
|---|---|
| `ERROR` | 未捕获异常、节点失败、外部依赖不可用导致功能受损 |
| `WARNING` | 已被兜底的降级：重试、主备切换、熔断、配额预警、单源抓取失败 |
| `INFO` | 生命周期里程碑与关键分支：run/stage 起止、resume/intervene、interrupt、fallback |
| `DEBUG` | 高频细节：逐条证据抓取、followup 判定明细、WS 连接 |

事件名采用 **`<domain>.<object>.<verb>`** 点分格式（如 `llm.call.finished`），全小写、稳定不变；可变信息一律进键值字段，**不写进事件名或散文消息**。

#### 12.1.4 事件字典（必打事件清单）

节点 / LLMClient / worker 只允许打字典内事件；新增事件须先登记本表。通道列：**日志**=structlog→Loki，**审计**=AuditLogger→DB（§9.3），**WS**=EventBus→看板（§4.3）。

| 事件名 | 级别 | 通道 | 必带字段 |
|---|---|---|---|
| `run.created` | INFO | 日志+审计 | run_id, team_id, creator_id, tier, template_id |
| `run.paused` / `run.resumed` / `run.cancelled` | INFO | 日志+审计 | run_id, operator_id |
| `run.finished` | INFO | 日志+审计+WS | run_id, status, duration_ms |
| `recovery.detected` | WARNING | 日志+审计 | run_id, recovered_stage |
| `stage.started` | INFO | 日志+WS | run_id, stage, attempt |
| `stage.finished` | INFO | 日志+WS | run_id, stage, duration_ms, token_used |
| `stage.failed` | ERROR | 日志+审计+WS | run_id, stage, attempt, error_code |
| `node.retry` | WARNING | 日志 | run_id, stage, attempt, reason |
| `interrupt.requested` | INFO | 日志+WS | run_id, stage, interrupt_reason |
| `human.intervened` | INFO | 日志+审计 | run_id, action, operator_id |
| `llm.call.finished` | INFO | 日志 | run_id, stage, model, provider, call_id, latency_ms, tokens, fallback |
| `llm.call.failed` | WARNING | 日志 | run_id, stage, model, call_id, error_code, latency_ms, trace_ref |
| `llm.fallback.switched` | WARNING | 日志+审计 | run_id, from_provider, to_provider, reason |
| `llm.circuit.opened` / `llm.circuit.recovered` | WARNING/INFO | 日志+审计 | provider, error_code |
| `researcher.task.started` / `researcher.task.finished` | INFO | 日志 | run_id, sub_question_id, evidence_count, duration_ms |
| `researcher.followup.decided` | DEBUG | 日志+审计 | run_id, sub_question_id, decision |
| `evidence.fetch.failed` | WARNING | 日志 | run_id, sub_question_id, source, error_code |
| `cost.warning` | WARNING | 日志+WS | run_id, used, budget, ratio |
| `quota.exceeded` | WARNING | 日志+审计 | team_id, tier, limit |
| `ws.connected` / `ws.disconnected` | DEBUG | 日志 | user_id, run_id |
| `decision.clarify` | INFO | 审计 | run_id, requires_user_input, question_keys, rationale |
| `decision.critic` | INFO | 审计 | run_id, conflicts_count, resolved, retained, rationale |

> `decision.*` 为 **AI 决策留档**（M2-8 落地）：把 Clarifier/Critic 的判定依据结构化落库，支撑"为什么追问 / 为什么收敛或保留分歧"的结论溯源，详见 §9.3。

#### 12.1.5 LLM 调用留痕与脱敏

LLM 调用是本系统排障与质量复盘的核心现场，区分"元数据"与"内容"两级：

- **元数据（始终记录，走 structlog→Loki）**：`model / provider / stage / call_id / prompt_template_id / token_usage(prompt,completion,total) / latency_ms / status / error_code / fallback`。**不含任何 prompt/completion 文本。**
- **内容（prompt/completion 文本，默认不进日志）**：
  - **失败现场**：`llm.call.failed` 时**必须**留存——把脱敏 + 截断后的请求/响应写入 MinIO 私有桶 `llm-traces/{yyyy-mm-dd}/{run_id}/{call_id}.json`，日志中只放 `trace_ref`（对象键），凭此复现；
  - **成功采样**：按 `settings.llm_trace_sample_rate`（默认 **1%**）对成功调用采样留存，同样脱敏 + 截断后入 MinIO；采样率支持按 team 在 0~10% 内热调覆盖；
  - 截断口径：单段消息 ≤ 2000 字符，单 blob 上限 256KB；用户问题、检索正文等业务数据只进私有桶，**Loki 中永不出现明文**。
- **脱敏**：复用 §12.1.1 `redact_processor`（API key / Bearer / JWT / secret 等）；写 MinIO 前对内容再跑同一脱敏。
- 留痕总开关 `settings.llm_trace_enabled`：关闭时仅留元数据（元数据不可关）。

#### 12.1.6 采集、检索与治理

**采集拓扑**：应用/worker 输出 stdout JSON → DaemonSet 侧 **Vector** 采集、打环境标签 → 写入 **Loki**；trace 经 OTLP 入 Jaeger；指标入 Prometheus（§12.2/§12.3）。

**检索工作流**：排障时先从报错/看板拿到 `run_id`，在 Loki 以 `{app="research"} |= "run_01HZ..."` 捞齐该 run 全链路；需要单次请求细节时按行内 `trace_id` 跳 Jaeger 看 span；需要 LLM 失败原文时取 `trace_ref` 到 MinIO 拉 blob。

**治理默认值**（均可经 `settings` / 运维配置调整）：

| 项 | 默认 |
|---|---|
| Loki 运行日志保留 | 热存 7 天 → 归档 90 天，到期删除 |
| MinIO `llm-traces` 保留 | 30 天生命周期自动删除 |
| Jaeger trace 保留 | 7 天（M1-M2 量小全量；M3 起按负载转头部采样） |
| `audit_entries`（DB） | 保留 1 年 |
| LLM 内容采样率 | 1%，按 team 0~10% 热调 |
| 日志级别 | dev=DEBUG；staging=INFO；prod：第三方库 WARNING，应用事件字典（§12.1.4）保留 INFO 以保证 run/stage 可查 |
| 单 run 提级 | 运维经内部开关对指定 run 置 debug，节点包装器读取后该 run 上下文降为 DEBUG，用于疑难复现 |
| 级别热调 | 支持不重启调整全局/命名空间级别 |

#### 12.1.7 工程日志 / 业务审计 / 实时事件 三通道分工

| 通道 | 载体 | 用途 | 持久化 | 保留 | 访问入口 |
|---|---|---|---|---|---|
| 工程运行日志 | structlog→stdout→Vector→Loki | 排障、性能分析 | Loki | 7d 热 / 90d 归档 | 运维（Grafana） |
| 业务审计日志 | AuditLogger→`audit_entries`(DB) | 谁在何时对何资源做了什么（合规） | PostgreSQL | 1 年 | §4.2.12 `/audit` |
| AI 决策留档 | AuditLogger `decision.*`→`audit_entries` | 结论溯源 / 判定解释 | PostgreSQL | 1 年 | 随报告/审计查询（M2-8） |
| 实时事件 | EventBus（进程内）→WebSocket | 看板实时呈现 | **不持久化** | — | §4.3 |

> EventBus 为进程内队列（§8），断线补齐靠 DB 查询与 `/cost/snapshot`；**凡需事后回溯的事实（阶段、证据、审计、决策）一律落库，不依赖 WS 事件**。审计/决策必须走 `AuditLogger`（不依赖日志采集是否在线），工程排障走 structlog，二者不可互相替代。

### 12.2 指标

| 指标 | 类型 | 标签 |
|---|---|---|
| `http_requests_total` | Counter | method, path, status |
| `http_request_duration_seconds` | Histogram | method, path |
| `llm_token_usage_total` | Counter | model, stage, run_id |
| `stage_duration_seconds` | Histogram | stage |
| `run_state_transition_total` | Counter | from_stage, to_stage |
| `ws_active_connections` | Gauge | - |
| `evidence_fetch_total` | Counter | source_type, status |

### 12.3 追踪

OpenTelemetry SDK 自动埋点：

- FastAPI 请求 → 中间件
- HTTPX 出站（检索、连接器）
- SQLAlchemy（通过 opentelemetry-instrumentation-sqlalchemy）
- Celery（通过 opentelemetry-instrumentation-celery）
- LangGraph（自定义 span wrapper）

Trace ID 通过 `traceparent` Header 跨服务传递，前端可从响应 Header 取回用于报错时反馈。

### 12.4 告警规则（M3 起）

| 规则 | 阈值 | 通知 |
|---|---|---|
| 阶段超时 | 任意阶段 P95 > 30 分钟 | Slack |
| Token 暴增 | 单 run 1 小时增量 > 200k | 邮件 |
| LLM Provider 失败率 | 5 分钟 > 20% | PagerDuty |
| WS 连接数 | 单节点 > 5000 | 邮件 |
| 数据库连接池等待 | > 100ms | Slack |

---

## 13. 安全设计

### 13.1 凭证加密分层

见 HLD §9.2.1 与 §10.2。代码层 `CryptoAdapter` 接口稳定，实现可替换。

### 13.2 多租户隔离

- API 层：`get_current_user` 依赖注入 `team_id`，所有查询 `WHERE team_id = :team_id`
- ORM 层：基类混入 `TenantScopedMixin`，由 `before_compile` 钩子自动追加过滤
- 实时通道：WebSocket 订阅校验用户对 run_id 的访问权（通过 `RunCollaborator` 或项目成员关系）

### 13.3 输入校验

- Pydantic 强类型校验所有请求体
- 查询参数走 `Query()` 限定范围（页大小、档位枚举）
- 自由文本字段（question）长度上限 500 字，Unicode 规范化
- LLM 输出走结构化 schema 校验，校验失败视作节点失败

### 13.4 SSRF 防护

- 连接器抓取目标 URL 走白名单域名前缀 + DNS 解析后 IP 检查（防内网）
- 私域连接器出站调用走独立网络命名空间

### 13.5 审计与防绕过

- 所有 Provider 调用必走 `ProviderFactory`，禁止直接调用第三方 SDK
- 所有写入操作必走 `AuditLogger`
- 用户介入仅允许受控操作类型（详见 HLD §10.5）

### 13.6 密钥管理

- `.env` 仅本地开发使用，`.env.example` 不含真实值
- 生产密钥全部从环境变量或密钥管理服务注入
- JWT secret 定期轮换（运维脚本，旧密钥保留 1 小时兼容）

---

## 14. 部署与运维

### 14.1 本地开发（docker-compose）

> 实际编排文件为 `backend/docker-compose.dev.yml`（完整栈：PostgreSQL+pgvector/Redis/MinIO）与 `backend/docker-compose.min.yml`（低内存精简栈，仅 PostgreSQL）；环境拓扑、端口、凭据与保活机制以《本地开发环境手册》（docs/ops/）为唯一事实源。下方为设计期多服务编排节选，当前本地开发不启 api/worker 容器（前后端进程跑在宿主侧）。

```yaml
# backend/docker-compose.dev.yml（设计期路径 deploy/docker-compose.yml，已调整；下为多服务编排预案）
version: "3.9"
services:
  api:
    build:
      context: ..
      dockerfile: backend/Dockerfile
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    volumes: [".:/app"]
    ports: ["8000:8000"]
    env_file: .env
    depends_on: [postgres, redis, minio]

  worker:
    build: ..
    command: celery -A app.workers.celery_app worker -Q retrieval,normalize,report,sync -l info
    env_file: .env
    depends_on: [postgres, redis]

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ra
      POSTGRES_PASSWORD: ra_dev
      POSTGRES_DB: research
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports: ["9000:9000", "9001:9001"]

volumes:
  pgdata:
```

### 14.2 健康检查

```python
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.get("/readyz")
async def readyz(db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)):
    await db.execute(text("SELECT 1"))
    await redis.ping()
    return {"status": "ready"}
```

K8s 探针：`livenessProbe` 走 `/healthz`，`readinessProbe` 走 `/readyz`，初始延迟 30s，间隔 10s。

### 14.3 优雅停机

- Uvicorn SIGTERM → 停止接受新连接 → 等待 WS 客户端断开（最长 30s）
- Celery worker SIGTERM → 停止拉取新任务 → 等待当前任务完成（最长 5 分钟）
- DB 引擎、应用 lifespan 上下文统一释放

### 14.4 备份与恢复

- PostgreSQL：每日 pg_basebackup + WAL 归档保留 7 天
- MinIO：版本化 + 跨节点纠删码（M1-M2 4 节点）
- Redis：每日 RDB + AOF 双开

---

## 15. M1-M2 实施清单

### 15.1 M1：最小链路端到端 demo

| # | 任务 | 关键产出 |
|---|---|---|
| M1-1 | 工程脚手架 | FastAPI + SQLAlchemy + Celery + Alembic + Docker Compose 跑通 |
| M1-2 | 鉴权基线 | 邮箱密码登录 + JWT 中间件 + 单元覆盖 |
| M1-3 | 数据模型初始化 | users · teams · projects · research_runs · stages · evidence · reports · audit_entries + 索引 |
| M1-4 | Orchestrator 骨架 | LangGraph 状态图 + 6 节点最小实现 + PG checkpoint |
| M1-5a | Provider 门面 + 主链路 | LangChain v1 `BaseChatModel` 薄封装 → `LLMClient` 门面（§7.1/7.5）+ `ProviderFactory.build_client`（§7.4）+ token 计量 + Prometheus 上报；OpenAI 为主 Provider，主备/熔断框架就绪 |
| M1-5b | Anthropic 备用 Provider | `ChatAnthropic` 适配器（§7.3）+ 主备切换演练（故障注入触发 `audit_log`）——依赖 M1-5a，可在 M1-6 后并行 |
| M1-6 | Researcher Celery 任务 | 单子问题搜索-读-追问循环 |
| M1-7 | WebSocket Hub | stage/evidence 事件推送 |
| M1-8 | 端到端跑通 | 固定研究问题可生成非空报告 + 看板可见阶段进度 |
| M1-9 | 统一日志基线 | structlog 上下文贯通（HTTP 中间件 + Celery `TracedTask` + `run_stage` 包装器 bind run_id/stage/trace_id）+ 事件字典 v1（§12.1.4）+ LLM 留痕（元数据全量、失败现场脱敏入 MinIO、采样开关）+ stdout→Vector→Loki 采集打通；验收见 §15.4 |

> M1-9 为横切基线，**随 M1-4 / M1-6 并行实现、不单独串行**；其上下文贯通与事件字典是节点编码的前置约束，须在第一个节点落地前就位。

### 15.2 M2：能力补齐与工程化

| # | 任务 | 关键产出 |
|---|---|---|
| M2-1 | 意图路由 | classify 接口 + 离线评估门禁 ≥ 85% 召回（工作包离线门禁；PRD A11/A12 的 MVP 验收召回为 ≥ 95%，两层口径以 SDP §5.1 为准；2026-09-12 实测研究召回/闲聊精确率均 100%） |
| M2-2 | 批判收敛 | Critic 节点完整实现 + 分歧 API |
| M2-3 | 实时成本展示 | Pushgateway + Metrics Exporter + WS 推送链路 ≤ 3s |
| M2-4 | 看板数据接口 | stages/sub_questions/evidence/cost/snapshot 完整 |
| M2-5 | 用户介入 | pause/resume/intervene 接口 + LangGraph interrupt 联通 |
| M2-6 | 信源元数据抽取 | 来源域名/发布时间/类型/可信分级抽取与去重打分（不实现"证据簇"自动聚类） |
| M2-7 | 数据点级溯源 | 报告 JSON Schema + report_citations 落库，论断绑定信源与原文片段 |
| M2-8 | 审计与决策留档 | 全量审计事件覆盖 + `/audit` 可查询接口 + AI 决策留档（Clarifier/Critic 判定理由经 `AuditLogger` 的 `decision.*` 落库，支撑结论溯源/解释，见 §9.3） |
| M2-9 | OpenAPI 导出 | 前端客户端生成 + CI 校验 |

> M2-6/M2-7 的编号与交付范围以《软件开发计划》§3 M2 里程碑表为唯一事实源（M2-6 信源元数据抽取 → M2-7 数据点级溯源）；M2-8/M2-9 为本表工程工作包延伸编号，SDP §3 未单列，SDP §5.2 映射表已按此口径引用。

### 15.3 Provider 层验证指标（M1 完成时检查）

为验证 §7 LangChain v1+ 薄封装方案是否兑现"复用而非重复造轮子"的承诺，M1 结束前对照下列指标：

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 代码量 | `backend/app/provider/` 总行数 ≤ 400 行 | `cloc backend/app/provider/` |
| 业务层 langchain 引用 | 业务代码 grep `import langchain` 命中数 = 0（仅 provider/ 内允许） | `rg "import langchain" backend/app/orchestrator backend/app/api backend/app/repo` |
| 测试覆盖 | `backend/app/provider/` 单测覆盖率 ≥ 90% | `pytest --cov` |
| 迭代速度 | M1-5a + M1-5b 合计实际工时 ≤ 6 人日（M1-5a ≤ 4，M1-5b ≤ 2） | 工时登记表 |
| 适配次数 | M1 阶段 openai / anthropic SDK 主版本升级适配次数 ≤ 1 | 升级记录 |
| 熔断触发 | 主备切换在演练中可见、产生 `audit_log` 事件 | 故障注入测试 |

若任一指标未达预期，触发 §16.3 决策评审，决定是否调整 Provider 边界或回退至纯自研方案。

### 15.4 统一日志基线验收指标（M1-9 完成时检查）

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 一屏捞齐 | 任一 `run_id` 在 Loki 能捞齐该 run 全链路日志（HTTP / 节点 / Celery / LLM），无断档 | 故障演练后按 run_id 查询 |
| 上下文贯通 | Celery researcher 任务日志携带 `run_id` + `trace_id`，非空率 100% | 采样统计 worker 日志字段 |
| 必带字段 | 每行应用日志均含 `trace_id / run_id / team_id`（无 run 的系统日志除外） | Loki LogQL 字段缺失率查询 |
| 事件字典合规 | 业务代码无散文式 `logger.info("...")` / `print`，事件均命中 §12.1.4 字典 | `rg "logger\.(info|warning|error|debug)\(" apps/` + 代码评审 |
| 脱敏 | 日志与 `llm-traces` blob 中 grep 不到 api_key / Bearer / JWT / secret 明文 | 红队正则扫描 |
| LLM 失败留痕 | 故障注入下 `llm.call.failed` 100% 有 `trace_ref`，blob 可拉取复现 | 故障注入测试 |
| 采集延迟 | 日志产生到 Loki 可查 P95 < 30s | 比对日志时间戳与入库时间 |
| 级别生效 | prod 环境第三方库日志 ≥ WARNING，应用事件字典 INFO 可见 | 环境配置核对 + Loki 抽查 |

---

## 16. 与上游文档衔接

| 上游文档 | 本文件承接 | 备注 |
|---|---|---|
| [架构设计概要](./AI研究者助手-架构设计概要.md) §4.1 后端模块 | §3 项目结构 | 模块目录一一对应 |
| HLD §7 LangGraph 状态图骨架 | §6 Agent 编排层 | 节点实现路径 |
| HLD §8 API 契约概要 | §4 API 契约完整定义 | 完整端点 + envelope |
| HLD §9 私域连接器架构 | §10 私域连接器设计 | M5 起详细化 |
| HLD §10 可观测性 | §12 可观测性 | 统一日志管理（§12.1）+ 指标 + 追踪 + 告警 |
| HLD §9.2.1 凭证加密分层 | §10.2 凭证加密 | M1-M2 用 Fernet，M3+ KMS |
| [智能体协作规格](./AI研究者助手-智能体协作规格说明.md) §10 交互协议 | §6.5 节点实现 | 4 种交互模式在节点函数中体现 |
| [智能体协作规格](./AI研究者助手-智能体协作规格说明.md) §10.2 请求-响应 / §10.3 扇出 | §6.5.4 researcher_fan_out | Celery 异步 + asyncio.gather |
| [PRD](./AI研究者助手-软件需求规格说明书.md) §6.4 成本治理 | §9.2 配额与限流 | 档位 token 上限执行 |
| [SDP](./AI研究者助手-软件开发计划.md) §3 M1-M2 交付物 | §15 M1-M2 实施清单 | 任务与里程碑对齐 |
| [后端契约草案](./AI研究者助手-后端契约草案.md) §4 事件 schema / §5 鉴权 / §6 HITL | §4.2.1 鉴权补 refresh+logout+WS 子协议、§4.2.5 pause/cancel/resume HumanInput 三选一、§4.3.1 事件表与 §4.4.1 SSE 帧 | 2026-09-09 草案 v0.1 评审通过后合并 |
| [后端契约草案](./AI研究者助手-后端契约草案.md) §7-§14 报告批注/标存疑/导出/分享/知识库/通知/审计/Telemetry | §4.2.13-§4.2.20 新增端点；§4.1.6 ID 前缀增补 `ant_/men_/dbt_/exp_/shr_/ntf_/aud_` | M4/M5/M6 分阶段实施 |
| [后端契约草案](./AI研究者助手-后端契约草案.md) §13.3 action 命名空间 | §12 审计日志 action 枚举 | 命名空间 `<domain>.<verb>` 统一 |
| [后端契约草案](./AI研究者助手-后端契约草案.md) §17.1 章节→里程碑窗口 | §15 M1-M2 实施清单 | §7-§14 接口按里程碑落地 |

### 16.3 关键技术选型决策记录

#### 2026-09-09 LLM Provider 层方案调整

**议题**：原 §7 设计为"自研 200-300 行 Provider 层，不引入 LangChain"。在选型评审中识别出与"尽可能复用开源已实现功能"原则的矛盾，以及 LangGraph `astream_events` 对节点级 token 流推送能力的强依赖。

**决策**：

1. 引入 LangChain v1+ 作为 Provider 层底层适配：
   - `langchain_core.BaseChatModel` / `Runnable` 作为多厂商统一协议
   - `langchain_openai.ChatOpenAI` / `langchain_anthropic.ChatAnthropic` 作为 SDK 适配
   - LangChain 原生 `with_fallbacks` / `with_structured_output` / `astream_events` 直接复用
2. 保留自研 `LLMProvider` Protocol 作为业务边界，业务层不直接 import `langchain_core`
3. 自研层只覆盖 LangChain 不提供的能力：`CircuitBreaker`、`UsageTracker`（含 team_id 维度归集）、`ProviderFactory` 主备编排
4. 不引入 `langchain_community` 工具全家桶，`langchain.agents` / `vectorstores` / `retrievers` 全部由 LangGraph + pgvector 自行编排

**版本锁定**：`langchain_core>=1.0,<2.0`、`langchain_openai>=1.0,<2.0`、`langchain_anthropic>=1.0,<2.0`、`openai>=1.40,<2.0`、`anthropic>=0.40,<1.0`，M1 阶段不主动升级。

**验证**：见 §15.3 Provider 层验证指标。

**影响范围**：

- §2 技术栈依赖表（已更新）
- §7 Provider Adapter（已重写：§7.1-§7.7）
- §15.3 实施清单验证指标（已新增）
- §6 Orchestrator 节点中 `await llm.complete(...)` 改为 `await runnable.ainvoke(...)` 调用方式（在节点实现层屏蔽 LangChain 类型）

下游产出（本文件的下游）：

- **数据模型详细设计**：补充字段约束、触发器、分区策略
- **前端详细设计**：基于 §4 OpenAPI 生成客户端、基于 §4.3 envelope 实现事件解析
- **部署运维手册**：基于 §14 补充 K8s Helm Chart、多区域灾备
- **prompt 工程规范**：基于 §6.5 节点函数中 `prompt_registry.get(...)` 的 key 列表

---

> **备注**：本文档 M1 范围内的部分（项目结构、API、Orchestrator 骨架、Provider 适配、M1 实施清单）可立即进入编码；M2 范围（意图路由、批判收敛、看板数据、用户介入）随 M1 末期同步细化；M3+（私域连接器、多区域部署、KMS）待相关里程碑启动前补充。
