# AI 研究者助手 · 后端契约草案

> **文档性质**：后端契约草案，用于补齐并定版[后端详细设计](./AI研究者助手-后端详细设计.md)中的已知缺口，以及承接 M4/M5/M6 新增能力的前后端契约。
> 相关文档：[前端详细设计](./AI研究者助手-前端详细设计.md)（§18.1 前端视角契约草案 · §18.2 差异确认清单 · §18.3 产品待确认）· [交互设计规范(DS)](./AI研究者助手-交互设计规范.md) · [SDP](./AI研究者助手-软件开发计划.md)
> 版本：v0.2（2026-09-13 状态回填；初版 v0.1 日期 2026-09-09）

> **评审与落地状态（2026-09-13 回填，替代 v0.1「全量待评审」表述，分章状态以 §18.1 表为准）**：
> - §5.1/§5.2（refresh/logout）、§6.3（verdict + additional_note）已随 M1/M2-2 落地，机读契约见 `docs/contract/openapi-m2-2.json`；
> - §4.1/§4.2/§5.3/§6.1/§6.2/§6.4-6.6 为已评审设计预案，随 M2-3~M2-5 等对应工作包落地（其中 §4.3 报告流 SSE 未被 M1 冻结契约采纳、M2 不实现，恢复需重新评审，见后端详细设计 §4.4）；
> - §7-§14 为 M4/M5/M6 新能力提案，里程碑窗口见 §17.1，落地前仍以后续评审为准。
> 已落地章节与机读契约冲突时，以 `docs/contract/` 冻结 JSON 为最高优先。

---

## 目录

1. [概述](#1-概述)
2. [范围与对应缺口](#2-范围与对应缺口)
3. [全局契约基线（继承与重申）](#3-全局契约基线继承与重申)
4. [事件 schema 修正与定版](#4-事件-schema-修正与定版)
5. [鉴权与会话补充契约](#5-鉴权与会话补充契约)
6. [运行控制与 HITL 通道定版](#6-运行控制与-hitl-通道定版)
7. [报告批注与提及 API](#7-报告批注与提及-api)
8. [报告「标存疑」与版本化重生成](#8-报告标存疑与版本化重生成)
9. [报告导出任务 API](#9-报告导出任务-api)
10. [报告只读分享 API](#10-报告只读分享-api)
11. [知识库 API（补齐列表与检索）](#11-知识库-api补齐列表与检索)
12. [站内通知中心 API](#12-站内通知中心-api)
13. [审计日志列表 API](#13-审计日志列表-api)
14. [前端 Telemetry 批量上报 API](#14-前端-telemetry-批量上报-api)
15. [新增错误码](#15-新增错误码)
16. [权限矩阵补充](#16-权限矩阵补充)
17. [里程碑窗口与 OpenAPI 落地](#17-里程碑窗口与-openapi-落地)
18. [评审记录与版本](#18-评审记录与版本)

---

## 1. 概述

### 1.1 目标

1. 定版后端详细设计中未给出请求/响应体的通道：`/auth/refresh`、`/runs/{id}/pause`、`/runs/{id}/cancel`、`/runs/{id}/resume` 的显式 body 语义。
2. 消除文档内部差异：`interrupt.requested`、`cost.warning`、SSE `report/stream` 帧形态，给出唯一规范。
3. 提供 M4/M5/M6 新能力的后端契约：报告批注/@、标存疑与重生成、导出、只读分享、知识库列表与检索、站内通知、审计列表、Telemetry。
4. 补充由此引入的错误码与权限矩阵，与前端详细设计 §13.3 对齐。

### 1.2 约定沿用

- 除本文件另有声明外，均沿用后端详细设计的全局约定：`/api/v1` 基路径、snake_case、ULID 前缀实体 ID、RFC 7807 错误、分页 `{items,total,page,page_size,has_more}`、ISO 8601 UTC 时间戳、`Idempotency-Key` 幂等、`Authorization: Bearer`。
- 事件 envelope 恒为：`{v:"1.0", event_id, ts, run_id, stage, type, payload}`；客户端消息恒为 `{v:"1.0", type, request_id, payload}`。
- 字段命名：HTTP/JSON 契约一律 snake_case；前端 TS 内部经 openapi 生成器映射为 camelCase，不反向影响契约。

### 1.3 新增实体 ID 前缀

| 实体 | 前缀 | 说明 |
|---|---|---|
| Annotation | `ant_` | 报告批注 |
| Mention | `men_` | @提及记录 |
| Doubt | `dbt_` | 论断存疑记录 |
| ExportJob | `exp_` | 导出任务 |
| ShareLink | `shr_` | 只读分享 |
| KnowledgeItem | `ki_` | 已有，沿用 |
| Notification | `ntf_` | 站内通知 |
| AuditRecord | `aud_` | 审计条目（返回层 ID，见 §13） |

---

## 2. 范围与对应缺口

| # | 对应前端文档 | 缺口/事项 | 本草案章节 | 状态 |
|---|---|---|---|---|
| 1 | §18.2 #1 | `interrupt.requested` payload 定版 | §4.1 | 定版建议 |
| 2 | §18.2 #2 | `cost.warning` payload 定版 | §4.2 | 定版建议 |
| 3 | §18.2 #3 | SSE `report/stream` 帧形态定版 | §4.3 | 定版建议 |
| 4 | §18.2 #4 | `/auth/refresh`、`pause`、`cancel` body | §5、§6.1 | 定版建议 |
| 5 | §18.2 #5 | verdict `additional_note` 对齐 | §6.3 | 定版建议 |
| 6 | §18.2 #6 | WS 鉴权通道 | §5.3 | 决策项 |
| 7 | §18.2 #7 | 批注/@、导出、knowledge/search、audit schema | §7、§9、§11、§13 | 新契约 |
| 8 | §18.2 #8 | 通知服务端点 | §12 | 新契约 |
| 9 | §18.2 #9 | stage 失败重试/回溯通道 | §6.4 | 新契约（需产品确认） |
| 10 | §18.3 #1 | 子问题计划 gate（编辑/确认） | §6.5 | 新契约（需产品确认） |
| 11 | §18.3 #7 | 启动前成本预估值 | §6.6 | 新契约（推荐扩展 classify） |
| 12 | — | 报告只读分享（M4） | §10 | 新契约 |
| 13 | — | 知识库 M5 页面（列表/沉淀/关联发起） | §11 | 新契约 |
| 14 | — | Telemetry 批量上报 | §14 | 新契约 |

---

## 3. 全局契约基线（继承与重申）

本节只重申本草案直接依赖的规则，避免与前文冲突：

- 分页查询参数 `page`（默认 1）、`page_size`（默认 20，上限 100）；游标深翻页可选 `cursor`。
- 列表类响应统一 `{ items: T[], total: number, page: number, page_size: number, has_more: boolean }`。
- 写操作需幂等的，统一支持 `Idempotency-Key`（UUIDv4），服务端保留 24h。
- 错误响应统一 RFC 7807：`{ type, title, status, detail, instance, code, trace_id }`；客户端按 `code` 分支。
- 软删除资源（归档/删除的语义）在返回中透出 `deleted_at` 或 404，由具体章节声明。

---

## 4. 事件 schema 修正与定版

> 本节内容用于消除后端详细设计中的内部差异。评审通过后替换对应描述；两种 payload 形式并存期以前端宽容解析为过渡（字段兜底），不建议永久兼容。

### 4.1 `interrupt.requested` 定版

统一以下列为唯一发布形态（不再使用 `interrupt_type/prompt/options` 顶层字段）：

```jsonc
{
  "v": "1.0",
  "event_id": "evt_...",
  "ts": 1736486400000,
  "run_id": "run_...",
  "stage": "clarify",          // 触发介入时所处阶段
  "type": "interrupt.requested",
  "payload": {
    "reason": "clarify_insufficient", // 枚举：clarify_insufficient | subquestion_evidence_short | user_decision_needed | ...
    "questions": [                     // 2~4 个，payload 以本数组为准
      {
        "key": "scope",
        "text": "本次研究的时间范围是？",
        "options": ["近一年", "近三年", "近五年", "不限"],
        "recommended": 2                // options 下标，可为 null（无默认）
      }
    ],
    "defaults": { "scope": "近三年" }, // 超时采用的默认假设（与 questions 一一对应）
    "expires_in_seconds": 900           // 澄清窗口（服务端默认假设时限），超时自动用 defaults
  }
}
```

约束：前端呈现只依赖 `questions[]` 与 `recommended`；`defaults` 仅用于超时后的"已按默认假设"标注。

### 4.2 `cost.warning` 定版

```jsonc
{
  "type": "cost.warning",
  "payload": {
    "level": "warning",           // warning(70%) | danger(90%)
    "used": 69200,
    "budget": 100000,
    "ratio": 0.692                // used / budget，用于进度条
  }
}
```

发布节流：即时。前端成本卡状态由 `level` 驱动，数值由 `used/budget/ratio` 呈现。

### 4.3 SSE `report/stream` 帧形态定版

> **实现状态（2026-09-15 M2 全工作包关闭后核对）**：该 SSE 报告流端点与 `report.chunk`/`done` 帧**整组 M2 未实现**（M1~M2-8b 后端无发射点，前端运行代码未订阅）；终稿可读由 `run.finished(succeeded)` + REST 报告端点（M2-7 起含 outline/blocks/citations）收敛。本节为逐 token 报告流的预留设计，挂 M4 体验打磨，以届时冻结契约为准。

统一为：**所有事件帧 `data` 都是完整 envelope 的 JSON**，`event` 字段与 `data.type` 保持一致（冗余，便于事件源过滤），`id` 字段为 `event_id`：

```jsonc
// 帧样例（每个事件由空行分隔）
event: report.chunk
id: evt_01HZ...
data: {"v":"1.0","event_id":"evt_01HZ...","ts":1736486400000,"run_id":"run_...",
       "stage":"report","type":"report.chunk","payload":{"chunk_id":"rep_chk_...","delta":"2026 年...","position":42}}

event: done
id: evt_01HZ...
data: {"v":"1.0","event_id":"evt_01HZ...","ts":1736486400000,"run_id":"run_...",
       "stage":"report","type":"done","payload":{"report_id":"rep_...","status":"final"}}
```

`done` 之外的帧类型固定为：`report.chunk`、`report.replace`、`error`（`error` 帧 `data.payload` 为 `{code, message, trace_id}`）。连接关闭语义：`done` 后服务端关闭流。

### 4.4 `report.replace` 定版（M4）

整报告版本化重生成的载体（标存疑触发，见 §8）。发布于 run 的 report 阶段流（SSE）与 WS 均可：

```jsonc
{
  "type": "report.replace",
  "payload": {
    "reason": "claim_doubt_resolved",   // claim_doubt_resolved | manual_regen | other
    "previous_report_id": "rep_...",     // 被替换旧版（状态置 superseded）
    "new_report_id": "rep_...",
    "changed_claim_ids": ["clm_..."]     // 发生内容变更的论断锚点集合（前端据此重映射批注）
  }
}
```

`new_report_id` 可立即用 §7/§8 之外的 `GET /reports/{report_id}` 拉取全文。`changed_claim_ids` 为空表示"仅重排序/元数据变化"。

### 4.5 批注相关实时事件（可选，M4）

新增事件 `annotation.created` / `annotation.updated` / `annotation.deleted`，发布到 **run 的 WS 频道**（report 归属某 run，订阅者可接收），`stage="report"`，`payload` 含 §7 的 Annotation 对象（`deleted` 事件只含 `annotation_id` 与 `deleted_at`）。

前端策略：优先增量刷新；后端若担心事件风暴，可降级为前端在报告页激活时轮询 §7 列表（2s 节流）。**是否纳入 WS 频道由后端评审决定，前端两侧均兼容。**

---

## 5. 鉴权与会话补充契约

### 5.1 刷新令牌

```jsonc
// POST /api/v1/auth/refresh   （无 Bearer，携带 refresh_token）
// 请求：无 body 或空 body；refresh_token 经 Cookie 或专用头传递由后端定版（见 §5.3 决策项）。
// 推荐形态（与现有 login 响应一致，便于前端最小改动）：
{ "refresh_token": "xxx" }

// 200 响应
{ "access_token": "jwt...", "expires_in": 1800, "token_type": "Bearer" }

// 错误
// 401 REFRESH_TOKEN_REVOKED   —— 已登出/黑名单，前端须清会话跳登录
// 401 REFRESH_TOKEN_EXPIRED   —— 7 天过期，前端须清会话跳登录
```

前端约束：`401` 后 single-flight 刷新（同窗口只一次），刷新成功重放原请求；两种错误码均触发登出流程。

### 5.2 登出

```jsonc
// POST /api/v1/auth/logout （Bearer）
// 200 { "ok": true }
// 语义：将当前 refresh_token 的 jti 写入黑名单 revoked_refresh:{jti}，TTL 覆盖原过期时间。
// 可选字段：{ "all_devices": true } —— 撤销该用户全部 refresh（M6 账户"登出全部设备"依赖此项）。
```

### 5.3 WS 鉴权通道（决策项，推荐子协议）

| 方案 | 说明 | 风险 | 建议 |
|---|---|---|---|
| 子协议（推荐） | 客户端握手 `Sec-WebSocket-Protocol: bearer, <jwt>`，服务端校验后回显 `bearer` | 部分网关需放行子协议头 | 消除 token 落 URL query 的日志泄露面 |
| query 参数 | `?token=<jwt>` | token 可能进访问日志/代理日志 | 不推荐，仅本地开发可用 |

评审结论将影响前端 RealtimeClient 的重连/换 token 实现。**在同结论前，mock 网关与前端同时支持子协议，生产默认子协议。**

### 5.4 会话并发与标签页策略

- 跨标签页登出同步：后端登出撤销 refresh 后，其他标签页下一次 refresh 返回 401 即登出（无需前端 storage 事件广播，作为兜底即可）。
- 不引入额外长轮询会话探测；以 30 分钟 access 生命周期内行为为基线。

---

## 6. 运行控制与 HITL 通道定版

### 6.1 暂停 / 继续 / 取消（显式 body）

```jsonc
// POST /api/v1/runs/{run_id}/pause
// 请求体：{ "reason"?: "user_action" }        // 可选，写入审计
// 200：{ "run_id": "run_...", "status": "paused" }
// 409：RUN_NOT_PAUSABLE —— 仅在 running/queued 可软暂停；succeeded/failed/cancelled 不可

// POST /api/v1/runs/{run_id}/resume
// 请求体（统一人类输入模型，见 §6.2）：{ "human_input"?: HumanInput }
//   简单"继续"（软暂停恢复）：空 body 或 { "human_input": { "kind": "proceed" } }
// 200：{ "run_id": "run_...", "status": "running" }
// 409：RUN_NOT_RESUMABLE —— 非 paused/awaiting_human 状态不可恢复

// POST /api/v1/runs/{run_id}/cancel
// 请求体：{ "reason"?: "user_action", "keep_partial"?: true }
//   默认 true：报告阶段中断时保留已推送片段为草稿（report.status="draft" 且可只读查看）
// 200：{ "run_id": "run_...", "status": "cancelled", "partial_report_id"?: "rep_..." }
// 语义：硬中断 + 中间态清理；与 WS cancel 指令等价，幂等（重复 cancel 返回当前状态）
```

### 6.2 `resume` 的统一人类输入模型（HumanInput）

澄清/裁决/追问/剔除等 HITL 场景统一走 `human_input`，收敛前端可发起的所有受控操作：

```jsonc
// POST /api/v1/runs/{run_id}/resume
{
  "human_input": {
    // 三选一，与后端 runner.resume 分支对齐：
    "answers": {                    // 1) 流程挂起（澄清/裁决）答案
      "scope": "近三年",
      "verdicts": {
        "conf_...": { "choice": "evidence_a", "reason": "..." }   // await_human reason=critique 分支
      }
    },
    "action": {                     // 2) 主动介入动作（与 POST /intervene 语义等价）
      "type": "ask_followup",       // ask_followup | exclude_evidence | revert_stage | mark_doubt
      "payload": { "sub_question_id": "subq_...", "question": "..." }
    }
    // 3) kind: "proceed" —— 纯恢复继续（软暂停后），不携带 answers/action
  }
}
```

约束：

- `POST /runs/{id}/intervene` 保留为独立快捷通道，语义与 `resume {human_input:{action}}` 完全等价，二者实现共用解析。
- 后端不接受的字段不静默忽略：返回 422（`UNKNOWN_HUMAN_INPUT`），避免前端臆造字段被吞。

### 6.3 裁决 verdict 字段对齐（含 `additional_note`）

```jsonc
// POST /api/v1/conflicts/{conflict_id}/verdict
{
  "choice": "evidence_a",         // evidence_a | evidence_b | both | reject
  "reason": "A 的数据口径与原文一致",
  "additional_note"?: "供报告局限区块引用"   // 与 Verdict ORM 对齐：新增可空字段
}
// 200：{ "conflict_id": "conf_...", "status": "resolved", "verdict_id": "ver_..." }
```

落地项：Verdict ORM 增加 `additional_note: str | null`，字段写入报告 `limitation` 区块引用（供局限汇总展示）。

### 6.4 stage 失败重试/回溯通道（§18.2 #9，需产品确认）

当 `stage.failed` 且错误码可恢复（如 `PROVIDER_UNAVAILABLE`），允许用户手动重试该 stage 或回退到前序 stage：

```jsonc
// POST /api/v1/runs/{run_id}/stages/{stage}/retry
// 请求体：{ "attempt_reason"?: "user_action" }
// 200：{ "run_id": "run_...", "stage": "retrieve", "attempt": 2, "status": "running" }
// 409：STAGE_NOT_RETRYABLE —— 状态机不允许（仅 failed/succeeded 可重试，且由编排器决定安全回退点）

// POST /api/v1/runs/{run_id}/revert-stage   // 等价 revive（已定义 revert_stage 介入，二者对齐）
```

约束：重试只能回退到当前 stage 或其紧邻前序的可安全重放 stage；不允许跳到前序任意 stage 造成流水线语义破坏。后端无重试端点前，前端不展示"重试"按钮（展示"自动回溯中/稍后恢复"），见前端 §11.3。

### 6.5 子问题计划 gate（§18.3 #1，需产品确认，预留设计）

若产品确定"分解后由用户确认子问题再继续检索"，则增加：

```jsonc
// GET  /api/v1/runs/{run_id}/sub-questions            // 已有
// PUT  /api/v1/runs/{run_id}/sub-questions/plan       // 待用户确认后的最终计划
{
  "sub_questions": [
    { "id": "subq_...", "enabled": true, "order": 1 },
    { "id": "subq_...", "enabled": false }             // 用户取消该子问题
  ]
}
// 200：{ "run_id": "run_...", "status": "running", "stage": "retrieve" }
```

约束：仅当 run 处于 `stage=decompose` 挂起的 `awaiting_human` 计划确认点才可用。**若产品维持 MVP"自动执行、只读展示"，本通道不实现**（对应前端 §11.2 口径）。

### 6.6 启动前成本预估值（§18.3 #7，推荐扩展 `/intent/classify`）

在 `POST /intent/classify` 响应中增加可空预算字段，供向导 step4 展示：

```jsonc
// 200 响应扩展（字段均可空；后端能估算时返回，估算不可用时置 null）
{
  "intent": "research",
  "confidence": 0.92,
  "recommended_template": "tmpl_general_01",
  "recommended_tier": "standard",
  "estimated_token_budget": 120000,          // 可空
  "estimated_cost_grade": "standard"         // 成本分级文案键（low|standard|deep|extreme），可空
}
```

约束：预估值仅供参考，`POST /runs` 响应仍以实际 `estimated_token_budget/estimated_cost_grade` 为准；两者不一致时前端以后者为准并更新展示。

---

## 7. 报告批注与提及 API

### 7.1 数据模型

```jsonc
// Anchor（ReportAnchor，snake_case 契约版）
{
  "kind": "claim",                    // claim | block
  "claim_id": "clm_...",              // kind=claim 必填
  "block_id": "blk_...",              // kind=block 必填；kind=claim 时也返回其所属 block 便于检索
  "range": [12, 48]                   // 相对 claim.text 的字符偏移 [start,end]，可缺省表示整条
}

// Annotation
{
  "annotation_id": "ant_...",
  "report_id": "rep_...",
  "run_id": "run_...",
  "anchor": { /* Anchor */ },
  "content_md": "复核该数据口径。",
  "parent_id": "ant_...",             // 可选：回复上级批注，形成线程（null=线程根）
  "mentions": [ { "mention_id": "men_...", "user_id": "usr_...", "display_name": "张三" } ],
  "created_by": "usr_...",
  "created_at": "2026-09-09T08:00:00Z",
  "updated_at": "2026-09-09T08:05:00Z"
}
```

约束：

- `anchor` 落在终稿（非 superseded）报告上；旧版重生成后批注需按 `claim_id` 重映射（前端 §10.4），后端保留 `anchor.claim_id` 不变、`block_id/range` 失效自动置空并标记 `anchor_stale: true`。
- `content_md` 仅支持纯文本与基础 markdown 子集（由渲染层净化，后端仍按不可信输入存储与回显）。
- 删除语义：线程根删除即整线程隐藏（逻辑删除），子回复删除仅自身；`deleted_at` 回显。

### 7.2 端点

| 方法 | 路径 | 鉴权 | 说明 | 幂等 |
|---|---|---|---|---|
| GET | `/api/v1/reports/{report_id}/annotations` | 项目成员 | 列表，筛选：`claim_id`、`block_id`、`parent_id=null(只看线程根)`；分页 | — |
| POST | `/api/v1/reports/{report_id}/annotations` | 项目成员 | 创建线程根或回复（`parent_id`） | 支持 |
| PATCH | `/api/v1/annotations/{annotation_id}` | 创建者/管理员 | 编辑 `content_md` | 支持 |
| DELETE | `/api/v1/annotations/{annotation_id}` | 创建者/管理员 | 逻辑删除（见上） | — |
| GET | `/api/v1/annotations/{annotation_id}` | 项目成员 | 单条详情（含 mentions 解析后的 display_name） | — |

```jsonc
// POST /api/v1/reports/{report_id}/annotations 请求体
{
  "anchor": { "kind": "claim", "claim_id": "clm_...", "range": [12, 48] },
  "content_md": "复核该数据口径。",
  "parent_id": null,                         // 回复线程根时填其 annotation_id
  "mentioned_user_ids": ["usr_..."]          // @提及；服务端校验均为该项目成员
}
```

错误：`ANNOTATION_ANCHOR_INVALID`(422)、`ANNOTATION_PARENT_NOT_FOUND`(404)、`ANNOTATION_TARGET_SUPERSEDED`(409，报告已 superseded 不可再批注)、`NOT_PROJECT_MEMBER`(404 伪装)。

提及语义：创建成功后向被提及者投递站内通知（§12），事件类型 `mention`；被提及人不在项目内 → 422 `MENTION_NOT_PROJECT_MEMBER`。

---

## 8. 报告「标存疑」与版本化重生成

### 8.1 数据模型

```jsonc
{
  "doubt_id": "dbt_...",
  "report_id": "rep_...",
  "claim_id": "clm_...",
  "reason": "数据来源时效存疑",       // 枚举建议：source_outdated | methodology | contradiction | other
  "note": "补充说明",
  "status": "pending",                // pending | evaluating | resolved | superseded
  "created_by": "usr_...",
  "created_at": "2026-09-09T08:00:00Z",
  "resolved_at": null
}
```

约束：同一 `claim_id` 同时只允许存在一个未决 doubt（pending/evaluating），重复提交返回 `DOUBT_ALREADY_OPEN`(409)。

### 8.2 端点与流程

```jsonc
// POST /api/v1/reports/{report_id}/claims/{claim_id}/doubt
{ "reason": "source_outdated", "note": "该数据为 2024 年口径" }
// 202：{ "doubt_id": "dbt_...", "status": "pending" }     // 异步触发，不阻塞 HTTP

// GET /api/v1/reports/{report_id}/doubts    // 列出该报告所有存疑（含已解决），筛选 status

// GET /api/v1/doubts/{doubt_id}             // 单条 + 关联重生成记录
```

流程（建议实现语义，供评审）：

1. 创建 doubt（status=pending）→ 投递站内通知给报告创建者/项目 owner（如存疑由非本人提出）。
2. Orchestrator 拉起对 `claim_id` 的局部重新评估（status=evaluating），复用 Reporter 单条评估路径。
3. 重评估完成：**整报告版本化发布新版**（新 `report_id`，旧版 status=superseded），SSE/WS 推送 `report.replace`（§4.4），doubt 置 `resolved`。
4. 若局部重评估判定无需变更内容：doubt 置 `resolved`，`changed_claim_ids=[]`，仅通知不 replace（避免无意义版本切换）。

重复触发的并发控制：以 `claim_id+report_id` 的未决 doubt 为锁，冲突返回 409。

### 8.3 报告生命周期定版

报告 status 枚举定版为：`draft | final | superseded`。

- `draft`：生成中或硬中断保留的片段稿（只读可看，不做批注/导出/分享入口）。
- `final`：终稿，唯一允许批注、标存疑、导出、分享的版本。
- `superseded`：被 `report.replace` 或"新版本"取代的历史版，仅历史查看。

`GET /reports/{report_id}` 增加 `status`、`replaced_by`(superseded 时指向新 report_id)、`supersedes`(新报告指向旧版) 字段。

---

## 9. 报告导出任务 API（M4）

### 9.1 设计要点

- 采用异步任务模型：`POST` 返回 `202` + `export_id`，前端以 `GET /exports/{id}` 轮询或等待 §12.4 通知（export.ready）。
- 仅 `final` 状态报告可导出（见 §8.3）；`draft`/`superseded` 返回 409。
- 同一 `report_id + format` 同一时刻至多允许一个 `queued/running` 任务；重复触发返回 409（携带相同 `Idempotency-Key` 时返回既有任务，保证幂等重放不报错）。
- 产物文件保留 7 天，超时删除；`file.url` 为带签名临时下载地址（`expires_in=3600s`）。

### 9.2 数据模型 ExportJob

```jsonc
{
  "export_id": "exp_...",
  "report_id": "rep_...",
  "format": "pdf",                // pdf | docx | markdown | json（最终支持子集由后端评审确认，见 9.6）
  "status": "queued",             // queued | running | ready | failed
  "file": null,                   // status=ready 时有值
  "error": null,                  // status=failed 时有 { "code": "EXPORT_RENDER_FAILED", "message": "..." }
  "requested_by": "usr_...",
  "created_at": "2026-09-09T08:00:00Z",
  "updated_at": "2026-09-09T08:00:03Z",
  "ready_at": null                // ready 时间，供列表展示"生成于"
}

// file 对象（status=ready 后）
{
  "url": "https://.../exports/exp_...pdf?X-Amz-Signature=...",
  "expires_in": 3600,
  "size_bytes": 284912,
  "content_type": "application/pdf"
}
```

### 9.3 端点

```jsonc
// POST /api/v1/reports/{report_id}/export        // 鉴权；仅项目成员
{
  "format": "pdf",
  "options"?: { "include_annotations": false }    // 是否带批注（M5[v1.1] 可用；能力随后端裁剪）
}
// 202 → { "export_id": "exp_...", "status": "queued" }
// 409 REPORT_NOT_FINAL（非 final 报告）
// 409 EXPORT_IN_PROGRESS（同报告同格式已有进行中任务）

// GET /api/v1/exports/{export_id}                // 轮询主通道 → ExportJob

// GET /api/v1/reports/{report_id}/exports
// query: status、page、page_size → 标准分页历史任务（页面"最近导出"列表）

// 下载：file.url 302 → 对象存储；签名地址过期需重新 GET /exports/{id} 获取
```

### 9.4 完成/失败通知

- 轮询为主通道；后台完成后可选投递站内通知（§12.4 `export.ready`/`export.failed`），前端据此在任务页与首页收敛入口。
- 失败可重试：用户在列表对 failed 任务点重试即再次 `POST /export`。

### 9.5 新增错误码

| code | HTTP | 场景 |
|---|---|---|
| `EXPORT_FORMAT_UNSUPPORTED` | 422 | format 不在支持子集内 |
| `REPORT_NOT_FINAL` | 409 | 非 final 报告发起导出/分享 |
| `EXPORT_IN_PROGRESS` | 409 | 同报告同格式存在进行中任务且无幂等键 |
| `EXPORT_RENDER_FAILED` | job.error | 渲染失败（异步，非 HTTP 错误） |

### 9.6 里程碑与评审项（M4 打磨期）

- 格式子集与样式模板待后端评审确认（对应前端详细设计 §18.1「报告导出」注释：格式 pdf/docx/… 后端拍板）。
- `options.include_annotations` 属批注能力（v1.1），若批注后端后移则本选项一并后移。

---

## 10. 报告只读分享 API（M4）

### 10.1 设计要点

- 只读分享链接绑定**具体 report_id（版本快照语义）**：`final` 版本可分享；被 superseded 后历史版本仍可按快照只读访问（供归档留档）。
- 内部成员照常走鉴权页面（`/runs/:runId/report`，见前端 §4.2）；外部/匿名只读走公开入口 + 不可枚举 token，**不要求 Bearer**。
- 分享创建受项目权限约束（§16）；撤销即刻失效。

### 10.2 ShareLink 模型

```jsonc
{
  "share_id": "shr_...",
  "report_id": "rep_...",
  "run_id": "run_...",
  "project_id": "prj_...",
  "label": "给产品组终稿",           // 可空，创建者自定义标题
  "token": "lE8y...",                // 服务端生成、url-safe、不可猜测（≥128bit）；仅在创建响应返回一次，列表不返回
  "status": "active",                // active | revoked
  "access": "read",                  // 定版只读；为扩展预留字段
  "expires_at": null,                // 可选过期时间；null=永不过期（需管理员撤销）
  "auto_follow_latest": false,       // true：版本被 replace 后公开入口 302 到新版本（评审项，默认 false）
  "view_count": 12,
  "created_by": "usr_...",
  "created_at": "2026-09-09T08:00:00Z",
  "revoked_at": null
}
```

### 10.3 端点

```jsonc
// —— 管理（Bearer，项目成员）——
// POST /api/v1/reports/{report_id}/shares
{ "label"?: "给产品组终稿", "expires_at"?: null, "auto_follow_latest"?: false }
// 201 → ShareLink（含 token，仅此一次返回全文）

// GET /api/v1/reports/{report_id}/shares    → 列表（status 维度过滤；不返回 token）
// POST /api/v1/shares/{share_id}/revoke     → 200 { "status": "revoked" }
// 越权/不存在统一 404（见 §15 说明，不泄露分享存在性）

// —— 公开只读（无鉴权）——
// GET /api/v1/public/shares/{token}
// 200 → {
//   "share_id": "shr_...", "report_id": "rep_...", "run_id": "run_...", "label": "...",
//   "report": { "title": "...", "status": "final", "blocks": [...], "claims": [...], "evidence": [...] }
// }
// report 内容与内部 GET /reports/{id} 结构化字段一致，但剥离批注/存疑/审计等内部字段；
// citation 引用的证据详情仅提供共享快照白名单内的字段。
// 404 SHARE_NOT_FOUND / SHARE_REVOKED / SHARE_EXPIRED（统一 404，不区分枚举探测）
// 429：按 IP 与 token 双维度限流
```

前端路由映射：内部成员 `ReportChrome [分享]` 生成链接；分享打开行为分两态——项目内成员复用鉴权阅读页，外部用户进入匿名只读页 `/share/:token`（M4 新增：无 app chrome、不挂鉴权守卫）。若产品决定"分享仅限项目内成员"，匿名公开页不实现（评审项）。

### 10.4 实时与统计

- `view_count` 自增即可，不投实时事件，不写审计（防刷屏）。

### 10.5 新增错误码

| code | HTTP | 场景 |
|---|---|---|
| `SHARE_NOT_FOUND` | 404 | token 不存在/已撤销/已过期（统一） |
| `REPORT_NOT_FINAL` | 409 | 非 final 报告发起分享 |
| `SHARE_CREATE_FORBIDDEN` | 404 | 非项目成员发起（按资源隐藏） |
| `SHARE_LIMIT_EXCEEDED` | 429 | 单报告活跃分享数超上限（建议 50，评审项） |

---

## 11. 知识库 API（补齐列表与检索，M5 [v1.1]）

### 11.1 数据模型（沿用 KnowledgeItem，补齐契约字段）

```jsonc
{
  "knowledge_item_id": "ki_...",
  "title": "欧盟碳关税 2026 立法进展",
  "content_md": "...",                // 列表接口不返回，详情返回全文
  "content_excerpt": "...",           // 列表返回截断（约 200 字）
  "source_type": "web",               // web | pdf | doc | image | evidence（自研究证据沉淀时取 evidence）
  "source_private": false,            // 来源受版权/私有访问限制时为 true（影响外链展示策略）
  "source_url": "https://...",
  "credibility": "B",                 // 可信分级 A | B | C | D（沿用后端既有维度）
  "tags": ["policy", "eu"],
  "visibility": "project",            // private（仅创建者）| project | org（取值以项目体系为准，评审项）
  "project_id": "prj_...",
  "origin_run_id": null,              // 证据沉淀来源 run（可空）
  "created_by": "usr_...",
  "created_at": "2026-09-09T08:00:00Z",
  "updated_at": "2026-09-09T08:00:00Z",
  "deleted_at": null                  // 软删除
}
```

### 11.2 列表端点

```jsonc
// GET /api/v1/knowledge/items
// query: q（标题/标签关键字过滤）、source_type、credibility、visibility、
//        sort=created_at（默认）| credibility、page、page_size
// → 标准分页；items 为 11.1 列表形态（无 content_md）

// GET /api/v1/knowledge/items/{knowledge_item_id}   → 详情（含 content_md）
// 越权/不存在统一 404 KNOWLEDGE_ITEM_NOT_FOUND
```

### 11.3 语义检索（定版前端 §18.1 草案）

```jsonc
// POST /api/v1/knowledge/search
{
  "query": "新能源汽车出海政策",
  "filters": { "types": ["evidence"], "credibility": ["A", "B"], "visibility": ["project"] },
  "page": 1,
  "page_size": 20
}
// 200 → {
//   "items": [
//     { "knowledge_item_id": "ki_...", "title": "...", "content_excerpt": "...",
//       "source_type": "evidence", "source_private": false, "credibility": "B",
//       "highlights": [{ "start": 10, "end": 30 }],     // 命中片段相对 excerpt 的字符区间
//       "score": 0.87 }
//   ],
//   "total": 5, "page": 1, "page_size": 20, "has_more": false
// }
```

约束：

- `filters` 缺省为全部；`visibility` 过滤必须叠加调用者可见范围（private 仅创建者、project 仅项目成员），**服务端强校验**，越权内容不出现于结果。
- 依赖向量/混合检索索引，M5 后端落地；联调期允许以关键字兜底实现（在接口上标注 `backend=temporary` 头，评审后移除）。

### 11.4 管理操作

```jsonc
// PATCH /api/v1/knowledge/items/{knowledge_item_id}
// 可改字段：tags、title（创建者）；credibility 修正、visibility 提升（owner/admin，见 §16）
// { "tags"?: [...], "title"?: "...", "credibility"?: "B", "visibility"?: "project" }

// DELETE /api/v1/knowledge/items/{knowledge_item_id}   // 软删除；仅创建者或项目 owner/admin
// POST /api/v1/knowledge/items                          // 手工沉淀：正文/来源/标签；凭证来源标注 origin_run_id（评审项）
```

"分享到研究"（前端 §11.6）在 run 启动时以 `knowledge_item_id[]` 引用（进入 `POST /runs` 的 context 来源白名单），不在本节约束。

### 11.5 新增错误码

| code | HTTP | 场景 |
|---|---|---|
| `KNOWLEDGE_ITEM_NOT_FOUND` | 404 | 不存在/越权（统一） |
| `KNOWLEDGE_SEARCH_QUERY_TOO_SHORT` | 422 | query 少于 2 字符（语义检索无效） |
| `KNOWLEDGE_ITEM_DELETE_FORBIDDEN` | 404 | 无删除权限（按资源隐藏） |

---

## 12. 站内通知中心 API（M4 基础，M6 扩展）

### 12.1 模型 Notification

```jsonc
{
  "notification_id": "ntf_...",
  "recipient_id": "usr_...",
  "kind": "mention",                 // 枚举见 12.4
  "title": "张三在报告终稿中 @了你",
  "body_md": "定位到「…」论断的批注，请复核。",   // 可空，富文本精简
  "link": { "type": "report_claim", "route": { "run_id": "run_...", "report_id": "rep_...", "claim_id": "clm_..." } },
                                      // 前端 deep-link：type 见 12.3，route 键随 type 变化
  "run_id": null, "report_id": null, "project_id": null,  // 冗余便于按项目/run 过滤
  "actor_id": "usr_...",             // 触发者；系统事件为 null
  "read": false,
  "created_at": "2026-09-09T08:00:00Z",
  "read_at": null
}
```

### 12.2 端点

```jsonc
// GET /api/v1/notifications
// query: kind、read、project_id、run_id、page、page_size → 标准分页

// GET /api/v1/notifications/unread-count   → { "count": 3 }      // 顶栏红点；轮询默认 60s，标签页可见时刷新
// POST /api/v1/notifications/{notification_id}/read → 200 { "ok": true }   // 幂等
// POST /api/v1/notifications/read-all       → { "ok": true, "updated": n }
```

**实时通道决策（评审项）**：现有事件总线按 run 作用域（envelope 携带 `run_id`），而站内通知是**用户作用域**（跨 run）。两条可选路径：

- 方案 A（推荐，后端评审定时间）：开放用户级通道 `/api/v1/ws?scope=user`，事件类型 `notification.created`（envelope 无 `run_id`）。
- 方案 B（M4 默认兜底）：红点轮询 `unread-count` + 点击拉取列表；前端实现成本低。

**未评审前按 B 实现，红点与列表契约不变，实时化只影响"立即红点"体验。**

### 12.3 `link.type` 深链枚举

| type | route 键 | 目标页 |
|---|---|---|
| `report_claim` | run_id / report_id / claim_id | 报告页定位论断 |
| `report_annotation` | run_id / report_id / annotation_id | 报告页定位批注线程 |
| `report_doubt` | run_id / report_id / doubt_id | 报告页定位存疑记录 |
| `run_cockpit` | run_id / stage | 指挥舱定位阶段 |
| `conflict` | run_id / conflict_id | 分歧工作台（v1.1） |
| `knowledge_item` | knowledge_item_id | 知识库条目 |

### 12.4 `kind` 目录与触发源

| kind | 触发源 | 里程碑 |
|---|---|---|
| `mention` | 批注创建含 @（§7.1，被提及者非项目成员→422） | M4 [v1.1] |
| `annotation.reply` | 批注线程回复（命中父线程参与者） | M4 [v1.1] |
| `doubt.opened` | 他人对报告标存疑（§8.2，通知报告创建者/项目 owner） | M4 [v1.1] |
| `doubt.resolved` | 存疑解决/重生成就绪（§8.2） | M4 [v1.1] |
| `report.ready` | run 完成、报告达 final（通知创建者与订阅者） | M3+ |
| `run.needs_input` | `interrupt.requested`/awaiting_human 澄清窗口开启 | M3 |
| `conflict.assigned` | 分歧待裁决指派 | M5 [v1.1] |
| `export.ready` / `export.failed` | 导出任务后台完成/失败（§9.4） | M4 |
| `share.revoked` | 管理员撤销分享链接提醒 | M4 |
| `system` | 策略/告警等运营事件 | M6 |

### 12.5 错误码

| code | HTTP | 场景 |
|---|---|---|
| `NOTIFICATION_NOT_FOUND` | 404 | 通知不存在（仅本人可见，越权同 404） |

---

## 13. 审计日志列表 API（M6/管理员）

### 13.1 说明

- 审计在服务端各写操作已隐含记录；本节只定版**列表读取**契约（前端团队管理页 `GET /audit` 消费，见前端 §12.3）。
- 仅 owner/admin 可读取；非授权角色统一 404（与全局"越权按资源隐藏"一致，不泄露是否存在审计能力），错误码 `AUDIT_ACCESS_DENIED`。

### 13.2 端点与响应

```jsonc
// GET /api/v1/audit
// query:
//   target_type: run | report | project | conflict | knowledge_item | annotation | share | export（可空=全部）
//   target_id、actor_id（可空）、action（可空，前缀匹配如 intervene.*）、
//   time_from、time_to（ISO 8601，可空）、page、page_size
// → 标准分页：
{
  "items": [
    {
      "audit_id": "aud_...",
      "user_id": "usr_...",                       // 发起者（系统动作为 null）
      "action": "intervene.exclude_evidence",     // 点分命名，见 13.3
      "target_type": "run",
      "target_id": "run_...",
      "payload": { "evidence_id": "ev_...", "reason": "与当前子问题无关" },
      "created_at": "2026-09-09T08:00:00Z"
    }
  ],
  "total": 1, "page": 1, "page_size": 20, "has_more": false
}
```

### 13.3 action 命名空间（草案，供评审）

| 命名空间 | 覆盖动作 | 对应章节 |
|---|---|---|
| `auth.*` | login / logout / refresh / logout_all | §5 |
| `run.*` | create / pause / resume / cancel / retry_stage / revert_stage | §6 |
| `intervene.*` | ask_followup / exclude_evidence / verdict / mark_doubt | §6.2-6.3、§8 |
| `annotation.*` | create / update / delete / reply | §7 |
| `doubt.*` | open / resolve / regenerate | §8 |
| `report.*` | finalize / supersede / export | §8.3、§9 |
| `share.*` | create / revoke | §10 |
| `knowledge.*` | create / update / delete | §11 |
| `membership.*` | invite / role_change / remove（M4/M6 成员管理） | §16 |

### 13.4 新增错误码

| code | HTTP | 场景 |
|---|---|---|
| `AUDIT_ACCESS_DENIED` | 404 | 非 owner/admin 读取审计（按资源隐藏） |

---

## 14. 前端 Telemetry 批量上报 API（M2 起，可后置）

### 14.1 约定

- 收敛前端自研 telemetry 模块（前端 §16.1）的单一批量端点；**不得阻塞主链路**：前端本地汇聚（localStorage 缓冲 + 阈值批量），失败静默丢弃该批。
- 隐私边界：仅事件名/页面/结构化键值；禁止自由文本与可识别的用户输入内容；`props` 仅允许扁平 JSON 标量。
- 仅登录用户上报；服务端做采样与限流。

### 14.2 端点

```jsonc
// POST /api/v1/telemetry/batch
{
  "events": [
    { "event": "report.citation.open", "ts": 1736486400123, "run_id": "run_...", "page": "report", "props": { "citation_index": 3 } },
    { "event": "cockpit.stage_click",   "ts": 1736486400500, "run_id": "run_...", "page": "cockpit", "props": { "stage": "retrieve" } }
  ]
}
// 204 无响应体（仅确认接收，异步落库；过载时允许抽样丢弃并返回 204）
```

约束：

- 单批 ≤ 200 条、≤ 64KB；超限返回 422 `TELEMETRY_BATCH_INVALID`（前端据此拆批）。
- `event` 命名 `^[a-z0-9_.]{1,128}$`；`ts` 为 epoch ms，距当前 ±7 天外整条丢弃。
- `run_id`/`page` 可空（如全局页事件）；同事件 30s 内高频去重建议在服务端抽样层处理（评审项）。

### 14.3 新增错误码

| code | HTTP | 场景 |
|---|---|---|
| `TELEMETRY_BATCH_INVALID` | 422 | 批尺寸/命名/时间戳越界 |
| `TELEMETRY_RATE_LIMITED` | 429 | 单用户批量频率超限（前端降频重试或丢弃） |

---

## 15. 新增错误码汇总

> 仅收录本草案定版/新增错误码；后端既有全局错误码（`INVALID_ARGUMENT`、`UNAUTHENTICATED`、`NOT_FOUND`、`RATE_LIMITED` 等）沿用，不重复列出。评审时若与后端错误目录重名，按后端目录语义收敛。

### 15.1 会话与鉴权（§5）

| code | HTTP | 触发场景 |
|---|---|---|
| `REFRESH_TOKEN_REVOKED` | 401 | refresh 已被登出黑名单撤销，清会话跳登录 |
| `REFRESH_TOKEN_EXPIRED` | 401 | refresh 超 7 天，清会话跳登录 |

### 15.2 运行控制 / HITL（§6）

| code | HTTP | 触发场景 |
|---|---|---|
| `RUN_NOT_PAUSABLE` | 409 | 仅 running/queued 可暂停 |
| `RUN_NOT_RESUMABLE` | 409 | 仅 paused/awaiting_human 可恢复 |
| `STAGE_NOT_RETRYABLE` | 409 | 状态机不允许该 stage 重试/回溯 |
| `UNKNOWN_HUMAN_INPUT` | 422 | human_input 三选一之外字段/未知分支（不静默吞字段） |

### 15.3 批注 / @提及（§7）

| code | HTTP | 触发场景 |
|---|---|---|
| `ANNOTATION_ANCHOR_INVALID` | 422 | 锚点 kind/claim_id/range 非法或越界 |
| `ANNOTATION_PARENT_NOT_FOUND` | 404 | 回复线程根不存在 |
| `ANNOTATION_TARGET_SUPERSEDED` | 409 | 报告已 superseded，禁止新增批注 |
| `MENTION_NOT_PROJECT_MEMBER` | 422 | @提及对象非本项目成员 |
| `NOT_PROJECT_MEMBER` | 404 | 非项目成员访问项目内资源（按资源隐藏） |

### 15.4 标存疑（§8）

| code | HTTP | 触发场景 |
|---|---|---|
| `DOUBT_ALREADY_OPEN` | 409 | 同 claim 存在未决存疑（pending/evaluating） |

### 15.5 导出与分享（§9、§10）

| code | HTTP | 触发场景 |
|---|---|---|
| `EXPORT_FORMAT_UNSUPPORTED` | 422 | format 不在支持子集内 |
| `REPORT_NOT_FINAL` | 409 | 非 final 报告发起导出/分享 |
| `EXPORT_IN_PROGRESS` | 409 | 同报告同格式已有进行中导出且无幂等键 |
| `EXPORT_RENDER_FAILED` | job.error | 渲染失败（异步任务内错误） |
| `SHARE_NOT_FOUND` | 404 | token 不存在/撤销/过期（统一） |
| `SHARE_CREATE_FORBIDDEN` | 404 | 非项目成员创建分享（按资源隐藏） |
| `SHARE_LIMIT_EXCEEDED` | 429 | 单报告活跃分享超上限（建议 50） |

### 15.6 知识库 / 通知 / 审计 / Telemetry（§11–§14）

| code | HTTP | 触发场景 |
|---|---|---|
| `KNOWLEDGE_ITEM_NOT_FOUND` | 404 | 条目不存在/越权（统一） |
| `KNOWLEDGE_SEARCH_QUERY_TOO_SHORT` | 422 | 检索 query 少于 2 字符 |
| `KNOWLEDGE_ITEM_DELETE_FORBIDDEN` | 404 | 无删除权限（按资源隐藏） |
| `NOTIFICATION_NOT_FOUND` | 404 | 通知不存在（仅本人可见） |
| `AUDIT_ACCESS_DENIED` | 404 | 非 owner/admin 读取审计（按资源隐藏） |
| `TELEMETRY_BATCH_INVALID` | 422 | 批尺寸/命名/时间戳越界 |
| `TELEMETRY_RATE_LIMITED` | 429 | 批量上报频率超限 |

---

## 16. 权限矩阵补充

> 后端已存在 RBAC 角色枚举（owner / admin / researcher / reviewer）与项目成员关系，前端 §13.3 已按该枚举暂定映射。下表仅补充本草案涉及动作的授权推荐，**评审确认后替换/并入后端权限模型**，不重复造轮子。
> 图例：● 允许 · ○ 仅本人/仅创建者 · – 禁止（访问一律表现为 404，见 §15 资源隐藏原则）。

| 动作 | owner | admin | researcher | reviewer | 章节 |
|---|---|---|---|---|---|
| 查看项目/报告/运行 | ● | ● | ● | ● | — |
| 发起 run / 暂停 / 恢复 / 取消 | ● | ● | ● | – | §6.1 |
| 介入：追加追问 / 排除证据 / 重试回溯 | ● | ● | ● | – | §6.2、§6.4 |
| 分歧裁决 verdict | ● | ● | ● | ○（被指派时） | §6.3 |
| 子问题计划确认 gate（若落地） | ● | ● | ● | – | §6.5 |
| 批注：创建 / 回复 / 编辑删除自己 | ● | ● | ● | ● | §7 |
| 批注：删除他人 | ● | ● | ○（仅本 run 报告创建者） | – | §7 |
| 标存疑 doubt | ● | ● | ● | ● | §8 |
| 触发存疑重生成 / 报告版本化 | ● | ● | ○（仅本 run 报告创建者） | – | §8.2 |
| 报告导出 | ● | ● | ● | ● | §9 |
| 创建只读分享 | ● | ● | ● | ○（仅自己可见范围报告） | §10 |
| 撤销分享 | ● | ● | ● | ○（仅自己创建的） | §10 |
| 知识库：浏览 / 检索 | ● | ● | ● | ● | §11 |
| 知识库：手工沉淀 / 改标签标题 / 删除自己条目 | ● | ● | ● | ○（仅创建者） | §11.4 |
| 知识库：credibility 修正 / visibility 提升 / 删除他人条目 | ● | ● | – | – | §11.4 |
| 成员与项目设置 / 角色变更 | ● | ● | – | – | M4/M6 |
| 审计日志读取 | ● | ● | – | – | §13 |
| Telemetry 上报 | ● | ● | ● | ● | §14 |

补充说明：

- 表内 `researcher` 对"仅本人 run"的动作标注 ○（仅本 run 报告创建者）：含义为对其他 run 的报告不具该权限（§7.2 批注删除、§8.2 重生成语义一致）。
- "越权统一 404"只在目标资源本身对调用者不可见时使用；调用者确为本项目成员、仅缺特定动作权限（如非创建者删除他人批注）时，仍建议返回 404 避免语义探测（评审项：与后端现有权限错误策略对齐）。

---

## 17. 里程碑窗口与 OpenAPI 落地

### 17.1 章节 → 里程碑窗口

| 章节 | 内容 | 里程碑窗口 | 发布版本 | 落地对象 |
|---|---|---|---|---|
| §4 | 事件 schema 修正定版 | M2–M3 | v1.0 | 替换后端详设事件表（§4 对应） |
| §5 | 鉴权与会话补充 | M1–M2 | v1.0 | 后端详设鉴权章 |
| §6 | 运行控制 / HITL 定版 | M2–M3 | v1.0 | 后端详设运行控制章 |
| §6.4–6.6 | 重试 / 计划 gate / 成本预估 | M2–M3（评审后） | v1.0 | 需产品确认（§18.2 #9、§18.3 #1/#7） |
| §7 | 报告批注与 @提及 | M4 | v1.1 | 新增能力 |
| §8 | 标存疑与版本化重生成 | M4 | v1.1 | 新增能力 |
| §9 | 报告导出任务 | M4 | v1.0（打磨期） | 新增能力 |
| §10 | 报告只读分享 | M4 | v1.0（打磨期） | 新增能力 |
| §11 | 知识库列表与检索 | M5 | v1.1 | 新增能力 |
| §12 | 站内通知中心 | M4 基础 / M6 扩展 | v1.0 起 | 新增能力 |
| §13 | 审计日志列表 | M6 | v1.1+ | 新增能力（管理员工具） |
| §14 | Telemetry 批量上报 | M2 起（可后置） | v1.0 | 新增能力 |
| §15–§16 | 错误码 / 权限矩阵 | 随各章 | — | 并入后端详设对应章节 |

### 17.2 OpenAPI 落地路径

评审通过后按以下顺序合入，完成"后端契约草案 → 可执行契约"闭环：

1. **后端评审拍板**：本文档全部"评审项 / 决策项"勾销（§18 评审清单）；冲突项以后端详设为准并回写本表。
2. **契约落载体**：各章请求/响应/错误码并入后端详细设计，并由 `app/export_openapi.py` 导出为 `docs/contract/openapi-*.json` 累积冻结快照（当前 openapi-m2-2.json）作为权威 schema；事件 schema 并入后端详设事件表（WS 帧不进 OpenAPI）。
3. **前端生成管线**：后端发布累积冻结快照 `docs/contract/openapi-*.json`（当前 openapi-m2-2.json）→ 前端按 §3.3 `openapi-generator typescript-fetch` 生成 client 与类型；同步**删除前端 §18.1 手写草案注释与手写 d.ts 映射**，避免双源漂移。
4. **差异清单关闭**：前端详细设计 §18.2 #1–#9、§18.3 #1–#7 逐条回填结论（§4–§6 定版关闭 #1–#7，新增通道随产品决策关闭 #8/#9）。
5. **快照固化**：本文档归档为"已评审快照"，后续变更走 OpenAPI diff，不再维护第二份人工契约。

---

## 18. 评审记录与版本

### 18.1 评审清单（2026-09-13 据实回填）

| 章节 | 状态 | 评审备注 |
|---|---|---|
| §4 事件定版 | 部分落地/部分预案 | §4.1 interrupt.requested、§4.2 cost.warning 已评审，随 M2-3~M2-5 落地；§4.3 报告流 SSE 未被 M1 冻结契约采纳、M2 不实现（后端详细设计 §4.4 状态说明）；§4.4/4.5 随 M4 |
| §5 鉴权补充 | 部分落地 | §5.1 refresh 已随 M1 落地；§5.2 logout 为 M1 软登出（黑名单随后续工作包）；§5.3 WS 子协议（`bearer`）已评审，切换窗口随 M2 后续批次，当前仍为 query token |
| §6 运行控制 | 部分落地 | §6.3 verdict 已随 M2-2 落地（走 /conflicts/{id}/verdict 自动恢复，非 /runs/resume 形态 B）；§6.1/6.2/6.4-6.6 已评审为预案，随 M2-4/M2-5 落地 |
| §7 批注与 @ | 提案（M4） | 未评审，M4 启动前评审 |
| §8 标存疑 | 提案（M4） | 未评审 |
| §9 导出任务 | 提案（M4） | 未评审；导出里程碑已统一为 M4（见 PRD §9 V2 与 SDP M4-4） |
| §10 只读分享 | 提案（M4） | 未评审 |
| §11 知识库 | 提案（M5） | 未评审 |
| §12 通知中心 | 提案（M4 基础/M6 扩展） | 未评审 |
| §13 审计 / §14 Telemetry | §14 预案随 M2、§13 提案 M6 | §14 Telemetry 批量上报随 M2 埋点工作包落地；§13 审计列表 M6 评审 |
| §15 错误码 / §16 权限矩阵 | 随章 | 已落地章节的错误码随对应冻结契约生效（如 conflict 409/422）；其余随各章评审 |

### 18.2 版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-09 | 草案初版：承接前端 §18.2 差异清单与 §18.3 产品待确认，补齐/定版 §1–§17，待后端评审 |
| v0.2 | 2026-09-13 | 状态回填：§18.1 评审清单按 M1/M2-1/M2-2 实际落地情况分章标注（§5.1/5.2/6.3 已落地，§4.1/4.2/5.3/6.1/6.2/6.4-6.6 为已评审预案，§4.3 报告流 SSE 标注未采纳，§7-14 保持 M4+ 提案）；§17.2 契约载体更正为 docs/contract 累积冻结快照；文首状态同步 |
