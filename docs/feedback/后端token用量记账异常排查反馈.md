# 后端 token 用量记账异常排查反馈（research_runs/stages.token_used）

> 提出方：后端开发（自查）
> 日期：2026-09-15
> 关联：M2-8a 交接 §5.2 观察 3；M2-8b 交接 §7.4 观察 1；M2-3 实时成本域；PRD A8（埋点指标可信度）
> 状态：**主链路已查明并修复（随本单提交）**；两项口径/硬化项挂后续（见 §4）

---

## 0. TL;DR

1. M2-8a 交接报告的「真实 run 但 run/stages/cost-snapshot 三处 token 全 0」**当前无法复现**：近 15 个真实 run（含 cancelled）中所有 succeeded 的 `research_runs.token_used` 均非零（12,927~33,397），DeepSeek 结构化调用 6/6 返回 usage。判断为当时环境瞬态（疑似 LLM 密钥/意图降级窗口），非现行链路缺陷。
2. 排查中**实锤一条真实记账缺陷并已修复**：pause→resume 续跑时，残留阶段行的收口发生在「新阶段首个 debug 帧」，此时还没有 values 快照、累计值为 0——表现为 resume run 的暂停前阶段行 token_used 记 0（P2 残留行收口引入的取值时机错误）。
3. **run 级累计与 checkpoint 恢复链路经数据验证是正确的**：resume 后 `state.token_used` 含暂停前消耗（retrieve 行快照 3,638 = clarify 1,619 + decompose ~2,019 可佐证）；成本闸门、A8 成功率指标读的 run 级数字可信。
4. 另发现 `stages.token_used` 的语义是「截至该阶段完成时的 run 累计快照」而非「阶段自身消耗」（retrieve/standardize 因无 LLM 调用而与上一阶段恒等），属展示口径项，不影响闸门与 A8。

## 1. 记账链路梳理（事实）

```text
DeepSeek 非流式 json_object 响应
  → OpenAIProvider.chat: _extract_usage(AIMessage.usage_metadata)   app/provider/openai.py
  → LLMClient.chat/complete_structured: UsageTracker + Prometheus   app/provider/client.py
  → 节点（clarifier/sub_questioner/critic/reporter）:
      state["token_used"] = 旧累计 + completion.usage.total_tokens  各 nodes/*.py
  → LangGraph PostgresSaver 每超步持久化 state（token_used 随之跨进程/跨暂停保留）
  → executor._astream_and_publish:
      values 快照 → latest_used → 阶段行收口/RunCostEmitter 帧    app/orchestrator/executor.py
  → _drive_to_terminal: run.token_used 写回 + stage.report 收口
  → 读取：GET /runs/{id}/cost/snapshot 与看板均读 research_runs.token_used（run 级累计）
```

- 检索链路（researcher_fan_out / retrieval）无任何 LLM 调用（纯 HTTP），不产生 token；故 retrieve/standardize 阶段行值恒等于上一阶段（快照语义）。
- `stream()` 通道不提取 usage，但生产节点全部走 `complete_structured`（非流式），无实际漏计路径。
- 降级路径（LLM 不可用/调用失败）consumed_tokens=0 是设计行为。

## 2. 实锤缺陷：resume 收口取值时机（已修复）

### 2.1 现象（2026-09-15 数据取证）

| run | 经历 | 问题阶段行 | run 级 token_used |
| --- | --- | --- | --- |
| `01M2HV1M8FTJVAB62ARGDVS5WX` | clarify 中 pause→proceed resume | clarify 行 running/0（未收口） | 20,519（正确） |
| `01M2HXH6ZR7W1QCBG4VE5PFQDG` | decompose 中 pause→resume | decompose 行 running/0（未收口） | 21,391（正确） |
| `01M2HR3W8NA20VEMAZ4DQ4C9MB` | M2-8b 真链 pause@clarify→resume | clarify 行 running/0 | 24,142（正确） |
| 当日 8 个无暂停 succeeded run | 一次跑完 | 全部阶段行正常非零 | 12,927~33,397 |

相关性：阶段行 0 值/残留 **只出现在发生过 pause→resume 的 run 的暂停前阶段**，一次跑完的 run 无一命中。

### 2.2 根因

M2-8b 交接 §7.4 观察 1 的 P2 残留行收口逻辑（2026-09-15 早些时候 `3dd1be3` 修复）在「resume 后首个 debug task 帧」收口前序 running 阶段行；但 debug 帧先于配对的 values 帧到达，该时刻 `latest_used=0`，会把残留行写成 succeeded/token=0（且 finished 帧早于正确时机）。今日修复前的旧代码则是干脆不收口（行残留 running）——两种表现同一根因：**收口时机没有真实 values 快照可取**。

### 2.3 修复

`app/orchestrator/executor.py` `_astream_and_publish`：

- resume 后首个业务帧（`last_stage is None` 且存在前序 running 行）时，debug 帧只记录 `pending_stage`，**不立即收口/打开**；
- 配对的 values 快照到达后，先用快照真实累计（含暂停前消耗）收口全部前序 running 行、补发 `stage.finished`，再打开新阶段发 `stage.started`，保证 finished(旧) 先于 started(新)；
- 首跑/澄清重放首帧（前序行均 pending，无残留）保持 M2-4「task 帧立即打开阶段」的 ask_followup 受理窗口语义不变。

单测：`test_executor.py::test_resume_first_frame_closes_stale_running_stage_rows`（断言残留行收口、token 取快照非零、finished→started 全局顺序）、`test_first_run_first_frame_does_not_close_prior_rows`（首跑不延迟）。全量 697 passed（净增同前），mypy 53 与基线一致。

### 2.4 未做真链复验的说明

三进程 pause→resume 真链已于 M2-8b 回归覆盖（跨进程帧/终态）；本次修复为确定性流时序逻辑，离线假流单测已精确断言取值与帧序，未重复消耗真实 LLM 跑真链。如下轮联调可顺带验证：pause@clarify→proceed 恢复后，GET /runs/{id}/stages 的 clarify 行 status=succeeded 且 token_used 非零。

## 3. M2-8a「全 0」历史报告的结论

- 当日报案的两个 run 已过保留窗口、无对应审计/日志现场；当前代码与同环境实测不能复现（succeeded run run 级全部非零；DeepSeek 6 次探针 usage 318/325/286/318/263/272 均正常）。
- 链路审查未发现「全部节点系统性记 0」的现行路径。最可能的当时诱因：LLM 密钥/Provider 注入瞬态或意图 2s 竞速导致整 run 走降级（降级 token=0 是设计行为，但 run 仍可能成功）。
- 处置：不修改记账主链路；建议补一条低成本观测（见 §4.1），若再次出现可立即定位是「降级」还是「usage 真缺失」。

## 4. 挂后续项（不阻断 M3 启动）

1. **【硬化，建议 M3 顺手做】usage=0 可观测**：`complete_structured` 成功返回但 `total_tokens=0` 且非显式降级时打 warning（含 model/finish_reason/run_id 上下文）；节点把「LLM 真实调用但 usage 缺失」与「降级启发式」在 state/帧上区分为两种计数来源。当前零观测手段是 M2-8a 无法回溯的直接原因。
2. **【口径，需产品/前端确认】stages.token_used 是累计快照**：若看板/报告要展示「各阶段自身消耗」，应由相邻阶段快照差值计算（或后端 stages 出 `stage_tokens_delta`），不能直接读本行；当前前端 `mapStages` 仅用于状态映射（succeeded→done），未消费该数值做金额/用量展示，无现存错误展示。
3. **【测试基建，既有问题】test_executor + test_ws_commands 联跑存在顺序相关 flaky**：基线代码 3 次中 2 次有 1 个 WS 用例失败（失败用例不固定），单文件/全量随机顺序均通过；疑似全局内存 registry/WS portal loop 残留。与本修复无关，建议 M3 测试治理时收敛（WS 用例统一注入独立 registry 或 fixture 复位全局单例）。

## 5. 影响面评估

- 成本闸门（90% 挂起）、`token.usage.update`/cost.warning 帧、`/cost/snapshot`、A8「报告生成成功率」等均读 run 级 `token_used`，该值经数据验证正确，**不受 §2 阶段行缺陷影响**。
- §2 缺陷的用户可见面仅为：resume run 结束后查「阶段列表」时暂停前阶段显示 running（旧）或 token=0（中间修复）；看板终态映射与阶段进度不依赖该数值。
