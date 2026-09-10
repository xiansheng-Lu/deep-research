// 全局 Toast 服务（[前端详细设计 §6.1 M0 反馈]）
// 单例模式：模块作用域 reactive 数组持有队列
// 调用方：toast.info(msg, options?) / toast.success / toast.warn / toast.danger / toast.dismiss(id)
// 渲染方：UiToastHost 通过订阅队列完成实际展示
import { reactive } from 'vue'

// 视觉类型（与 UiBadge / UiTag 一致）
export type ToastType = 'info' | 'success' | 'warn' | 'danger'

// 单条 toast 入参选项
export interface ToastOptions {
  // 自动关闭毫秒数；0 表示常驻，需手动关闭
  duration?: number
  // 是否显示关闭按钮
  closable?: boolean
  // 副标题/详情
  description?: string
}

interface ToastItem extends Required<Pick<ToastOptions, 'duration' | 'closable'>> {
  id: string
  type: ToastType
  message: string
  description?: string
}

// 队列（reactive 让宿主自动响应变化）
const queue = reactive<ToastItem[]>([])

// 默认持续时间：4s；danger 错误略长 6s
function defaultDuration(type: ToastType): number {
  return type === 'danger' ? 6000 : 4000
}

// 生成稳定的 toast id
function genId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

// 核心 push：返回 id 供手动关闭
function push(type: ToastType, message: string, options: ToastOptions = {}): string {
  const item: ToastItem = {
    id: genId(),
    type,
    message,
    description: options.description,
    duration: options.duration ?? defaultDuration(type),
    closable: options.closable ?? true
  }
  queue.push(item)
  return item.id
}

// 移除指定 id
function dismiss(id: string): void {
  const idx = queue.findIndex((it) => it.id === id)
  if (idx >= 0) queue.splice(idx, 1)
}

// 清空队列（一般用于路由切换/单元测试）
function clear(): void {
  queue.splice(0, queue.length)
}

// 暴露给宿主使用的只读快照
function snapshot(): readonly ToastItem[] {
  return queue
}

export const toast = {
  info: (msg: string, opts?: ToastOptions) => push('info', msg, opts),
  success: (msg: string, opts?: ToastOptions) => push('success', msg, opts),
  warn: (msg: string, opts?: ToastOptions) => push('warn', msg, opts),
  danger: (msg: string, opts?: ToastOptions) => push('danger', msg, opts),
  dismiss,
  clear,
  snapshot
}

export type { ToastItem }
