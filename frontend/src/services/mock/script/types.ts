// 剧本节点 DSL：M1 仅需顺序、发事件、等待三种节点

import type { RawEvent } from './envelope'

// 脚本节点联合类型
export type ScriptNode = SequenceNode | EmitNode | WaitNode

// 顺序执行
export interface SequenceNode {
  type: 'sequence'
  nodes: ScriptNode[]
}

// 事件工厂：需要在发送瞬间才确定字段（如 occurred_at）时使用
export type RawEventFactory = () => RawEvent

// 发送原始事件（信封由 runner 在实际发送瞬间构造，ts/event_id 与发送时刻对齐）
export interface EmitNode {
  type: 'emit'
  event: RawEvent | RawEventFactory
  delayMs?: number // 发送前延迟
}

// 等待
export interface WaitNode {
  type: 'wait'
  durationMs: number
}

export function sequence(...nodes: ScriptNode[]): SequenceNode {
  return { type: 'sequence', nodes }
}

export function emit(event: RawEvent | RawEventFactory, delayMs?: number): EmitNode {
  return { type: 'emit', event, delayMs }
}

export function wait(durationMs: number): WaitNode {
  return { type: 'wait', durationMs }
}
