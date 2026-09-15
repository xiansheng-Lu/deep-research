// 全局助手闲聊编排（[前端详细设计 §12.6 M2]，对齐 M2-1 交接单 §3）
// POST /assistant/chat SSE 流式；M2-1 无服务端会话存储，历史由客户端 localStorage 持有，
// 每次请求携带最近 20 条；刷新页面不丢当前会话（交接单 §3.3）。
import { computed, ref } from 'vue'
import { streamAssistantChat } from '@/services/api/assistant'
import type { ApiError } from '@/services/http/error'
import type { AssistantRole, AssistantTurn } from '@/services/api/types'

const MAX_MESSAGE_CHARS = 2000
const HISTORY_SEND_LIMIT = 20
// 本地最多保留 100 条消息（约 50 轮），超出裁掉最旧
const STORE_LIMIT = 100

export type ChatMessageStatus = 'streaming' | 'done' | 'error'

export interface ChatMessage {
  id: string
  role: AssistantRole
  content: string
  status: ChatMessageStatus
  // 失败原因（error 态展示）
  errorText?: string
}

function storageKey(userId: string): string {
  return `m2.assistant.chat.v1.${userId}`
}

function createId(): string {
  return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

export function useChat(userId: string) {
  const messages = ref<ChatMessage[]>(loadHistory(userId))
  const sending = ref(false)
  let abortController: AbortController | null = null

  const hasMessages = computed(() => messages.value.length > 0)

  function persist(): void {
    try {
      // 仅持久化已完成的用户/助手文本，流式中/失败态也保留最后内容便于刷新后查看
      const plain = messages.value.slice(-STORE_LIMIT).map((m) => ({
        role: m.role,
        content: m.content
      }))
      localStorage.setItem(storageKey(userId), JSON.stringify(plain))
    } catch {
      // 存储不可用（隐私模式/超额）时仅保留内存态，不影响对话
    }
  }

  function loadHistory(uid: string): ChatMessage[] {
    try {
      const raw = localStorage.getItem(storageKey(uid))
      if (!raw) return []
      const parsed: unknown = JSON.parse(raw)
      if (!Array.isArray(parsed)) return []
      return parsed
        .filter(
          (m): m is { role: AssistantRole; content: string } =>
            !!m &&
            (m.role === 'user' || m.role === 'assistant') &&
            typeof m.content === 'string'
        )
        .map((m) => ({ id: createId(), role: m.role, content: m.content, status: 'done' }))
    } catch {
      return []
    }
  }

  // 从指定消息集合截取最近已完成的对话作为 history（交接单 §3.1：history 不含当前 message）
  function buildHistoryFrom(list: ChatMessage[]): AssistantTurn[] {
    return list
      .filter((m) => m.status === 'done' && m.content.trim())
      .slice(-HISTORY_SEND_LIMIT)
      .map((m) => ({ role: m.role, content: m.content }))
  }

  function buildHistory(): Array<{ role: AssistantRole; content: string }> {
    return buildHistoryFrom(messages.value)
  }

  function friendlyError(err: unknown): string {
    const apiError = err as ApiError
    if (apiError?.status === 503 || apiError?.code === 'provider_unavailable') {
      return '闲聊服务暂不可用（模型未配置或暂时不可用），请稍后再试'
    }
    if (apiError?.status === 422) {
      return '消息内容不符合要求（1-2000 字），请调整后重试'
    }
    if (apiError?.status === 401) {
      return '登录态已失效，请重新登录'
    }
    return '回答中断，请检查网络后重试'
  }

  // SSE 流内错误帧（交接单 §3.2：event:error 后不再有 [DONE]）的友好文案
  function friendlyStreamError(error: { code: string; message: string }): string {
    if (error.code === 'provider_unavailable') {
      return '闲聊服务暂时不可用，回答已中断，请稍后重试'
    }
    return error.message || '回答中断，请重试'
  }

  // 追加助手气泡并流式接收回答；currentText 为本轮用户消息，history 必须是该轮之前的对话
  async function runTurn(
    currentText: string,
    history: Array<{ role: AssistantRole; content: string }>
  ): Promise<void> {
    const assistantMsg = ref<ChatMessage>({
      id: createId(),
      role: 'assistant',
      content: '',
      status: 'streaming'
    })
    messages.value.push(assistantMsg.value)
    sending.value = true
    abortController = new AbortController()

    try {
      await streamAssistantChat(
        { message: currentText, history },
        {
          signal: abortController.signal,
          onDelta: (delta) => {
            assistantMsg.value = { ...assistantMsg.value, content: assistantMsg.value.content + delta }
            syncMessage(assistantMsg.value)
          },
          onError: (error) => {
            assistantMsg.value = {
              ...assistantMsg.value,
              status: 'error',
              errorText: friendlyStreamError(error)
            }
            syncMessage(assistantMsg.value)
          }
        }
      )
      if (assistantMsg.value.status === 'streaming') {
        assistantMsg.value = { ...assistantMsg.value, status: 'done' }
        syncMessage(assistantMsg.value)
      }
    } catch (err) {
      // 用户主动停止（AbortError）：保留已收到的部分内容，不标记为失败
      if (err instanceof DOMException && err.name === 'AbortError') {
        assistantMsg.value = { ...assistantMsg.value, status: 'done' }
        syncMessage(assistantMsg.value)
      } else {
        assistantMsg.value = {
          ...assistantMsg.value,
          status: 'error',
          errorText: friendlyError(err)
        }
        syncMessage(assistantMsg.value)
      }
    } finally {
      sending.value = false
      abortController = null
      persist()
    }
  }

  // 发送一条用户消息并流式接收助手回答
  async function send(text: string): Promise<void> {
    const content = text.trim()
    if (!content || sending.value) return

    // 必须在压入当前用户消息前截取 history，否则当前消息会与 message 字段重复发送
    const history = buildHistory()
    const userMsg: ChatMessage = { id: createId(), role: 'user', content, status: 'done' }
    messages.value.push(userMsg)
    await runTurn(content, history)
  }

  // 用最新引用替换列表中的同 id 消息（保持数组引用稳定，避免整列重建）
  function syncMessage(next: ChatMessage): void {
    const idx = messages.value.findIndex((m) => m.id === next.id)
    if (idx !== -1) messages.value[idx] = next
  }

  // 停止生成：中断请求，已收到的部分内容保留为完成态
  function stop(): void {
    abortController?.abort()
    abortController = null
    sending.value = false
    const last = messages.value[messages.value.length - 1]
    if (last && last.status === 'streaming') {
      syncMessage({ ...last, status: 'done', errorText: '已手动停止' })
      persist()
    }
  }

  // 重发：移除失败的助手气泡，以原用户消息重新请求（保留原用户气泡，不重复展示）
  async function resend(failedId: string): Promise<void> {
    if (sending.value) return
    const idx = messages.value.findIndex((m) => m.id === failedId)
    const failed = messages.value[idx]
    const userMsg = idx > 0 ? messages.value[idx - 1] : undefined
    if (failed?.status !== 'error' || userMsg?.role !== 'user') return
    // history 为该用户消息之前的已完成对话（不含本轮用户消息）
    const history = buildHistoryFrom(messages.value.slice(0, idx - 1))
    messages.value.splice(idx, 1)
    await runTurn(userMsg.content, history)
  }

  function clear(): void {
    messages.value = []
    try {
      localStorage.removeItem(storageKey(userId))
    } catch {
      // 忽略
    }
  }

  return {
    messages,
    sending,
    hasMessages,
    maxMessageChars: MAX_MESSAGE_CHARS,
    send,
    stop,
    resend,
    clear
  }
}
