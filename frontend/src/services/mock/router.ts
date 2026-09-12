// REST 路由分发：严格对齐 docs/contract/openapi-m1.json 冻结端点
// 业务错误体与后端一致：{code, message, details}；请求体校验错误与 FastAPI 一致：422 {detail:[...]}

import type { IncomingMessage, ServerResponse } from 'node:http'
import {
  store,
  generateId,
  nowIso,
  issueTokenPair,
  TIER_TOKEN_BUDGET,
  type MockRun,
  type MockReport
} from './store'
import { broadcastWsEvent, closeWsConnections } from './realtime'
import { buildHappyPathScript, buildReportMarkdown } from './fixtures/happy_path'
import { executeNode } from './script/runner'
import { TERMINAL_EVENT_TYPES } from '../realtime/types'
import type { RealtimeEnvelope } from '../realtime/types'

type RouteHandler = (req: IncomingMessage, res: ServerResponse, params: Record<string, string>) => void | Promise<void>

interface Route {
  method: string
  pattern: RegExp
  paramNames: string[]
  handler: RouteHandler
}

const routes: Route[] = []

function route(method: string, path: string, handler: RouteHandler): void {
  const paramNames: string[] = []
  const patternStr = path.replace(/\{(\w+)\}/g, (_, name: string) => {
    paramNames.push(name)
    return '([^/]+)'
  })
  routes.push({ method, pattern: new RegExp(`^${patternStr}$`), paramNames, handler })
}

// ─── body 预读（网关入口同步抢占 data 监听，结果存入 WeakMap）───

const bufferedBodies = new WeakMap<IncomingMessage, unknown>()

export function prebufferBodySync(req: IncomingMessage): Promise<unknown> {
  const cached = bufferedBodies.get(req)
  if (cached !== undefined) return Promise.resolve(cached)
  return new Promise((resolve) => {
    let raw = ''
    let settled = false
    const finish = (value: unknown): void => {
      if (settled) return
      settled = true
      bufferedBodies.set(req, value)
      resolve(value)
    }
    req.on('data', (chunk: Buffer | string) => { raw += chunk.toString() })
    req.on('end', () => finish(parseJsonLenient(raw)))
    req.on('error', () => finish({}))
  })
}

async function parseBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const value = bufferedBodies.get(req)
  if (value && typeof value === 'object') return value as Record<string, unknown>
  const parsed = await prebufferBodySync(req)
  return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
}

// 宽松 JSON 解析：剥离 UTF-8 BOM，失败返回空对象
function parseJsonLenient(raw: string): unknown {
  const trimmed = raw.replace(/^\uFEFF/, '').trimStart()
  if (!trimmed) return {}
  try {
    return JSON.parse(trimmed)
  } catch {
    return {}
  }
}

// ─── 响应工具 ───

function sendJson(res: ServerResponse, status: number, data: unknown): void {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'X-Trace-Id': `mock-${Date.now()}`
  })
  res.end(JSON.stringify(data))
}

// 业务异常响应（与后端 main.py AppError handler 同构）
function sendAppError(
  res: ServerResponse,
  status: number,
  code: string,
  message: string,
  details: Record<string, unknown> = {}
): void {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'X-Trace-Id': `mock-${Date.now()}`
  })
  res.end(JSON.stringify({ code, message, details }))
}

// FastAPI 请求体校验错误
interface ValidationItem {
  loc: Array<string | number>
  msg: string
  type: string
}

function sendValidationError(res: ServerResponse, items: ValidationItem[]): void {
  res.writeHead(422, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'X-Trace-Id': `mock-${Date.now()}`
  })
  res.end(JSON.stringify({ detail: items }))
}

function missingField(res: ServerResponse, field: string): void {
  sendValidationError(res, [{ loc: ['body', field], msg: 'Field required', type: 'missing' }])
}

// Bearer 鉴权：失败已写 401 响应时返回 null
function authenticate(req: IncomingMessage, res: ServerResponse) {
  const header = req.headers.authorization ?? ''
  const [scheme, token] = header.split(' ')
  if (scheme !== 'Bearer' || !token) {
    sendAppError(res, 401, 'auth_error', '缺少有效的 Bearer 凭证')
    return null
  }
  if (!store.accessTokens.has(token)) {
    sendAppError(res, 401, 'auth_error', '令牌无效或已过期')
    return null
  }
  const user = store.users.get(store.currentUserId)
  if (!user) {
    sendAppError(res, 401, 'auth_error', '用户不存在')
    return null
  }
  return user
}

// 路由匹配与分发
export async function handleRequest(req: IncomingMessage, res: ServerResponse): Promise<boolean> {
  const url = new URL(req.url ?? '/', 'http://localhost')
  const pathname = url.pathname
  const method = req.method ?? 'GET'

  if (method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization, Idempotency-Key'
    })
    res.end()
    return true
  }

  if (method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE') {
    await prebufferBodySync(req)
  }

  for (const r of routes) {
    if (r.method !== method) continue
    const match = pathname.match(r.pattern)
    if (!match) continue

    const params: Record<string, string> = {}
    for (let i = 0; i < r.paramNames.length; i++) {
      params[r.paramNames[i]] = decodeURIComponent(match[i + 1])
    }

    try {
      await r.handler(req, res, params)
    } catch (err) {
      sendAppError(res, 500, 'internal_error', String(err))
    }
    return true
  }

  // 未命中的 API 请求返回 JSON 404（避免落到 Vite HTML 404）
  if (pathname.startsWith('/api/v1/')) {
    sendAppError(res, 404, 'not_found', `Not Found: ${method} ${pathname}`)
    return true
  }
  return false
}

// ─── meta ───

route('GET', '/healthz', (_req, res) => {
  sendJson(res, 200, {
    status: 'ok',
    service: 'ai-research-assistant (mock)',
    version: '0.1.0-mock',
    timestamp: nowIso()
  })
})

// ─── auth ───

const TOKEN_TTL_SECONDS = 3600

route('POST', '/api/v1/auth/login', async (req, res) => {
  const body = await parseBody(req)
  const email = body.email
  const password = body.password
  if (typeof email !== 'string' || !email) {
    missingField(res, 'email')
    return
  }
  if (typeof password !== 'string' || !password) {
    missingField(res, 'password')
    return
  }

  const user = Array.from(store.users.values()).find((u) => u.email === email)
  // mock 不校验密码原文：种子用户 + 任意非空密码即可登录（密码非空已在上面校验）
  if (!user) {
    sendAppError(res, 401, 'auth_error', '邮箱或密码错误')
    return
  }
  user.last_login_at = nowIso()

  const { accessToken, refreshToken } = issueTokenPair()
  sendJson(res, 200, {
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: 'bearer',
    expires_in: TOKEN_TTL_SECONDS
  })
})

route('POST', '/api/v1/auth/refresh', async (req, res) => {
  const body = await parseBody(req)
  const refreshToken = body.refresh_token
  if (typeof refreshToken !== 'string' || !refreshToken) {
    missingField(res, 'refresh_token')
    return
  }
  if (!store.refreshTokens.has(refreshToken)) {
    sendAppError(res, 401, 'auth_error', '刷新令牌无效或已过期')
    return
  }
  // refresh token 轮换：旧 token 立即失效
  store.refreshTokens.delete(refreshToken)
  const pair = issueTokenPair()
  sendJson(res, 200, {
    access_token: pair.accessToken,
    refresh_token: pair.refreshToken,
    token_type: 'bearer',
    expires_in: TOKEN_TTL_SECONDS
  })
})

route('POST', '/api/v1/auth/logout', (req, res) => {
  const user = authenticate(req, res)
  if (!user) return
  user.last_login_at = nowIso()
  sendJson(res, 200, { status: 'ok' })
})

route('GET', '/api/v1/auth/me', (req, res) => {
  const user = authenticate(req, res)
  if (!user) return
  sendJson(res, 200, {
    id: user.id,
    team_id: user.team_id,
    email: user.email,
    display_name: user.display_name,
    role: user.role,
    last_login_at: user.last_login_at ?? null
  })
})

// ─── projects ───

const TIERS = ['quick', 'standard', 'deep', 'extreme'] as const

route('GET', '/api/v1/projects', (req, res) => {
  const user = authenticate(req, res)
  if (!user) return
  const items = Array.from(store.projects.values())
    .filter((p) => p.team_id === user.team_id && p.status === 'active')
  sendJson(res, 200, items)
})

route('POST', '/api/v1/projects', async (req, res) => {
  const user = authenticate(req, res)
  if (!user) return
  const body = await parseBody(req)

  if (typeof body.name !== 'string' || !body.name.trim()) {
    missingField(res, 'name')
    return
  }
  const tier = body.default_tier ?? 'standard'
  if (typeof tier !== 'string' || !TIERS.includes(tier as (typeof TIERS)[number])) {
    sendValidationError(res, [{
      loc: ['body', 'default_tier'],
      msg: `Input should be one of: ${TIERS.join(', ')}`,
      type: 'enum'
    }])
    return
  }

  const ts = nowIso()
  const project = {
    id: generateId(),
    team_id: user.team_id,
    owner_id: user.id,
    name: body.name,
    description: typeof body.description === 'string' ? body.description : null,
    default_template_id: typeof body.default_template_id === 'string' ? body.default_template_id : null,
    default_tier: tier as MockRun['tier'],
    status: 'active' as const,
    created_at: ts,
    updated_at: ts
  }
  store.projects.set(project.id, project)
  sendJson(res, 201, project)
})

// ─── runs ───

route('POST', '/api/v1/runs', async (req, res) => {
  const user = authenticate(req, res)
  if (!user) return
  const body = await parseBody(req)

  if (typeof body.project_id !== 'string' || !body.project_id) {
    missingField(res, 'project_id')
    return
  }
  if (typeof body.question !== 'string' || body.question.trim().length < 5) {
    sendValidationError(res, [{
      loc: ['body', 'question'],
      msg: 'String should have at least 5 characters',
      type: 'string_too_short'
    }])
    return
  }
  const tier = (body.tier ?? 'standard') as string
  if (!TIERS.includes(tier as (typeof TIERS)[number])) {
    sendValidationError(res, [{
      loc: ['body', 'tier'],
      msg: `Input should be one of: ${TIERS.join(', ')}`,
      type: 'enum'
    }])
    return
  }

  const project = store.projects.get(body.project_id)
  if (!project || project.team_id !== user.team_id || project.status !== 'active') {
    sendAppError(res, 404, 'not_found', '项目不存在或不属于当前团队')
    return
  }

  const ts = nowIso()
  const runId = generateId()
  const run: MockRun = {
    id: runId,
    project_id: project.id,
    creator_id: user.id,
    template_id: typeof body.template_id === 'string' && body.template_id ? body.template_id : 'generic',
    tier: tier as MockRun['tier'],
    question: body.question,
    status: 'pending',
    current_stage: null,
    token_used: 0,
    token_budget: TIER_TOKEN_BUDGET[tier as MockRun['tier']],
    started_at: null,
    finished_at: null,
    error_code: null,
    error_message: null,
    stream_url: `/api/v1/ws/runs/${runId}/stream`,
    created_at: ts,
    updated_at: ts
  }
  store.runs.set(runId, run)

  // 后台执行剧本：事件同时更新内存态并广播到 WS
  void executeNode(buildHappyPathScript(run), {
    runId,
    sendEvent: (event) => {
      applyEventToRun(runId, event)
      broadcastWsEvent(runId, event)
      // 与后端一致：终态事件推送后关闭连接
      if (TERMINAL_EVENT_TYPES.has(event.type)) {
        closeWsConnections(runId)
      }
    }
  }).catch((err: unknown) => {
    console.error('[mock-gateway] 剧本执行失败:', err)
  })

  sendJson(res, 201, run)
})

route('GET', '/api/v1/runs/{run_id}', (req, res, params) => {
  const user = authenticate(req, res)
  if (!user) return
  const run = store.runs.get(params.run_id)
  if (!run || run.creator_id !== user.id) {
    sendAppError(res, 404, 'not_found', '研究运行不存在')
    return
  }
  sendJson(res, 200, run)
})

// ─── reports ───

function loadOwnedReport(req: IncomingMessage, res: ServerResponse, runId: string): MockReport | null {
  const user = authenticate(req, res)
  if (!user) return null
  const run = store.runs.get(runId)
  if (!run || run.creator_id !== user.id) {
    sendAppError(res, 404, 'not_found', '研究运行不存在')
    return null
  }
  const report = store.reportsByRunId.get(runId)
  if (!report) {
    // 与后端一致：run 存在但报告未产出时返回 422
    sendAppError(res, 422, 'validation_error', '报告尚未生成')
    return null
  }
  return report
}

route('GET', '/api/v1/runs/{run_id}/report', (req, res, params) => {
  const report = loadOwnedReport(req, res, params.run_id)
  if (!report) return
  sendJson(res, 200, report)
})

route('GET', '/api/v1/reports/{run_id}', (req, res, params) => {
  const report = loadOwnedReport(req, res, params.run_id)
  if (!report) return
  sendJson(res, 200, report)
})

// ─── 意图路由（M2-1：POST /intent/classify，启发式三分类，供 mock 模式联调）───

const TIER_BUDGET: Record<string, number> = {
  quick: 50_000,
  standard: 150_000,
  deep: 400_000,
  extreme: 1_000_000
}

// 闲聊启发式关键词（与后端离线评估分桶口径近似，mock 仅用于 UI 分流演示）
const CHAT_HINTS = ['你好', '您好', '谢谢', '再见', '你是谁', '讲个笑话', 'hello', 'hi', '在吗']
const RESEARCH_HINTS = ['对比', '分析', '趋势', '调研', '研究', '市场', '技术', '方案', '报告', '为什么', '如何']

route('POST', '/api/v1/intent/classify', async (req, res) => {
  const user = authenticate(req, res)
  if (!user) return
  const body = await parseBody(req)
  const text = typeof body.text === 'string' ? body.text.trim() : ''
  if (!text || text.length > 2000) {
    sendValidationError(res, [
      { loc: ['body', 'text'], msg: text ? 'String should have at most 2000 characters' : 'Field required', type: text ? 'string_too_long' : 'missing' }
    ])
    return
  }
  const force = body.force
  if (force !== undefined && force !== null && force !== 'chat' && force !== 'research') {
    sendValidationError(res, [{ loc: ['body', 'force'], msg: "Input should be 'chat' or 'research'", type: 'literal_error' }])
    return
  }

  // 强制路径：完全绕过判别（source=forced，confidence=1.0）
  if (force === 'chat' || force === 'research') {
    sendJson(res, 200, force === 'chat'
      ? { intent: 'chat', confidence: 1, recommended_template: null, recommended_tier: null, estimated_token_budget: null, estimated_cost_grade: null, source: 'forced', degraded: false, reason: '用户强制闲聊' }
      : { intent: 'research', confidence: 1, recommended_template: 'generic', recommended_tier: 'quick', estimated_token_budget: TIER_BUDGET.quick, estimated_cost_grade: 'quick', source: 'forced', degraded: false, reason: '用户强制深度研究' })
    return
  }

  const isChat = CHAT_HINTS.some((hint) => text.includes(hint)) || (text.length < 8 && !RESEARCH_HINTS.some((hint) => text.includes(hint)))
  if (isChat) {
    sendJson(res, 200, {
      intent: 'chat',
      confidence: 0.96,
      recommended_template: null,
      recommended_tier: null,
      estimated_token_budget: null,
      estimated_cost_grade: null,
      source: 'llm',
      degraded: false,
      reason: '日常寒暄/常识类输入，无需多源研究'
    })
    return
  }

  const hasResearchHint = RESEARCH_HINTS.some((hint) => text.includes(hint))
  sendJson(res, 200, {
    intent: hasResearchHint ? 'research' : 'uncertain',
    confidence: hasResearchHint ? 0.9 : 0.55,
    recommended_template: 'generic',
    recommended_tier: hasResearchHint ? 'standard' : 'quick',
    estimated_token_budget: hasResearchHint ? TIER_BUDGET.standard : TIER_BUDGET.quick,
    estimated_cost_grade: hasResearchHint ? 'standard' : 'quick',
    source: 'llm',
    degraded: false,
    reason: hasResearchHint ? '含可检索实体与研究意图，建议多源核验' : '意图不够明确，保守按深度研究准备'
  })
})

// ─── 全局助手闲聊（M2-1：POST /assistant/chat，SSE 流式，无服务端会话）───

function sendSse(res: ServerResponse, event: string | null, data: string): void {
  res.write(event ? `event: ${event}\ndata: ${data}\n\n` : `data: ${data}\n\n`)
}

route('POST', '/api/v1/assistant/chat', async (req, res) => {
  const user = authenticate(req, res)
  if (!user) return
  const body = await parseBody(req)
  const message = typeof body.message === 'string' ? body.message.trim() : ''
  if (!message || message.length > 2000) {
    sendValidationError(res, [{ loc: ['body', 'message'], msg: message ? 'String should have at most 2000 characters' : 'Field required', type: message ? 'string_too_long' : 'missing' }])
    return
  }
  const history = Array.isArray(body.history) ? body.history : []
  if (history.length > 20) {
    sendValidationError(res, [{ loc: ['body', 'history'], msg: 'List should have at most 20 items', type: 'too_long' }])
    return
  }

  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Access-Control-Allow-Origin': '*'
  })

  // mock 固定回答：按片段流式吐出，模拟真实增量粒度
  const answer = `这是 mock 闲聊通道的回答。你刚才说的是：「${message}」。mock 模式不联网检索；需要多源核验的问题，请回首页发起深度研究。`
  const chunks = answer.match(/.{1,12}/g) ?? [answer]
  for (const chunk of chunks) {
    sendSse(res, null, JSON.stringify({ delta: chunk }))
    await new Promise((resolve) => setTimeout(resolve, 60))
  }
  sendSse(res, null, '[DONE]')
  res.end()
  void user
})

// ─── 事件归约：把 WS 事件更新到 run 内存态，终态时落报告 ───

function applyEventToRun(runId: string, env: RealtimeEnvelope): void {
  const run = store.runs.get(runId)
  if (!run) return
  const payload = (env.payload ?? {}) as Record<string, unknown>
  const ts = nowIso()

  if (env.type === 'stage.started') {
    run.status = 'running'
    if (!run.started_at) run.started_at = ts
    run.current_stage = typeof payload.stage === 'string' ? payload.stage : null
    run.updated_at = ts
    return
  }

  if (env.type === 'run.finished' || env.type === 'run.failed') {
    const failed = env.type === 'run.failed'
    run.status = failed
      ? 'failed'
      : (typeof payload.status === 'string' ? payload.status as MockRun['status'] : 'succeeded')
    run.current_stage = typeof payload.current_stage === 'string' ? payload.current_stage : null
    run.token_used = typeof payload.token_used === 'number' ? payload.token_used : run.token_used
    run.finished_at = ts
    run.updated_at = ts
    if (failed) {
      run.error_code = typeof payload.error_code === 'string' ? payload.error_code : 'INTERNAL_ERROR'
      run.error_message = typeof payload.error_message === 'string' ? payload.error_message : null
      return
    }
    if (run.status === 'succeeded') {
      store.reportsByRunId.set(runId, {
        id: generateId(),
        run_id: runId,
        template_id: run.template_id,
        status: 'final',
        content_md: buildReportMarkdown(run),
        token_used: run.token_used,
        created_at: ts,
        updated_at: ts
      })
    }
  }
}
