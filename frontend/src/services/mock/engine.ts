// Mock 剧本引擎（WP-10）：剧本生命周期、pause/resume/cancel/intervene 控制与事件归约
// REST 路由与 WS 指令通道共用本文件，保证两条介入通道行为等价（[前端详细设计 §9.5]）。
// 数据原则：WS 事件为唯一事实源，归约落 store 的 byRun 集合；REST 列表端点只读这些集合。

import {
  store,
  generateId,
  nowIso,
  type MockConflict,
  type MockEvidence,
  type MockExecution,
  type MockRun,
  type MockStageRecord,
  type MockSubQuestion
} from './store'
import { broadcastWsEvent, closeWsConnections, setCommandHandler } from './realtime'
import type { WsCommandInput, WsCommandReply } from './realtime'
import { executeNode } from './script/runner'
import type { ScriptNode } from './script/types'
import {
  buildDemoArtifacts,
  buildDemoClarifyScript,
  buildDemoConflict,
  buildDemoEvidence,
  buildDemoResumeScript,
  buildDemoSubQuestions
} from './fixtures/demo_full'
import { buildReportMarkdown } from './fixtures/happy_path'
import { TERMINAL_EVENT_TYPES, REALTIME_PROTOCOL_VERSION } from '../realtime/types'
import type { RealtimeEnvelope } from '../realtime/types'

// 控制动作结果：成功携带 RunControlResponse 形态；失败携带 HTTP 状态与错误信封
export type ControlResult =
  | { ok: true; runId: string; status: MockRun['status'] }
  | { ok: false; httpStatus: number; code: string; message: string }

// 剧本被取消时用于静默终止执行链（区别于真实异常，不打错误日志）
class ScriptAbortedError extends Error {
  constructor() {
    super('SCRIPT_ABORTED')
    this.name = 'ScriptAbortedError'
  }
}

// ─── 剧本启动 ───

// 启动一段剧本：注册执行句柄、接入归约/广播管线与 gate 控制
export function startScript(
  run: MockRun,
  script: ScriptNode,
  scriptName: MockExecution['script'],
  phase: MockExecution['phase']
): void {
  const execution: MockExecution = {
    script: scriptName,
    phase,
    pauseRequested: false,
    gateResolver: null,
    cancelRequested: false
  }
  store.executions.set(run.id, execution)

  void executeNode(script, {
    runId: run.id,
    sendEvent: (event) => dispatchEvent(run.id, event),
    awaitGate: (gateId) => awaitGate(run.id, gateId)
  }).catch((err: unknown) => {
    if (err instanceof ScriptAbortedError) return
    console.error('[mock-gateway] 剧本执行失败:', err)
  })
}

// demo_full 首次创建：phase1 澄清挂起剧本
export function startDemoClarify(run: MockRun): void {
  // 预建子问题/证据/冲突集合，保证 resume 前 GET 列表也能读到分解计划
  seedDemoCollections(run, { withEvidence: false })
  startScript(run, buildDemoClarifyScript(run), 'demo_full', 'running')
}

// ─── gate：阶段边界安全点 ───

async function awaitGate(runId: string, _gateId: string): Promise<void> {
  const exec = store.executions.get(runId)
  if (!exec || exec.cancelRequested) throw new ScriptAbortedError()
  if (exec.pauseRequested) {
    await new Promise<void>((resolve) => {
      exec.gateResolver = resolve
    })
  }
  if (exec.cancelRequested) throw new ScriptAbortedError()
}

// ─── 事件分发：取消即中止；归约 + 广播；终态关流 ───

function dispatchEvent(runId: string, env: RealtimeEnvelope): void {
  const exec = store.executions.get(runId)
  if (exec?.cancelRequested) throw new ScriptAbortedError()
  applyEvent(runId, env)
  broadcastWsEvent(runId, env)
  if (TERMINAL_EVENT_TYPES.has(env.type)) {
    closeWsConnections(runId)
  }
}

// ─── 控制动作（REST 与 WS 指令共用）───

// POST /runs/{id}/pause：软暂停，剧本在下一个 gate 安全点挂起
export function controlPause(runId: string): ControlResult {
  const run = store.runs.get(runId)
  const exec = store.executions.get(runId)
  if (!run) return notFound()
  if (run.status === 'paused') return conflict('RUN_ALREADY_PAUSED', '研究运行已处于暂停状态')
  if (run.status !== 'running' || !exec || exec.phase !== 'running') {
    return conflict('RUN_NOT_PAUSABLE', '仅进行中的研究运行可以暂停')
  }
  run.status = 'paused'
  run.updated_at = nowIso()
  exec.pauseRequested = true
  return { ok: true, runId, status: 'paused' }
}

// POST /runs/{id}/resume：两种恢复语义
// 1) 澄清挂起（phase=clarify_pending）：携带 answers 启动 phase2
// 2) 软暂停（pauseRequested）：从 gate 处继续
export function controlResume(runId: string, answers?: Record<string, unknown>): ControlResult {
  const run = store.runs.get(runId)
  const exec = store.executions.get(runId)
  if (!run || !exec) return notFound()

  if (exec.phase === 'clarify_pending') {
    if (run.status !== 'paused') {
      return conflict('RUN_NOT_RESUMABLE', '研究运行当前状态不允许恢复')
    }
    const safeAnswers: Record<string, string> = {}
    if (answers && typeof answers === 'object') {
      for (const [key, value] of Object.entries(answers)) {
        if (typeof value === 'string') safeAnswers[key] = value
      }
    }
    run.status = 'running'
    run.updated_at = nowIso()
    // phase2 剧本由引擎直接启动（paused 后 WS 已关闭，前端 resume 成功后重新建连）
    startScript(
      run,
      buildDemoResumeScript(run, safeAnswers),
      'demo_full',
      'running'
    )
    return { ok: true, runId, status: 'running' }
  }

  if (run.status === 'paused' && exec.pauseRequested) {
    run.status = 'running'
    run.updated_at = nowIso()
    exec.pauseRequested = false
    exec.gateResolver?.()
    exec.gateResolver = null
    return { ok: true, runId, status: 'running' }
  }

  return conflict('RUN_NOT_RESUMABLE', '研究运行当前状态不允许恢复')
}

// POST /runs/{id}/cancel：硬中断，幂等（终态重复取消直接返回当前状态）
export function controlCancel(runId: string): ControlResult {
  const run = store.runs.get(runId)
  if (!run) return notFound()
  if (run.status === 'cancelled' || run.status === 'succeeded' || run.status === 'failed') {
    return { ok: true, runId, status: run.status }
  }
  const exec = store.executions.get(runId)
  if (exec) {
    exec.cancelRequested = true
    // 若剧本正停在软暂停 gate 上，先解除再让其检测取消并中止
    exec.pauseRequested = false
    exec.gateResolver?.()
    exec.gateResolver = null
  }
  const ts = nowIso()
  run.status = 'cancelled'
  run.finished_at = ts
  run.updated_at = ts
  if (exec) exec.phase = 'aborted'
  // 取消事件走 REST/WS 当前通道立即广播，使看板实时收敛（绕过 dispatch 的取消中止检查）
  const cancelledEvent: RealtimeEnvelope = {
    v: REALTIME_PROTOCOL_VERSION,
    event_id: `evt_run_finished_cancel_${Date.now()}`,
    ts: Date.now(),
    run_id: runId,
    type: 'run.finished',
    payload: { status: 'cancelled', current_stage: run.current_stage, token_used: run.token_used }
  }
  broadcastWsEvent(runId, cancelledEvent)
  closeWsConnections(runId)
  return { ok: true, runId, status: 'cancelled' }
}

// POST /runs/{id}/intervene 与 WS intervene 指令：M2 开放 ask_followup / exclude_evidence
export function controlIntervene(
  runId: string,
  action: { type?: unknown; payload?: unknown }
): ControlResult {
  const run = store.runs.get(runId)
  if (!run) return notFound()
  if (run.status !== 'running' && run.status !== 'paused') {
    return conflict('INTERVENE_NOT_ALLOWED', '当前研究运行状态不允许介入操作')
  }
  const type = action.type
  const payload = (action.payload ?? {}) as Record<string, unknown>

  if (type === 'ask_followup') {
    if (typeof payload.question !== 'string' || !payload.question.trim()) {
      return validation('INVALID_ACTION_PAYLOAD', 'ask_followup 需要非空字符串字段 question')
    }
    // mock 接受追加追问（demo 固定剧本不动态改分支），REST/WS 双通道均回 ACK
    return { ok: true, runId, status: run.status }
  }

  if (type === 'exclude_evidence') {
    const evidenceId = payload.evidence_id
    if (typeof evidenceId !== 'string' || !evidenceId) {
      return validation('INVALID_ACTION_PAYLOAD', 'exclude_evidence 需要字符串字段 evidence_id')
    }
    const list = store.evidenceByRunId.get(runId) ?? []
    const target = list.find((e) => e.id === evidenceId)
    if (!target) return notFound('证据不存在或不属于该研究运行')
    // excluded=false 表示恢复已剔除证据（前端「可恢复列」）。
    // 说明：后端 M2-5 介入通道冻结前的 mock 先行形态，真实契约定稿后以前端对齐后端为准。
    const excluded = payload.excluded !== false
    target.excluded_by_user = excluded
    target.updated_at = nowIso()
    // 广播一条证据更新帧，看板据此即时置灰隐藏/恢复显示（payload 与 evidence.fetched 同构）
    const event: RealtimeEnvelope = {
      v: REALTIME_PROTOCOL_VERSION,
      event_id: `evt_evidence_excluded_${Date.now()}`,
      ts: Date.now(),
      run_id: runId,
      type: 'evidence.fetched',
      payload: {
        id: target.id,
        sub_question_id: target.sub_question_id,
        url: target.url,
        domain: target.domain,
        title: target.title,
        snippet: target.snippet,
        source_type: target.source_type,
        source_level: target.source_level,
        credibility: target.credibility,
        relevance_score: target.relevance_score,
        published_at: target.published_at ?? null,
        excluded_by_user: excluded
      }
    }
    broadcastWsEvent(runId, event)
    return { ok: true, runId, status: run.status }
  }

  return validation('UNSUPPORTED_ACTION', `M2 不支持的介入动作：${String(type)}`)
}

function notFound(message = '研究运行不存在'): ControlResult {
  return { ok: false, httpStatus: 404, code: 'not_found', message }
}

function conflict(code: string, message: string): ControlResult {
  return { ok: false, httpStatus: 409, code, message }
}

function validation(code: string, message: string): ControlResult {
  return { ok: false, httpStatus: 422, code, message }
}

// ─── WS 客户端指令处理（realtime 通道在握手后通过注入的处理器回调本文件）───

function handleWsCommand(
  runId: string,
  command: WsCommandInput
): WsCommandReply | null {
  const requestId = typeof command.request_id === 'string' ? command.request_id : ''
  if (!requestId) return null

  if (command.type === 'cancel') {
    const result = controlCancel(runId)
    return result.ok
      ? { type: 'intervene.ack', requestId, payload: { status: result.status } }
      : { type: 'intervene.error', requestId, payload: { code: result.code, message: result.message } }
  }

  if (command.type === 'intervene') {
    const result = controlIntervene(
      runId,
      (command.payload ?? {}) as { type?: unknown; payload?: unknown }
    )
    return result.ok
      ? { type: 'intervene.ack', requestId, payload: { status: result.status } }
      : { type: 'intervene.error', requestId, payload: { code: result.code, message: result.message } }
  }

  return {
    type: 'intervene.error',
    requestId,
    payload: { code: 'UNKNOWN_COMMAND', message: `未知指令类型：${String(command.type)}` }
  }
}

// 模块加载即注册：realtime 通道不静态依赖本文件，避免 import 环
setCommandHandler(handleWsCommand)

// ─── 事件归约：WS 事件 → store byRun 集合（REST 列表的唯一数据来源）───

function applyEvent(runId: string, env: RealtimeEnvelope): void {
  const run = store.runs.get(runId)
  if (!run) return
  const payload = (env.payload ?? {}) as Record<string, unknown>
  const ts = nowIso()

  switch (env.type) {
    case 'stage.started': {
      const stageName = typeof payload.stage === 'string' ? payload.stage : null
      // 控制态/终态优先：暂停挂起期间已入队的阶段事件不得把 paused 改回 running，
      // 否则 proceed 会因状态不匹配被拒（取消/终态同理不允许事件回写状态）
      if (!['paused', 'cancelled', 'succeeded', 'failed'].includes(run.status)) {
        run.status = 'running'
      }
      if (!run.started_at) run.started_at = ts
      if (stageName) run.current_stage = stageName
      run.updated_at = ts
      if (stageName) {
        upsertStage(runId, stageName, (record) => {
          record.status = 'running'
          record.attempt = typeof payload.attempt === 'number' ? payload.attempt : 1
          if (!record.started_at) record.started_at = ts
          record.updated_at = ts
        })
      }
      return
    }

    case 'stage.finished': {
      const stageName = typeof payload.stage === 'string' ? payload.stage : null
      run.updated_at = ts
      if (stageName) {
        upsertStage(runId, stageName, (record) => {
          record.status = 'succeeded'
          if (!record.started_at) record.started_at = ts
          record.finished_at = ts
          record.updated_at = ts
        })
      }
      return
    }

    case 'stage.failed': {
      const stageName = typeof payload.stage === 'string' ? payload.stage : null
      if (stageName) {
        upsertStage(runId, stageName, (record) => {
          record.status = 'failed'
          record.finished_at = ts
          record.updated_at = ts
        })
      }
      return
    }

    case 'sub_question.created':
    case 'sub_question.started':
    case 'sub_question.finished': {
      const id = typeof payload.sub_question_id === 'string' ? payload.sub_question_id : null
      if (!id) return
      upsertSubQuestion(runId, id, (record) => {
        if (typeof payload.question === 'string') record.question = payload.question
        if (Array.isArray(payload.depends_on)) {
          record.depends_on = payload.depends_on.filter((x): x is string => typeof x === 'string')
        }
        if (typeof payload.status === 'string') {
          record.status = payload.status as MockSubQuestion['status']
        }
        if (typeof payload.evidence_count === 'number') record.evidence_count = payload.evidence_count
        record.updated_at = ts
      })
      return
    }

    case 'evidence.fetched': {
      const evidence = parseEvidencePayload(runId, payload, ts)
      if (!evidence) return
      upsertEvidence(runId, evidence)
      // 同步子问题证据计数，使 GET /sub-questions 在证据流期间实时一致
      const subQuestions = store.subQuestionsByRunId.get(runId)
      const target = subQuestions?.find((q) => q.id === evidence.sub_question_id)
      if (target) {
        const count = (store.evidenceByRunId.get(runId) ?? [])
          .filter((e) => e.sub_question_id === target.id && !e.excluded_by_user).length
        target.evidence_count = count
        target.updated_at = ts
      }
      return
    }

    case 'conflict.detected': {
      const id = typeof payload.id === 'string' ? payload.id : null
      if (!id) return
      const list = store.conflictsByRunId.get(runId) ?? []
      const existing = list.find((c) => c.id === id)
      if (existing) {
        Object.assign(existing, {
          claim: stringField(payload.claim, existing.claim),
          type: stringField(payload.type, existing.type),
          severity: payload.severity as MockConflict['severity'],
          status: payload.status as MockConflict['status'],
          updated_at: ts
        })
      } else {
        list.push({
          id,
          run_id: runId,
          claim: stringField(payload.claim, ''),
          evidence_a_id: stringField(payload.evidence_a_id, ''),
          evidence_b_id: stringField(payload.evidence_b_id, ''),
          type: stringField(payload.type, 'unknown'),
          severity: (payload.severity as MockConflict['severity']) ?? 'medium',
          status: (payload.status as MockConflict['status']) ?? 'detected',
          created_at: ts,
          updated_at: ts
        })
        store.conflictsByRunId.set(runId, list)
      }
      return
    }

    case 'token.usage.update': {
      if (typeof payload.used === 'number') {
        run.token_used = payload.used
        run.updated_at = ts
      }
      return
    }

    case 'cost.warning':
      // 预警值不单独入存储：GET cost/snapshot 以 run.token_used/budget 实时计算 ratio
      run.updated_at = ts
      return

    case 'report.finished':
      // 终稿落盘在随后 run.finished(succeeded) 统一处理
      return

    case 'run.failed': {
      run.status = 'failed'
      run.current_stage = typeof payload.current_stage === 'string' ? payload.current_stage : null
      if (typeof payload.token_used === 'number') run.token_used = payload.token_used
      run.error_code = typeof payload.error_code === 'string' ? payload.error_code : 'INTERNAL_ERROR'
      run.error_message = typeof payload.error_message === 'string' ? payload.error_message : null
      run.finished_at = ts
      run.updated_at = ts
      const exec = store.executions.get(runId)
      if (exec) exec.phase = 'aborted'
      return
    }

    case 'run.finished': {
      const status = typeof payload.status === 'string' ? payload.status : 'succeeded'
      run.current_stage = typeof payload.current_stage === 'string' ? payload.current_stage : null
      if (typeof payload.token_used === 'number') run.token_used = payload.token_used

      if (status === 'paused') {
        // 澄清挂起是非失败非完成态：不写 finished_at，剧本 phase1 已自然结束
        run.status = 'paused'
        run.updated_at = ts
        const exec = store.executions.get(runId)
        if (exec) exec.phase = 'clarify_pending'
        return
      }

      run.finished_at = ts
      run.updated_at = ts

      if (status === 'cancelled') {
        run.status = 'cancelled'
        const exec = store.executions.get(runId)
        if (exec) exec.phase = 'aborted'
        return
      }

      run.status = 'succeeded'
      // 成功终态兜底：剧本漏发个别 stage.finished 时，stages 表也应收敛为全完成
      // （与前端 mapStages 在 succeeded 时的截断语义一致）
      for (const record of store.stagesByRunId.get(runId) ?? []) {
        if (record.status !== 'succeeded' && record.status !== 'skipped') {
          record.status = 'succeeded'
          if (!record.finished_at) record.finished_at = ts
          record.updated_at = ts
        }
      }
      if (status === 'succeeded') persistSucceededArtifacts(run, ts)
      const exec = store.executions.get(runId)
      if (exec) exec.phase = 'done'
      return
    }

    default:
      return
  }
}

function persistSucceededArtifacts(run: MockRun, ts: string): void {
  const exec = store.executions.get(run.id)
  if (exec?.script === 'demo_full') {
    // 终态前补齐全部证据/冲突集合（防御性：即使剧本被裁剪也能完整出报告）
    seedDemoCollections(run, { withEvidence: true })
    const artifacts = buildDemoArtifacts(run)
    store.reportsByRunId.set(run.id, {
      id: artifacts.structured.id,
      run_id: run.id,
      template_id: run.template_id,
      status: 'final',
      content_md: artifacts.markdown,
      token_used: run.token_used,
      created_at: ts,
      updated_at: ts
    })
    store.structuredReportsByRunId.set(run.id, artifacts.structured)
    store.citationsByRunId.set(run.id, artifacts.citations)
    return
  }

  // happy_path：仅 Markdown 报告（M1 既有行为）
  store.reportsByRunId.set(run.id, {
    id: generateId(),
    run_id: run.id,
    template_id: run.template_id,
    status: 'final',
    content_md: buildReportMarkdown(run),
    token_used: run.token_used,
    created_at: ts,
    updated_at: ts
  })
}

// ─── 归约辅助 ───

function upsertStage(
  runId: string,
  stageName: string,
  patch: (record: MockStageRecord) => void
): void {
  const list = store.stagesByRunId.get(runId) ?? []
  let record = list.find((s) => s.name === stageName)
  if (!record) {
    const ts = nowIso()
    record = {
      id: `${runId}-${stageName}`,
      run_id: runId,
      name: stageName,
      status: 'pending',
      attempt: 1,
      started_at: null,
      finished_at: null,
      created_at: ts,
      updated_at: ts
    }
    list.push(record)
    store.stagesByRunId.set(runId, list)
  }
  patch(record)
}

function upsertSubQuestion(
  runId: string,
  id: string,
  patch: (record: MockSubQuestion) => void
): void {
  const list = store.subQuestionsByRunId.get(runId) ?? []
  let record = list.find((q) => q.id === id)
  if (!record) {
    const ts = nowIso()
    record = {
      id,
      run_id: runId,
      question: '',
      depends_on: [],
      status: 'queued',
      evidence_count: 0,
      created_at: ts,
      updated_at: ts
    }
    list.push(record)
    store.subQuestionsByRunId.set(runId, list)
  }
  patch(record)
}

function upsertEvidence(runId: string, incoming: MockEvidence): void {
  const list = store.evidenceByRunId.get(runId) ?? []
  const existing = list.find((e) => e.id === incoming.id)
  if (existing) {
    Object.assign(existing, incoming, {
      // 保留已写入的全文与剔除态，增量帧不覆盖本地介入结果
      content: existing.content ?? incoming.content ?? null,
      excluded_by_user: existing.excluded_by_user || incoming.excluded_by_user
    })
  } else {
    list.push(incoming)
  }
  store.evidenceByRunId.set(runId, list)
}

function parseEvidencePayload(
  runId: string,
  p: Record<string, unknown>,
  ts: string
): MockEvidence | null {
  if (typeof p.id !== 'string' || typeof p.sub_question_id !== 'string') return null
  return {
    id: p.id,
    run_id: runId,
    sub_question_id: p.sub_question_id,
    url: stringField(p.url, ''),
    domain: stringField(p.domain, ''),
    title: stringField(p.title, ''),
    snippet: stringField(p.snippet, ''),
    content: null,
    source_type: (p.source_type as MockEvidence['source_type']) ?? 'search',
    source_level: (p.source_level as MockEvidence['source_level']) ?? 'tertiary',
    credibility: (p.credibility as MockEvidence['credibility']) ?? 'C',
    relevance_score: typeof p.relevance_score === 'number' ? p.relevance_score : 0,
    published_at: typeof p.published_at === 'string' ? p.published_at : null,
    fetched_at: ts,
    excluded_by_user: p.excluded_by_user === true,
    created_at: ts,
    updated_at: ts
  }
}

function stringField(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

// demo_full run 的集合预置（withEvidence=false 时仅建子问题计划，证据随流增量写入）
function seedDemoCollections(run: MockRun, options: { withEvidence: boolean }): void {
  if (!store.subQuestionsByRunId.has(run.id)) {
    store.subQuestionsByRunId.set(run.id, buildDemoSubQuestions(run.id))
  }
  if (options.withEvidence && !store.evidenceByRunId.has(run.id)) {
    store.evidenceByRunId.set(run.id, buildDemoEvidence(run.id))
  }
  if (options.withEvidence && !store.conflictsByRunId.has(run.id)) {
    const evidence = store.evidenceByRunId.get(run.id) ?? buildDemoEvidence(run.id)
    store.conflictsByRunId.set(run.id, [buildDemoConflict(run.id, evidence)])
  }
}
