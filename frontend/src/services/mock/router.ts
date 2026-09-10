// REST 路由分发（[前端M0收尾方案 §4.3]）
// 覆盖 §3.2 端点表

import type { IncomingMessage, ServerResponse } from 'node:http'
import { store, generateId, now } from './store'
import type { MockRun } from './store'
import { broadcastWsEvent } from './realtime'
import { broadcastSseEvent } from './sse'
import { buildHappyPathScript } from './fixtures/happy_path'
import { buildDemoFullScript } from './fixtures/demo_full'
import { executeNode } from './script/runner'

// 路由表类型
type RouteHandler = (req: IncomingMessage, res: ServerResponse, params: Record<string, string>) => void | Promise<void>

interface Route {
  method: string
  pattern: RegExp
  paramNames: string[]
  handler: RouteHandler
}

// 路由注册表
const routes: Route[] = []

// 注册路由
function route(method: string, path: string, handler: RouteHandler): void {
  const paramNames: string[] = []
  const patternStr = path.replace(/\{(\w+)\}/g, (_, name) => {
    paramNames.push(name)
    return '([^/]+)'
  })
  routes.push({
    method,
    pattern: new RegExp(`^${patternStr}$`),
    paramNames,
    handler
  })
}

// 解析请求体
async function parseBody(req: IncomingMessage): Promise<any> {
  // 如果已经预读（如 middleware 入口预读），直接复用
  if ((req as any)._mockBody !== undefined) {
    return (req as any)._mockBody
  }
  return new Promise((resolve) => {
    let body = ''
    req.on('data', (chunk) => { body += chunk })
    req.on('end', () => {
      resolve(parseJsonLenient(body))
    })
  })
}

// 宽松 JSON 解析：剥离 UTF-8 BOM 与前导空白，失败返回空对象
function parseJsonLenient(raw: string): any {
  // 移除 UTF-8 BOM（PowerShell Set-Content -Encoding UTF8 会写入）与前导空白
  const trimmed = raw.replace(/^\uFEFF/, '').trimStart()
  if (!trimmed) return {}
  try {
    return JSON.parse(trimmed)
  } catch {
    return {}
  }
}

// 预读 body 并暂存到 req._mockBody（必须在路由 handler 之前调用）
// 同步启动 body 预读（必须在 await 之前调用以抢占 data 监听器）
export function prebufferBodySync(req: IncomingMessage): Promise<any> {
  if ((req as any)._mockBody !== undefined) {
    return Promise.resolve((req as any)._mockBody)
  }
  return new Promise((resolve) => {
    let body = ''
    let settled = false
    const finish = (parsed: any) => {
      if (settled) return
      settled = true
      ;(req as any)._mockBody = parsed
      resolve(parsed)
    }
    // 关键：同步 attach，确保早于其他中间件
    req.on('data', (chunk) => {
      body += chunk
    })
    req.on('end', () => {
      finish(parseJsonLenient(body))
    })
    req.on('error', () => finish({}))
  })
}

// 发送 JSON 响应
function sendJson(res: ServerResponse, status: number, data: any): void {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*'
  })
  res.end(JSON.stringify(data))
}

// 发送 RFC 7807 错误
function sendError(res: ServerResponse, status: number, code: string, title: string, detail?: string): void {
  res.writeHead(status, {
    'Content-Type': 'application/problem+json',
    'Access-Control-Allow-Origin': '*'
  })
  res.end(JSON.stringify({
    type: 'about:blank',
    title,
    status,
    detail: detail ?? '',
    code,
    trace_id: `mock-${Date.now()}`
  }))
}

// 路由匹配与分发
export async function handleRequest(req: IncomingMessage, res: ServerResponse): Promise<boolean> {
  const url = new URL(req.url ?? '/', 'http://localhost')
  const pathname = url.pathname
  const method = req.method ?? 'GET'

  // CORS 预检
  if (method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization, Idempotency-Key'
    })
    res.end()
    return true
  }

  // 对写方法，先确保 body 已预读完成
  if (method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE') {
    await prebufferBodySync(req)
  }

  for (const r of routes) {
    if (r.method !== method) continue
    const match = pathname.match(r.pattern)
    if (!match) continue

    const params: Record<string, string> = {}
    for (let i = 0; i < r.paramNames.length; i++) {
      params[r.paramNames[i]] = match[i + 1]
    }

    try {
      await r.handler(req, res, params)
    } catch (err) {
      sendError(res, 500, 'INTERNAL_ERROR', 'Internal server error', String(err))
    }
    return true
  }

  return false
}

// ─── 路由定义 ───

// 健康检查
route('GET', '/api/v1/health', (_req, res) => {
  sendJson(res, 200, { status: 'ok', ts: Date.now() })
})

// 登录
route('POST', '/api/v1/auth/login', async (_req, res) => {
  const user = store.users.get(store.currentUserId)
  if (!user) {
    sendError(res, 401, 'AUTH_FAILED', 'Authentication failed')
    return
  }
  sendJson(res, 200, {
    access_token: 'mock-access-token',
    refresh_token: 'mock-refresh-token',
    expires_in: 3600,
    user: {
      id: user.id,
      username: user.username,
      email: user.email,
      display_name: user.display_name
    }
  })
})

// 刷新 token
route('POST', '/api/v1/auth/refresh', (_req, res) => {
  sendJson(res, 200, {
    access_token: 'mock-access-token-refreshed',
    refresh_token: 'mock-refresh-token-refreshed',
    expires_in: 3600
  })
})

// 当前用户
route('GET', '/api/v1/auth/me', (_req, res) => {
  const user = store.users.get(store.currentUserId)
  if (!user) {
    sendError(res, 401, 'UNAUTHORIZED', 'Not authenticated')
    return
  }
  sendJson(res, 200, {
    id: user.id,
    username: user.username,
    email: user.email,
    display_name: user.display_name
  })
})

// 项目列表
route('GET', '/api/v1/projects', (_req, res) => {
  const items = Array.from(store.projects.values()).map((p) => ({
    id: p.id,
    name: p.name,
    description: p.description,
    owner_id: p.owner_id,
    created_at: p.created_at,
    updated_at: p.updated_at
  }))
  sendJson(res, 200, {
    items,
    total: items.length,
    page: 1,
    page_size: 50,
    has_more: false
  })
})

// 创建项目
route('POST', '/api/v1/projects', async (req, res) => {
  const body = await parseBody(req)
  const id = generateId('proj')
  const project = {
    id,
    name: body.name ?? '未命名项目',
    description: body.description ?? '',
    owner_id: store.currentUserId,
    created_at: now(),
    updated_at: now()
  }
  store.projects.set(id, project)
  sendJson(res, 201, project)
})

// 任务列表
route('GET', '/api/v1/projects/{project_id}/tasks', (_req, res, params) => {
  const tasks = Array.from(store.tasks.values())
    .filter((t) => t.project_id === params.project_id)
    .map((t) => ({
      id: t.id,
      project_id: t.project_id,
      title: t.title,
      description: t.description,
      status: t.status,
      created_at: t.created_at
    }))
  sendJson(res, 200, {
    items: tasks,
    total: tasks.length,
    page: 1,
    page_size: 50,
    has_more: false
  })
})

// 发起 run
route('POST', '/api/v1/projects/{project_id}/runs', async (req, res, params) => {
  const body = await parseBody(req)
  const runId = generateId('run')
  const run: MockRun = {
    id: runId,
    project_id: params.project_id,
    status: 'running',
    query: body.query ?? '',
    template_id: body.template_id,
    created_at: now(),
    updated_at: now(),
    started_at: now(),
    cost_used: 0,
    token_used: 0,
    stages: [
      'intent_analysis',
      'evidence_collection',
      'evidence_evaluation',
      'synthesis',
      'report_drafting',
      'report_review'
    ].map((name) => ({
      name,
      status: 'pending' as const
    }))
  }
  store.runs.set(runId, run)

  // 根据请求体 fixture 字段选择剧本（默认 happy_path）
  const fixture = body.fixture === 'demo_full' ? 'demo_full' : 'happy_path'
  const script = fixture === 'demo_full'
    ? buildDemoFullScript(runId)
    : buildHappyPathScript(runId)
  run.fixture = fixture

  // 异步启动脚本执行
  executeNode(script, {
    sendEvent: (event) => {
      // 更新 run 状态
      updateRunFromEvent(runId, event)
      // 广播到 WS 和 SSE
      broadcastWsEvent(runId, event)
      broadcastSseEvent(runId, event)
    }
  }).catch(() => {
    // 脚本执行异常
  })

  sendJson(res, 201, run)
})

// 获取 run
route('GET', '/api/v1/runs/{run_id}', (_req, res, params) => {
  const run = store.runs.get(params.run_id)
  if (!run) {
    sendError(res, 404, 'RUN_NOT_FOUND', 'Run not found')
    return
  }
  sendJson(res, 200, run)
})

// 暂停/恢复/取消 run
route('POST', '/api/v1/runs/{run_id}/pause', (_req, res, params) => {
  const run = store.runs.get(params.run_id)
  if (!run) {
    sendError(res, 404, 'RUN_NOT_FOUND', 'Run not found')
    return
  }
  run.status = 'paused'
  run.updated_at = now()
  sendJson(res, 200, run)
})

route('POST', '/api/v1/runs/{run_id}/resume', (_req, res, params) => {
  const run = store.runs.get(params.run_id)
  if (!run) {
    sendError(res, 404, 'RUN_NOT_FOUND', 'Run not found')
    return
  }
  run.status = 'running'
  run.updated_at = now()
  sendJson(res, 200, run)
})

route('POST', '/api/v1/runs/{run_id}/cancel', (_req, res, params) => {
  const run = store.runs.get(params.run_id)
  if (!run) {
    sendError(res, 404, 'RUN_NOT_FOUND', 'Run not found')
    return
  }
  run.status = 'cancelled'
  run.updated_at = now()
  run.completed_at = now()
  sendJson(res, 200, run)
})

// 确认计划
route('POST', '/api/v1/runs/{run_id}/sub-questions/plan', (_req, res, params) => {
  const run = store.runs.get(params.run_id)
  if (!run) {
    sendError(res, 404, 'RUN_NOT_FOUND', 'Run not found')
    return
  }
  sendJson(res, 200, { ok: true })
})

// 分歧列表
route('GET', '/api/v1/runs/{run_id}/conflicts', (_req, res, params) => {
  const conflicts = Array.from(store.conflicts.values())
    .filter((c) => c.run_id === params.run_id)
    .map((c) => ({
      id: c.id,
      run_id: c.run_id,
      claim_a: c.claim_a,
      claim_b: c.claim_b,
      severity: c.severity,
      status: c.status,
      verdict: c.verdict,
      created_at: c.created_at
    }))
  sendJson(res, 200, {
    items: conflicts,
    total: conflicts.length,
    page: 1,
    page_size: 50,
    has_more: false
  })
})

// 裁决分歧
route('POST', '/api/v1/conflicts/{conflict_id}/verdict', async (req, res, params) => {
  const conflict = store.conflicts.get(params.conflict_id)
  if (!conflict) {
    sendError(res, 404, 'CONFLICT_NOT_FOUND', 'Conflict not found')
    return
  }
  const body = await parseBody(req)
  conflict.status = 'resolved'
  conflict.verdict = body.verdict ?? 'a'
  sendJson(res, 200, conflict)
})

// 获取报告
route('GET', '/api/v1/runs/{run_id}/report', (_req, res, params) => {
  const report = store.reports.get(`report-${params.run_id}`)
  if (!report) {
    sendError(res, 404, 'REPORT_NOT_FOUND', 'Report not found')
    return
  }
  sendJson(res, 200, report)
})

// 模板列表
route('GET', '/api/v1/templates', (_req, res) => {
  const items = Array.from(store.templates.values()).map((t) => ({
    id: t.id,
    name: t.name,
    description: t.description,
    created_at: t.created_at
  }))
  sendJson(res, 200, {
    items,
    total: items.length,
    page: 1,
    page_size: 50,
    has_more: false
  })
})

// 模板详情
route('GET', '/api/v1/templates/{template_id}', (_req, res, params) => {
  const template = store.templates.get(params.template_id)
  if (!template) {
    sendError(res, 404, 'TEMPLATE_NOT_FOUND', 'Template not found')
    return
  }
  sendJson(res, 200, template)
})

// 埋点上报
route('POST', '/api/v1/telemetry/batch', async (req, res) => {
  // 仅记录，不处理
  await parseBody(req)
  sendJson(res, 200, { ok: true })
})

// 当前团队
route('GET', '/api/v1/teams/current', (_req, res) => {
  sendJson(res, 200, {
    id: 'team-mock-001',
    name: '演示团队',
    member_count: 3
  })
})

// ─── 内部辅助 ───

// 根据事件更新 run 状态
function updateRunFromEvent(runId: string, event: any): void {
  const run = store.runs.get(runId)
  if (!run) return

  if (event.type === 'stage.started' || event.type === 'stage.succeeded' || event.type === 'stage.failed') {
    const stageName = event.payload?.stage
    const stage = run.stages.find((s) => s.name === stageName)
    if (stage) {
      stage.status = event.payload.status
      if (event.type === 'stage.started') {
        stage.started_at = event.ts
        stage.attempt = (stage.attempt ?? 0) + 1
      } else {
        stage.completed_at = event.ts
      }
    }
  }

  if (event.type === 'token.usage') {
    run.token_used = event.payload?.used ?? run.token_used
    run.cost_used = Math.round(run.token_used * 0.001)
  }

  if (event.type === 'cost.warning') {
    // 成本预警：记录最新预警级别与阈值时刻
    run.cost_used = Math.round((event.payload?.used ?? 0) * 0.001)
    run.last_cost_warning = {
      level: event.payload?.level,
      used: event.payload?.used,
      budget: event.payload?.budget,
      ratio: event.payload?.ratio,
      at: event.ts
    }
  }

  if (event.type === 'interrupt.requested') {
    // 澄清挂起：将 run 标记为 awaiting_clarification，并保留问题快照
    run.pending_interrupt = {
      type: 'clarification',
      reason: event.payload?.reason,
      questions: event.payload?.questions ?? [],
      expires_in_seconds: event.payload?.expires_in_seconds,
      at: event.ts
    }
  }

  if (event.type === 'conflict.detected') {
    // 冲突检测：写入冲突快照（与 store.conflicts 双向同步）
    const conflictId = event.payload?.conflict_id
    if (conflictId && !store.conflicts.has(conflictId)) {
      store.conflicts.set(conflictId, {
        id: conflictId,
        run_id: runId,
        claim_a: event.payload?.claim_a ?? '',
        claim_b: event.payload?.claim_b ?? '',
        severity: event.payload?.severity ?? 'medium',
        status: 'pending',
        created_at: event.ts
      })
    }
  }

  if (event.type === 'run.finished') {
    run.status = event.payload?.status ?? 'completed'
    run.completed_at = event.ts
    run.updated_at = event.ts
  }

  // 报告块流：累积 blocks（按 block_id 聚合，同 block 内累加 delta）
  if (event.type === 'report.chunk') {
    const reportId = `report-${runId}`
    let report = store.reports.get(reportId)
    if (!report) {
      report = {
        id: reportId,
        run_id: runId,
        title: '调研报告',
        blocks: [],
        created_at: event.ts
      }
      store.reports.set(reportId, report)
    }
    const blockId = event.payload?.block_id
    const delta = event.payload?.delta ?? ''
    let block = report.blocks.find((b) => b.id === blockId)
    if (!block) {
      block = {
        id: blockId,
        type: 'paragraph',
        content: '',
        order: report.blocks.length
      }
      report.blocks.push(block)
    }
    block.content += delta
    if (event.payload?.done) {
      // 块结束：根据已累积 content 推断类型（首字符 ## 视为 heading，末尾带 "建议" 视为 conclusion）
      if (block.content.startsWith('##')) {
        block.type = 'heading'
      } else if (/建议|结论/.test(block.content)) {
        block.type = 'conclusion'
      }
    }
    run.report_id = reportId
  }

  // 报告完成：标记 report 完结时间
  if (event.type === 'report.finished') {
    const reportId = event.payload?.report_id ?? `report-${runId}`
    const report = store.reports.get(reportId)
    if (report) {
      // 触发 MockReport 类型兼容的 updated_at 字段（M0 暂存于 created_at 上）
    }
    run.report_id = reportId
  }

  run.updated_at = event.ts
}
