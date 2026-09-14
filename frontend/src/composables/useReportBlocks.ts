// 结构化终稿（blocks 轨）取数与索引（WP-16；[前端详细设计 §7.4 / §10.2]）
// mock-first：GET /reports/{run_id} 在 demo_full 返回 markdown+blocks 超集；
// 真实后端 M2-7 数据点级溯源冻结前仅回 markdown（无 blocks），页面据 hasBlocks 留在 markdown 轨。
// 本组合式只负责取数/索引/交互状态，组件不直接发 REST（[前端详细设计 §10.1]）。
import { computed, ref } from 'vue'
import { getReportCitations, getStructuredReport } from '@/services/api/reports'
import { getRunConflicts } from '@/services/api/dashboard'
import { track } from '@/services/telemetry/telemetry'
import type { ApiError } from '@/services/http/error'
import type {
  ConflictEvidenceSummary,
  ConflictResponse,
  ReportBlock,
  ReportCitationItem,
  StructuredReportResponse
} from '@/services/api/types'

export function useReportBlocks(runId: string) {
  const loading = ref(false)
  const error = ref<ApiError | null>(null)
  const report = ref<StructuredReportResponse | null>(null)
  const citations = ref<ReportCitationItem[]>([])
  const conflicts = ref<ConflictResponse[]>([])

  const blocks = computed<ReportBlock[]>(() => report.value?.blocks ?? [])
  const outline = computed<StructuredReportResponse['outline']>(() => report.value?.outline ?? [])
  // 终稿是否走 blocks 轨：响应含非空 blocks（demo_full 与后端 M2-7 冻结后的目标形态）。
  // M2-7 冻结、终稿全部结构化后，ReportView 的 markdown 终稿分支随之删除（详设 §11.4）。
  const hasBlocks = computed(() => blocks.value.length > 0)

  // 索引 1：marker（如 "[1]"）→ 引用项，供 CitationMarker 悬停卡与点击定位
  const citationByMarker = computed(() => {
    const map = new Map<string, ReportCitationItem>()
    for (const item of citations.value) {
      map.set(item.marker, item)
    }
    return map
  })

  // 索引 2：evidence_id → 引用项（同一证据可在多个 block 引用）
  const citationsByEvidence = computed(() => {
    const map = new Map<string, ReportCitationItem[]>()
    for (const item of citations.value) {
      const list = map.get(item.evidence_id)
      if (list) {
        list.push(item)
      } else {
        map.set(item.evidence_id, [item])
      }
    }
    return map
  })

  // 索引 3：conflict_id → 冲突列表项，供 dispute 块关联
  const conflictById = computed(() => {
    const map = new Map<string, ConflictResponse>()
    for (const item of conflicts.value) {
      map.set(item.id, item)
    }
    return map
  })

  // 底部信源索引按角标序号排序展示（marker 形态 "[n]"）
  const sortedCitations = computed(() =>
    [...citations.value].sort((a, b) => markerNumber(a.marker) - markerNumber(b.marker))
  )

  // 报告末尾的局限汇总
  const limitationBlocks = computed(() => blocks.value.filter((block) => block.type === 'limitation'))

  // 「只看分歧」筛选（[前端详细设计 §10.3] 第四态：未命中灰化不删除；
  // 「只看已交叉验证」为 M4 范围，本 WP 不做不预埋）
  const disputesOnly = ref(false)

  function isBlockDimmed(block: ReportBlock): boolean {
    return disputesOnly.value && block.type !== 'dispute'
  }

  // ─── SourcePanel 抽屉 ───

  const sourcePanelOpen = ref(false)
  const activeEvidenceId = ref<string | null>(null)

  function openSource(evidenceId: string): void {
    activeEvidenceId.value = evidenceId
    sourcePanelOpen.value = true
    // WP-18：点击/键盘开溯源抽屉的唯一漏斗（CitationMarker.activate → 本函数），
    // props 只记证据 id 这一标识字段
    track('report.citation.open', { evidence_id: evidenceId }, runId)
  }

  function closeSourcePanel(): void {
    sourcePanelOpen.value = false
  }

  // ─── dispute 块双方证据（M2 只读不接裁决，数据源为报告引用索引）───

  // 报告 citations 已随首帧并行加载且九字段齐全（含 ev05/ev12），
  // 直接由引用索引构造 ConflictBlock 需要的证据摘要，与指挥舱「证据池优先」同模式；
  // 不调用 GET /conflicts/{id}：mock 该端点当前仅回冲突列表项形态（无内嵌 evidence_a/b），
  // 待真实后端 M2-2 详情内嵌摘要与报告场景需要额外字段时，再在此切换数据源。
  function citationEvidence(evidenceId: string): ConflictEvidenceSummary | null {
    const item = citationsByEvidence.value.get(evidenceId)?.[0]
    if (!item || !item.domain || !item.source_type || !item.credibility) return null
    return {
      id: item.evidence_id,
      title: item.title,
      url: item.url,
      domain: item.domain,
      snippet: item.snippet,
      credibility: item.credibility,
      source_type: item.source_type,
      published_at: item.published_at ?? null
    }
  }

  // dispute 区块的 ConflictBlock 入参聚合（冲突列表项 + 引用索引中的双方证据摘要）
  function disputeOf(block: ReportBlock): {
    conflict: ConflictResponse | null
    evidenceA: ConflictEvidenceSummary | null
    evidenceB: ConflictEvidenceSummary | null
  } {
    const empty = { conflict: null, evidenceA: null, evidenceB: null }
    if (!block.conflict_id) return empty
    const conflict = conflictById.value.get(block.conflict_id) ?? null
    if (!conflict) return empty
    return {
      conflict,
      evidenceA: citationEvidence(conflict.evidence_a_id),
      evidenceB: citationEvidence(conflict.evidence_b_id)
    }
  }

  // ─── 目录定位 ───

  function scrollToBlock(blockId: string): void {
    document.getElementById(blockId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // outline 项与 block 无显式映射：按 outline.type 定位该类型首个 block；
  // M2-7 若后端给出 section→blocks 映射，在此对齐（WP-16 计划决策 6）
  function locateOutline(type: string, fallbackIndex = 0): void {
    const target = blocks.value.find((block) => block.type === type) ?? blocks.value[fallbackIndex]
    if (target?.id) scrollToBlock(target.id)
  }

  // ─── 取数 ───

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      // 三路并行：结构化终稿 + 角标溯源索引 + run 冲突列表（计划决策 8：首帧 ≤2s）
      const [structured, citationList, conflictList] = await Promise.all([
        getStructuredReport(runId),
        getReportCitations(runId),
        getRunConflicts(runId)
      ])
      report.value = structured
      citations.value = citationList
      conflicts.value = conflictList
    } catch (err) {
      // 真链 M2-7 前 citations/conflicts 端点可能未就绪：增强失败不推翻 markdown 轨，
      // ReportView 按 hasBlocks=false 继续渲染 markdown（计划 §五真链验收）
      error.value = err as ApiError
    } finally {
      loading.value = false
    }
  }

  return {
    loading,
    error,
    report,
    blocks,
    outline,
    hasBlocks,
    citations,
    sortedCitations,
    conflicts,
    limitationBlocks,
    citationByMarker,
    citationsByEvidence,
    conflictById,
    disputesOnly,
    isBlockDimmed,
    sourcePanelOpen,
    activeEvidenceId,
    openSource,
    closeSourcePanel,
    disputeOf,
    locateOutline,
    load
  }
}

// 取角标内序号："[12]" → 12；非标准形态排到末尾
function markerNumber(marker: string): number {
  const matched = /\[(\d+)\]/.exec(marker)
  return matched ? Number(matched[1]) : Number.MAX_SAFE_INTEGER
}
