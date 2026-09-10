// Mock 网关 Vite 插件入口
// development + VITE_MOCK=gateway 时内嵌，拦截 /api/v1、/healthz、/api/v1/ws 路由

import type { Plugin, ViteDevServer } from 'vite'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Socket } from 'node:net'
import type { WebSocket as WsWebSocket, WebSocketServer as WsWebSocketServer } from 'ws'
import { handleRequest, prebufferBodySync } from './router'
import { handleWsUpgrade, isAccessTokenValid } from './realtime'
import { seedInitialData } from './seed'

// connect 中间件栈最小结构（unshift 到栈顶优先拦截）
interface ConnectStack {
  stack: Array<{ route: string; handle: (req: IncomingMessage, res: ServerResponse, next: () => void) => void }>
}

const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export function mockGatewayPlugin(): Plugin {
  let initialized = false

  return {
    name: 'mock-gateway',

    configureServer(server: ViteDevServer) {
      if (!initialized) {
        seedInitialData()
        initialized = true
      }

      const middlewares = server.middlewares as unknown as ConnectStack
      const entry = {
        route: '',
        handle(req: IncomingMessage, res: ServerResponse, next: () => void): void {
          const url = new URL(req.url ?? '/', 'http://localhost')
          const pathname = url.pathname
          const method = req.method ?? 'GET'

          // REST 与健康检查：写方法同步启动 body 预读抢占 data 监听
          if (pathname.startsWith('/api/v1/') || pathname === '/healthz') {
            const bodyPromise = WRITE_METHODS.has(method)
              ? prebufferBodySync(req)
              : Promise.resolve(undefined)

            bodyPromise.then(async () => {
              const handled = await handleRequest(req, res)
              if (!handled) next()
            }).catch((err: unknown) => {
              console.error('[mock-gateway] 请求处理失败:', err)
              if (!res.headersSent) {
                res.writeHead(500, { 'Content-Type': 'application/json' })
                res.end(JSON.stringify({ code: 'internal_error', message: 'mock gateway error' }))
              }
            })
            return
          }

          next()
        }
      }
      middlewares.stack.unshift(entry)

      // WebSocket 升级拦截
      const httpServer = server.httpServer
      if (!httpServer) return

      httpServer.on('upgrade', (req: IncomingMessage, socket: Socket, head: Buffer) => {
        const url = new URL(req.url ?? '/', 'http://localhost')
        const wsMatch = url.pathname.match(/^\/api\/v1\/ws\/runs\/([^/]+)\/stream$/)
        if (!wsMatch) return
        const runId = wsMatch[1]

        // 对齐后端：accept 前鉴权，token 缺失/无效直接以 HTTP 403 拒绝握手
        if (!isAccessTokenValid(url.searchParams.get('token'))) {
          socket.write('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
          socket.destroy()
          return
        }

        import('ws').then(({ WebSocketServer }) => {
          const wss = new WebSocketServer({ noServer: true }) as WsWebSocketServer
          wss.handleUpgrade(req, socket, head, (ws: WsWebSocket) => {
            handleWsUpgrade(ws, runId)
            wss.emit('connection', ws, req)
          })
        }).catch((err: unknown) => {
          console.error('[mock-gateway] WS 升级失败:', err)
        })
      })
    }
  }
}
