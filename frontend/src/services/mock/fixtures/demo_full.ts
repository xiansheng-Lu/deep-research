// demo_full 剧本（[前端M0收尾方案 §3.3]）
// 完整研究流程：阶段推进 + 澄清挂起/恢复 + 冲突裁决 + 成本预警 + 报告流 + run 完成，约 60s

import { sequence, emit, wait, interrupt } from '../script/types'
import type { ScriptNode } from '../script/types'
import { createEnvelope } from '../script/envelope'
import type {
  StageEvent,
  TokenUsageEvent,
  CostWarningEvent,
  InterruptRequestEvent,
  ConflictDetectedEvent,
  ReportChunkEvent,
  ReportFinishedEvent,
  RunFinishedEvent
} from '../../realtime/types'

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
    content: '本报告围绕"大语言模型在企业知识管理中的应用"展开，综合调研 2024-2025 年间的主要实践、工具与落地挑战。'
  },
  {
    id: 'block-3',
    type: 'heading' as const,
    content: '## 主要发现'
  },
  {
    id: 'block-4',
    type: 'paragraph' as const,
    content: '1. 企业级 LLM 部署从 PoC 走向规模化，私有化与 API 混合架构成为主流。'
  },
  {
    id: 'block-5',
    type: 'paragraph' as const,
    content: '2. 检索增强生成（RAG）与知识图谱结合，显著降低幻觉并提升事实一致性。'
  },
  {
    id: 'block-6',
    type: 'heading' as const,
    content: '## 风险与冲突'
  },
  {
    id: 'block-7',
    type: 'paragraph' as const,
    content: '不同来源在"小模型本地化"问题上存在分歧：部分研究强调端侧推理的成本优势，另一部分则指出其上下文长度与推理能力的局限。'
  },
  {
    id: 'block-8',
    type: 'heading' as const,
    content: '## 结论与建议'
  },
  {
    id: 'block-9',
    type: 'conclusion' as const,
    content: '建议优先在内部知识库检索与客服辅助场景落地，结合人工审核与持续评估机制，渐进扩展至更复杂的业务流。'
  }
]

export function buildDemoFullScript(runId: string): ScriptNode {
  const nodes: ScriptNode[] = []

  // ─── 阶段 1: intent_analysis ───
  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.started', {
        stage: 'intent_analysis',
        status: 'started'
      }, { runId, stage: 'intent_analysis' })
    )
  )
  nodes.push(wait(1500))

  // 澄清请求（interrupt 节点：发 interrupt.requested，runner 会调用 onInterrupt 并 sleep 2s 模拟用户响应）
  nodes.push(
    emit(
      createEnvelope<InterruptRequestEvent>('interrupt.requested', {
        reason: '研究目标存在歧义，需要用户明确范围',
        questions: [
          {
            id: 'q-scope',
            text: '请选择本次调研的重点方向（可多选）',
            type: 'multi',
            options: [
              { key: 'tech', label: '技术架构与模型选型', recommended: true },
              { key: 'product', label: '产品形态与用户体验' },
              { key: 'business', label: '商业模式与 ROI' },
              { key: 'risk', label: '合规、安全与伦理风险' }
            ]
          },
          {
            id: 'q-depth',
            text: '调研深度',
            type: 'single',
            options: [
              { key: 'overview', label: '概览（30 分钟阅读）' },
              { key: 'standard', label: '标准（60 分钟阅读）', recommended: true },
              { key: 'deep', label: '深度（120 分钟阅读）' }
            ]
          }
        ],
        expires_in_seconds: 300
      }, { runId, stage: 'intent_analysis' })
    )
  )
  nodes.push(interrupt('clarification'))

  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.succeeded', {
        stage: 'intent_analysis',
        status: 'succeeded'
      }, { runId, stage: 'intent_analysis' })
    )
  )

  // ─── 阶段 2: evidence_collection ───
  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.started', {
        stage: 'evidence_collection',
        status: 'started'
      }, { runId, stage: 'evidence_collection' })
    )
  )
  nodes.push(
    emit(
      createEnvelope<TokenUsageEvent>('token.usage', {
        used: 12000,
        budget: 100000,
        ratio: 0.12
      }, { runId, stage: 'evidence_collection' })
    )
  )
  nodes.push(wait(2500))
  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.succeeded', {
        stage: 'evidence_collection',
        status: 'succeeded'
      }, { runId, stage: 'evidence_collection' })
    )
  )

  // ─── 阶段 3: evidence_evaluation ───
  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.started', {
        stage: 'evidence_evaluation',
        status: 'started'
      }, { runId, stage: 'evidence_evaluation' })
    )
  )
  nodes.push(
    emit(
      createEnvelope<TokenUsageEvent>('token.usage', {
        used: 38000,
        budget: 100000,
        ratio: 0.38
      }, { runId, stage: 'evidence_evaluation' })
    )
  )

  // 冲突检测
  nodes.push(
    emit(
      createEnvelope<ConflictDetectedEvent>('conflict.detected', {
        conflict_id: `conflict-${runId}-1`,
        claim_a: '小型模型（7B-13B）在端侧部署后已能满足 80% 企业知识检索需求',
        claim_b: '端侧小模型受上下文窗口与推理能力限制，仍需云端大模型补足复杂任务',
        severity: 'high'
      }, { runId, stage: 'evidence_evaluation' })
    )
  )
  nodes.push(interrupt('conflict'))

  nodes.push(wait(1500))
  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.succeeded', {
        stage: 'evidence_evaluation',
        status: 'succeeded'
      }, { runId, stage: 'evidence_evaluation' })
    )
  )

  // ─── 阶段 4: synthesis ───
  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.started', {
        stage: 'synthesis',
        status: 'started'
      }, { runId, stage: 'synthesis' })
    )
  )
  nodes.push(
    emit(
      createEnvelope<TokenUsageEvent>('token.usage', {
        used: 71000,
        budget: 100000,
        ratio: 0.71
      }, { runId, stage: 'synthesis' })
    )
  )
  nodes.push(wait(2000))

  // 成本预警（70% 阈值）
  nodes.push(
    emit(
      createEnvelope<CostWarningEvent>('cost.warning', {
        level: '70',
        used: 71000,
        budget: 100000,
        ratio: 0.71
      }, { runId, stage: 'synthesis' })
    )
  )
  nodes.push(wait(1500))
  nodes.push(
    emit(
      createEnvelope<TokenUsageEvent>('token.usage', {
        used: 91000,
        budget: 100000,
        ratio: 0.91
      }, { runId, stage: 'synthesis' })
    )
  )

  // 成本预警（90% 阈值）
  nodes.push(
    emit(
      createEnvelope<CostWarningEvent>('cost.warning', {
        level: '90',
        used: 91000,
        budget: 100000,
        ratio: 0.91
      }, { runId, stage: 'synthesis' })
    )
  )
  nodes.push(wait(1500))

  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.succeeded', {
        stage: 'synthesis',
        status: 'succeeded'
      }, { runId, stage: 'synthesis' })
    )
  )

  // ─── 阶段 5: report_drafting ───
  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.started', {
        stage: 'report_drafting',
        status: 'started'
      }, { runId, stage: 'report_drafting' })
    )
  )

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

  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.succeeded', {
        stage: 'report_drafting',
        status: 'succeeded'
      }, { runId, stage: 'report_drafting' })
    )
  )

  // ─── 阶段 6: report_review ───
  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.started', {
        stage: 'report_review',
        status: 'started'
      }, { runId, stage: 'report_review' })
    )
  )
  nodes.push(wait(1500))

  // 报告完成
  nodes.push(
    emit(
      createEnvelope<ReportFinishedEvent>('report.finished', {
        report_id: `report-${runId}`,
        total_blocks: REPORT_BLOCKS.length
      }, { runId })
    )
  )

  nodes.push(
    emit(
      createEnvelope<StageEvent>('stage.succeeded', {
        stage: 'report_review',
        status: 'succeeded'
      }, { runId, stage: 'report_review' })
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
