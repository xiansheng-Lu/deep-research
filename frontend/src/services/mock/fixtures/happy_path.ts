// happy_path 剧本：对齐后端 M1 实际事件流
// 6 阶段顺序发布 stage.started（clarify -> decompose -> retrieve -> standardize -> critique -> report），
// 随后发布 run.finished；全程约 8~30 秒（随档位变化）

import { sequence, emit, wait } from '../script/types'
import type { ScriptNode } from '../script/types'
import type { MockRun } from '../store'

// M1 六阶段（与后端 orchestrator.state.ResearchStage 一致）
const STAGES = ['clarify', 'decompose', 'retrieve', 'standardize', 'critique', 'report'] as const

// 各档位单阶段基础耗时（毫秒）
const STAGE_DELAY_MS: Record<MockRun['tier'], number> = {
  quick: 1200,
  standard: 2200,
  deep: 3600,
  extreme: 5000
}

// 各档位最终 token 用量占预算的大致比例
const FINAL_TOKEN_RATIO: Record<MockRun['tier'], number> = {
  quick: 0.08,
  standard: 0.12,
  deep: 0.15,
  extreme: 0.18
}

export function buildHappyPathScript(run: MockRun): ScriptNode {
  const nodes: ScriptNode[] = []
  const baseDelay = STAGE_DELAY_MS[run.tier]

  // 前置启动间隔：模拟后端编排器引导耗时，留出 WS 订阅窗口，避免首事件丢失
  nodes.push(wait(400))

  STAGES.forEach((stage, index) => {
    // 阶段开始（后端节点切入时仅发布 stage.started）
    nodes.push(
      emit({
        type: 'stage.started',
        run_id: run.id,
        stage,
        attempt: 1
      })
    )
    // 末阶段（report）给足生成报告的时间感
    const factor = index === STAGES.length - 1 ? 1.4 : 1
    nodes.push(wait(Math.round(baseDelay * factor * (0.85 + Math.random() * 0.3))))
  })

  // 终态：succeeded，携带最终 token 用量（与后端 executor finished_event 同构）
  const tokenUsed = Math.round(run.token_budget * FINAL_TOKEN_RATIO[run.tier] * (0.9 + Math.random() * 0.2))
  nodes.push(
    emit(() => ({
      type: 'run.finished',
      run_id: run.id,
      status: 'succeeded',
      current_stage: 'report',
      token_used: tokenUsed,
      occurred_at: new Date().toISOString()
    }))
  )

  return sequence(...nodes)
}

// 生成 run 对应的 Markdown 报告（run.finished 后写入报告存储）
export function buildReportMarkdown(run: MockRun): string {
  const tierLabel: Record<MockRun['tier'], string> = {
    quick: '快速档',
    standard: '标准档',
    deep: '深度档',
    extreme: '极限档'
  }
  return [
    `# 调研报告：${run.question}`,
    '',
    `> 研究档位：${tierLabel[run.tier]} | 模板：${run.template_id}`,
    '',
    '## 一、调研概述',
    '',
    `本报告围绕研究问题「${run.question}」展开，经过澄清界定、问题分解、证据检索、证据标准化、交叉审校与综合撰写六个阶段，形成如下结论。`,
    '',
    '## 二、主要发现',
    '',
    '1. 该领域在近两年呈现明显的工程化落地趋势，核心能力从单点实验走向可复制的生产系统。',
    '2. 开源方案与商业方案的差距正在缩小，关键差异集中在数据治理、评测体系与运维成本上。',
    '3. 团队在引入相关能力时，应优先评估数据合规、供应商锁定风险与端到端拥有成本。',
    '',
    '## 三、证据要点',
    '',
    '- 行业公开资料显示，头部团队的投入重心已从模型训练转向应用层编排与评估闭环。',
    '- 多个案例表明，小参数模型配合高质量领域数据，在垂直场景中可达到与通用大模型相当的效果。',
    '',
    '## 四、结论与建议',
    '',
    '建议以单一高价值场景作为切入，先建立可量化的评测基线，再逐步扩大覆盖范围；同时保留多云与可替换的架构选项，降低长期供应风险。',
    ''
  ].join('\n')
}
