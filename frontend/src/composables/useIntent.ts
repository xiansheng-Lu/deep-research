// 意图分流编排（[前端详细设计 §11.1] 单入口状态机；对齐 M2-1 交接单 §2）
// 输入 → POST /intent/classify（2s 超时微态不阻塞）→ chat/research/uncertain；
// 接口失败/超时按保守策略本地降级 research 并显式标注 degraded。
import { ref } from 'vue'
import { classifyIntent } from '@/services/api/intent'
import type { ForceIntent, IntentClassifyResponse } from '@/services/api/types'

// 交接单口径：判别等待 ≤2s；超过即按降级处理
const CLASSIFY_TIMEOUT_MS = 2000

export type IntentPhase = 'idle' | 'classifying' | 'result'

// 保守降级结果：与后端 fallback 形态一致（research + degraded=true，推荐档位给不出时由 UI 兜默认档）
function buildFallbackResult(): IntentClassifyResponse {
  return {
    intent: 'research',
    confidence: 0,
    recommended_template: 'generic',
    recommended_tier: null,
    estimated_token_budget: null,
    estimated_cost_grade: null,
    source: 'fallback',
    degraded: true,
    reason: '意图判别服务暂不可用，已保守按深度研究准备'
  }
}

export function useIntent() {
  const phase = ref<IntentPhase>('idle')
  const result = ref<IntentClassifyResponse | null>(null)

  // 判别：带 2s 竞速；任何失败（5xx/网络/超时）都收敛为保守 research
  async function classify(text: string, force?: ForceIntent): Promise<IntentClassifyResponse> {
    phase.value = 'classifying'
    try {
      const res = await Promise.race([
        classifyIntent({ text, force }),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error('INTENT_TIMEOUT')), CLASSIFY_TIMEOUT_MS)
        )
      ])
      result.value = res
      phase.value = 'result'
      return res
    } catch {
      const fallback = buildFallbackResult()
      result.value = fallback
      phase.value = 'result'
      return fallback
    }
  }

  function reset(): void {
    phase.value = 'idle'
    result.value = null
  }

  return { phase, result, classify, reset }
}
