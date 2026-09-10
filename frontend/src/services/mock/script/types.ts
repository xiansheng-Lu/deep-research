// 指令脚本节点类型定义（[前端M0收尾方案 §4.3]）
// TypeScript DSL：节点联合类型 + Builder 辅助函数

import type { RealtimeEnvelope } from '../../realtime/types'

// 脚本节点联合类型
export type ScriptNode =
  | SequenceNode
  | EmitNode
  | ParallelNode
  | WaitNode
  | InterruptNode
  | CompleteNode
  | FailNode

// 顺序执行
export interface SequenceNode {
  type: 'sequence'
  nodes: ScriptNode[]
}

// 发送事件
export interface EmitNode {
  type: 'emit'
  event: RealtimeEnvelope
  delayMs?: number // 发送前延迟
}

// 并行执行
export interface ParallelNode {
  type: 'parallel'
  nodes: ScriptNode[]
}

// 等待
export interface WaitNode {
  type: 'wait'
  durationMs: number
}

// 中断（澄清/冲突）
export interface InterruptNode {
  type: 'interrupt'
  interruptType: 'clarification' | 'conflict'
  // 等待用户响应后继续
}

// 完成
export interface CompleteNode {
  type: 'complete'
  status: 'succeeded' | 'failed' | 'cancelled'
}

// 故障注入
export interface FailNode {
  type: 'fail'
  errorCode: string
  errorMessage: string
}

// Builder 辅助函数
export function sequence(...nodes: ScriptNode[]): SequenceNode {
  return { type: 'sequence', nodes }
}

export function emit(event: RealtimeEnvelope, delayMs?: number): EmitNode {
  return { type: 'emit', event, delayMs }
}

export function parallel(...nodes: ScriptNode[]): ParallelNode {
  return { type: 'parallel', nodes }
}

export function wait(durationMs: number): WaitNode {
  return { type: 'wait', durationMs }
}

export function interrupt(interruptType: 'clarification' | 'conflict'): InterruptNode {
  return { type: 'interrupt', interruptType }
}

export function complete(status: 'succeeded' | 'failed' | 'cancelled' = 'succeeded'): CompleteNode {
  return { type: 'complete', status }
}

export function fail(errorCode: string, errorMessage: string): FailNode {
  return { type: 'fail', errorCode, errorMessage }
}
