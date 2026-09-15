// 剧本节点 DSL：顺序、发事件、等待、安全挂起点（gate）四种节点
// gate 用于 M2 演示：剧本执行到阶段边界时检查控制态，软暂停请求在此挂起、取消在此中止

import type { RawEvent } from './envelope'

// 脚本节点联合类型
export type ScriptNode = SequenceNode | EmitNode | WaitNode | GateNode

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

// 安全挂起点：runner 调用 ctx.awaitGate(gateId)，由执行引擎决定立即通过、挂起或中止
export interface GateNode {
  type: 'gate'
  gateId: string
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

// 阶段边界安全点：暂停在阶段切换处生效，取消在此快速中止
export function gate(gateId = 'stage_boundary'): GateNode {
  return { type: 'gate', gateId }
}
