// 全局助手（闲聊）API（M2-1：POST /assistant/chat，SSE 流式，backend/app/api/v1/assistant.py）
// 不走六阶段流水线、不检索、不产生项目数据；服务端无会话存储，history 由客户端携带。
import { httpStream } from '../http/http'
import type { ApiError } from '../http/error'
import type { AssistantChatRequest, AssistantStreamError } from './types'

export interface StreamChatHandlers {
  // 每个文本增量片段
  onDelta: (delta: string) => void
  // 服务端在流内发回的错误帧（回答中断）；收到后流自然结束
  onError?: (error: AssistantStreamError) => void
  // 外部中断（用户停止生成）
  signal?: AbortSignal
}

// SSE 帧协议（与后端 _stream_answer 一一对齐）：
//   data: {"delta": "片段"}\n\n      增量
//   data: [DONE]\n\n                 正常结束
//   event: error\ndata: {code,message}\n\n  异常
export async function streamAssistantChat(
  body: AssistantChatRequest,
  handlers: StreamChatHandlers
): Promise<void> {
  // 建连阶段失败（含 401/503/网络错误）直接抛给 UI 层展示失败态与重发
  const response = await httpStream('/assistant/chat', {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal: handlers.signal
  })

  if (!response.body) {
    throw new Error('闲聊响应缺少流式响应体')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let pendingEvent = ''

  const handleFrame = (rawFrame: string): boolean => {
    let event = pendingEvent
    let data = ''
    for (const line of rawFrame.split('\n')) {
      const trimmed = line.replace(/\r$/, '')
      if (trimmed.startsWith('event:')) {
        event = trimmed.slice(6).trim()
      } else if (trimmed.startsWith('data:')) {
        data += trimmed.slice(5).trim()
      }
    }
    pendingEvent = ''
    if (!data) return true

    if (data === '[DONE]') return false
    const parsed = JSON.parse(data) as { delta?: string } | AssistantStreamError
    if (event === 'error' || ('code' in parsed && 'message' in parsed)) {
      handlers.onError?.(parsed as AssistantStreamError)
      return false
    }
    if ('delta' in parsed && typeof parsed.delta === 'string') {
      handlers.onDelta(parsed.delta)
    }
    return true
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE 帧以空行分隔
    let sepIndex: number
    while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)
      if (!handleFrame(frame)) {
        // [DONE] / error：服务端随后主动关闭流，不做客户端 cancel（避免 ERR_ABORTED 日志）
        return
      }
    }
  }
  // 收尾：处理残留在 buffer 中的最后一帧
  if (buffer.trim()) handleFrame(buffer)
}

// 便于 UI 层把错误帧与建连错误统一映射
export function isApiErrorLike(err: unknown): err is ApiError {
  return typeof err === 'object' && err !== null && 'status' in err && 'code' in err
}
