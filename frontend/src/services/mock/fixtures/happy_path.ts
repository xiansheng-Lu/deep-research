// happy_path 剧本（[前端M0收尾方案 §3.3]）
// 6 阶段顺序推进 -> 报告流 -> run 完成，约 25s

import { sequence, emit, wait } from '../script/types'
import type { ScriptNode } from '../script/types'
import { createEnvelope } from '../script/envelope'
import type {
  StageEvent,
  TokenUsageEvent,
  ReportChunkEvent,
  ReportFinishedEvent,
  RunFinishedEvent
} from '../../realtime/types'

// 研究阶段定义
const STAGES = [
  'intent_analysis',
  'evidence_collection',
  'evidence_evaluation',
  'synthesis',
  'report_drafting',
  'report_review'
]

// 报告内容（模拟流式输出）
const REPORT_BLOCKS = [
  {
    id: 'block-1',
    type: 'heading' as const,
    content: '## 调研概述'
  },
  {
    id: 'block-2',
    type: 'paragraph' as const,
    content: '本报告基于对 2024-2025 年 AI 领域主要技术趋势的综合调研，涵盖大语言模型、多模态技术、AI Agent 等核心方向。'
  },
  {
    id: 'block-3',
    type: 'heading' as const,
    content: '## 主要发现'
  },
  {
    id: 'block-4',
    type: 'paragraph' as const,
    content: '1. 大语言模型持续向更大规模、更强推理能力演进，同时小型化模型在端侧部署方面取得突破。'
  },
  {
    id: 'block-5',
    type: 'paragraph' as const,
    content: '2. 多模态融合成为主流趋势，视觉-语言-代码的统一架构逐步成熟。'
  },
  {
    id: 'block-6',
    type: 'heading' as const,
    content: '## 结论与建议'
  },
  {
    id: 'block-7',
    type: 'conclusion' as const,
    content: '建议重点关注 Agent 架构设计与垂直领域微调技术，同时关注模型安全与对齐问题。'
  }
]

export function buildHappyPathScript(runId: string): ScriptNode {
  const nodes: ScriptNode[] = []

  // 阶段推进
  for (const stage of STAGES) {
    // 阶段开始
    nodes.push(
      emit(
        createEnvelope<StageEvent>('stage.started', {
          stage,
          status: 'started'
        }, { runId, stage })
      )
    )

    // 阶段执行中：发送 token 使用事件
    nodes.push(
      emit(
        createEnvelope<TokenUsageEvent>('token.usage', {
          used: Math.floor(Math.random() * 5000) + 1000,
          budget: 100000,
          ratio: Math.random() * 0.3
        }, { runId, stage })
      )
    )

    // 等待模拟处理时间
    nodes.push(wait(2000 + Math.random() * 1500))

    // 阶段完成
    nodes.push(
      emit(
        createEnvelope<StageEvent>('stage.succeeded', {
          stage,
          status: 'succeeded'
        }, { runId, stage })
      )
    )
  }

  // 报告流式输出
  for (const block of REPORT_BLOCKS) {
    const chunks = splitIntoChunks(block.content, 20)
    for (const chunk of chunks) {
      nodes.push(
        emit(
          createEnvelope<ReportChunkEvent>('report.chunk', {
            block_id: block.id,
            delta: chunk,
            done: false
          }, { runId })
        )
      )
      nodes.push(wait(100 + Math.random() * 150))
    }
    // 块完成
    nodes.push(
      emit(
        createEnvelope<ReportChunkEvent>('report.chunk', {
          block_id: block.id,
          delta: '',
          done: true
        }, { runId })
      )
    )
    nodes.push(wait(200))
  }

  // 报告完成
  nodes.push(
    emit(
      createEnvelope<ReportFinishedEvent>('report.finished', {
        report_id: `report-${runId}`,
        total_blocks: REPORT_BLOCKS.length
      }, { runId })
    )
  )

  // 运行完成
  nodes.push(
    emit(
      createEnvelope<RunFinishedEvent>('run.finished', {
        run_id: runId,
        status: 'succeeded'
      }, { runId })
    )
  )

  return sequence(...nodes)
}

// 辅助：将文本切分为小块（模拟流式输出）
function splitIntoChunks(text: string, chunkSize: number): string[] {
  const chunks: string[] = []
  for (let i = 0; i < text.length; i += chunkSize) {
    chunks.push(text.slice(i, i + chunkSize))
  }
  return chunks
}
