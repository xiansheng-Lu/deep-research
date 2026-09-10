// Mock 网关 Vite 插件入口（[前端M0收尾方案 §4.3]）
// 以 Vite dev 插件形式内嵌，拦截 /api、/ws、/sse 路由

import type { Plugin, ViteDevServer } from 'vite'
import type { IncomingMessage } from 'node:http'
import type { WebSocket as WsWebSocket, WebSocketServer as WsWebSocketServer } from 'ws'
import { handleRequest } from './router'
import { prebufferBodySync } from './router'
import { handleWsUpgrade } from './realtime'
import { createSseConnection } from './sse'
import { seedInitialData } from './seed'

export function mockGatewayPlugin(): Plugin {
  let initialized = false

  return {
    name: 'mock-gateway',

    configureServer(server: ViteDevServer) {
      // 初始化种子数据（仅一次）
      if (!initialized) {
        seedInitialData()
        initialized = true
        console.log('[mock-gateway] 种子数据已加载')
      }

      // 强制插到 connect 栈最前面（unshift 私有 API）
      // 关键：handle 函数体内第一行同步启动 body 监听，不使用 async
      const middlewares: any = server.middlewares
      const mockEntry = {
        route: '',
        handle(req: any, res: any, next: () => void): void {
          const url = new URL(req.url ?? '/', 'http://localhost')
          const pathname = url.pathname

          // SSE 端点
          const sseMatch = pathname.match(/^\/api\/v1\/runs\/([^/]+)\/report\/stream$/)
          if (sseMatch) {
            const runId = sseMatch[1]
            createSseConnection(req, res, runId)
            return
          }

          // REST 端点：同步启动 body 预读抢占 data 监听
          if (pathname.startsWith('/api/v1/')) {
            const method = req.method ?? 'GET'
            const bodyPromise = (method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE')
              ? prebufferBodySync(req)
              : Promise.resolve(undefined)

            bodyPromise.then(async () => {
              // body 已设到 req._mockBody
              const handled = await handleRequest(req, res)
              if (!handled) {
                next()
              }
            }).catch((err) => {
              console.error('[mock-gateway] 处理失败:', err)
              try { res.writeHead(500); res.end() } catch {}
            })
            return
          }

          next()
        }
      }
      if (Array.isArray(middlewares.stack)) {
        middlewares.stack.unshift(mockEntry)
      } else {
        // 退化
        server.middlewares.use((req: any, res: any, next: any) => mockEntry.handle(req, res, next))
      }

      // 获取底层 HTTP 服务器处理 WebSocket
      const httpServer = server.httpServer
      if (!httpServer) return

      // 拦截 WebSocket 升级请求
      httpServer.on('upgrade', (req: IncomingMessage, socket: any, head: Buffer) => {
        const url = new URL(req.url ?? '/', 'http://localhost')
        const pathname = url.pathname

        // WS 端点：/api/v1/ws/runs/{run_id}/stream
        const wsMatch = pathname.match(/^\/api\/v1\/ws\/runs\/([^/]+)\/stream$/)
        if (wsMatch) {
          const runId = wsMatch[1]

          // 动态加载 ws 模块
          import('ws').then(({ WebSocketServer }) => {
            const wss = new WebSocketServer({ noServer: true }) as WsWebSocketServer
            wss.handleUpgrade(req, socket, head, (ws: WsWebSocket) => {
              handleWsUpgrade(ws, req, runId)
              wss.emit('connection', ws, req)
            })
          }).catch((err) => {
            console.error('[mock-gateway] WS 升级失败:', err)
            socket.destroy()
          })
        }
      })
    }
  }
}
