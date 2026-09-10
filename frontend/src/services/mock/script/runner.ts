// 脚本节点调度器（[前端M0收尾方案 §4.3]）
// 解释执行 ScriptNode 树，驱动事件发送

import type { ScriptNode } from './types'
import type { RealtimeEnvelope } from '@/services/realtime/types'

export type EventSender = (event: RealtimeEnvelope) => void
export type InterruptHandler = (interruptType: 'clarification' | 'conflict') => Promise<void>

export interface RunnerContext {
  sendEvent: EventSender
  onInterrupt?: InterruptHandler
  abortSignal?: AbortSignal
}

// 执行脚本节点
export async function executeNode(node: ScriptNode, ctx: RunnerContext): Promise<void> {
  // 检查是否已中止
  if (ctx.abortSignal?.aborted) {
    throw new Error('Script aborted')
  }

  switch (node.type) {
    case 'sequence':
      for (const child of node.nodes) {
        await executeNode(child, ctx)
      }
      break

    case 'emit':
      if (node.delayMs && node.delayMs > 0) {
        await sleep(node.delayMs)
      }
      ctx.sendEvent(node.event)
      break

    case 'parallel':
      await Promise.all(node.nodes.map((child) => executeNode(child, ctx)))
      break

    case 'wait':
      await sleep(node.durationMs)
      break

    case 'interrupt':
      if (ctx.onInterrupt) {
        await ctx.onInterrupt(node.interruptType)
      }
      // 等待用户响应（模拟：延迟 2s 后继续）
      await sleep(2000)
      break

    case 'complete':
      // 完成节点，不执行额外操作
      break

    case 'fail':
      throw new Error(`[${node.errorCode}] ${node.errorMessage}`)
  }
}

// 辅助：睡眠
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
