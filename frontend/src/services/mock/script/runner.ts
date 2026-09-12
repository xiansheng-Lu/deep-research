// 剧本节点调度器：解释执行 ScriptNode 树，驱动事件发送

import type { ScriptNode } from './types'
import type { RealtimeEnvelope } from '@/services/realtime/types'
import { wrapEnvelope } from './envelope'

export type EventSender = (event: RealtimeEnvelope) => void

export interface RunnerContext {
  runId: string
  sendEvent: EventSender
  // gate 节点回调：由执行引擎注入，决定挂起/中止/放行；缺省时 gate 直接通过
  awaitGate?: (gateId: string) => Promise<void>
}

// 执行脚本节点
export async function executeNode(node: ScriptNode, ctx: RunnerContext): Promise<void> {
  switch (node.type) {
    case 'sequence':
      for (const child of node.nodes) {
        await executeNode(child, ctx)
      }
      break

    case 'emit': {
      if (node.delayMs && node.delayMs > 0) {
        await sleep(node.delayMs)
      }
      // 信封在发送瞬间构造，保证 ts/event_id 与真实发布时刻一致
      const raw = typeof node.event === 'function' ? node.event() : node.event
      ctx.sendEvent(wrapEnvelope(ctx.runId, raw))
      break
    }

    case 'wait':
      await sleep(node.durationMs)
      break

    case 'gate':
      await ctx.awaitGate?.(node.gateId)
      break
  }
}

// 辅助：睡眠
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
