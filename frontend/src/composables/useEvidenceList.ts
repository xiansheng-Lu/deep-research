// 证据池列表编排（WP-13，[前端详细设计 §9.4]）
// 数据双源：REST 分页（历史全量、稳定总数）+ useRunStream 共享实时态（增量帧、剔除置灰），
// 按稳定 id 并集合并，断网恢复/重连补帧均不会产生重复行；全文 content 仅在卡片展开时懒加载。
import { computed, isRef, onMounted, ref, watch, type MaybeRef, type Ref } from 'vue'
import { getEvidence, listRunEvidence } from '@/services/api/dashboard'
import { peekRunStreamState } from '@/composables/useRunStream'
import type { ApiError } from '@/services/http/error'
import type { EvidenceResponse } from '@/services/api/types'

const DEFAULT_PAGE_SIZE = 20

export interface UseEvidenceListOptions {
  pageSize?: number
  // 按子问题过滤（响应式：WP-14 切换筛选条件时自动重拉）
  subQuestionId?: MaybeRef<string | null>
  // 是否包含已剔除证据（响应式）
  includeExcluded?: MaybeRef<boolean>
  // 挂载时是否自动拉首页（默认 true；独立使用可关闭）
  immediate?: boolean
}

export function useEvidenceList(runId: string, options: UseEvidenceListOptions = {}) {
  const pageSize = options.pageSize ?? DEFAULT_PAGE_SIZE
  const immediate = options.immediate !== false

  const subQuestionId = toRefLike(options.subQuestionId, null)
  const includeExcluded = toRefLike(options.includeExcluded, false)

  // ─── REST 分页态 ───
  const restItems = ref<EvidenceResponse[]>([])
  const restTotal = ref(0)
  const loadedPages = ref(0)
  const loading = ref(false)
  const loadingMore = ref(false)
  const error = ref<ApiError | null>(null)

  // ─── 全文懒加载态（按证据 id 索引）───
  const contents = ref<Record<string, string>>({})
  const loadingContentIds = ref<Set<string>>(new Set())
  const contentErrors = ref<Record<string, string>>({})

  // 共享实时态（指挥舱挂载 useRunStream 后必然存在；独立使用时为 null，纯 REST 工作）
  const liveState = peekRunStreamState(runId)

  // ─── 双源并集 + 过滤 ───

  const mergedItems = computed<EvidenceResponse[]>(() => {
    const liveRows: EvidenceResponse[] = liveState?.evidence ?? []
    return unionEvidence(restItems.value, liveRows)
  })

  const items = computed<EvidenceResponse[]>(() => {
    const sqId = subQuestionId.value
    const keepExcluded = includeExcluded.value
    return mergedItems.value.filter((e) => {
      if (sqId && e.sub_question_id !== sqId) return false
      if (!keepExcluded && e.excluded_by_user) return false
      return true
    })
  })

  // 实时增量可能超过 REST 已加载页覆盖范围：以两者最大值暴露总数
  const total = computed(() => Math.max(restTotal.value, mergedItems.value.length))
  // 视图是 REST 与实时态的并集：并集（按当前过滤）已覆盖服务端总数即无需再翻页；
  // 纯 REST 使用（无实时缓存，如事后回看终态 run）时退化为标准分页
  const hasMore = computed(() => items.value.length < restTotal.value)

  // ─── 分页拉取 ───

  async function fetchPage(page: number, mode: 'refresh' | 'more'): Promise<void> {
    if (mode === 'refresh') loading.value = true
    else loadingMore.value = true
    error.value = null
    try {
      const pageEnv = await listRunEvidence(runId, {
        page,
        page_size: pageSize,
        sub_question_id: subQuestionId.value ?? undefined,
        include_excluded: includeExcluded.value
      })
      if (mode === 'refresh') {
        restItems.value = pageEnv.items
        loadedPages.value = 1
      } else {
        restItems.value = unionEvidence(restItems.value, pageEnv.items)
        loadedPages.value = page
      }
      restTotal.value = pageEnv.total
    } catch (err) {
      // 刷新失败保留旧数据并暴露错误；首页由错误态组件承接
      if (mode === 'refresh') error.value = err as ApiError
    } finally {
      if (mode === 'refresh') loading.value = false
      else loadingMore.value = false
    }
  }

  function refresh(): Promise<void> {
    return fetchPage(1, 'refresh')
  }

  function loadMore(): Promise<void> {
    if (loading.value || loadingMore.value || !hasMore.value) return Promise.resolve()
    return fetchPage(loadedPages.value + 1, 'more')
  }

  // 过滤条件变化：回到第一页重拉（REST 侧过滤），实时侧在 computed 中同步过滤
  watch([subQuestionId, includeExcluded], () => {
    void refresh()
  })

  // ─── 全文懒加载 ───

  function findMerged(evidenceId: string): EvidenceResponse | undefined {
    return mergedItems.value.find((e) => e.id === evidenceId)
  }

  async function loadContent(evidenceId: string): Promise<string | null> {
    const cached = contents.value[evidenceId]
    if (cached !== undefined) return cached
    if (loadingContentIds.value.has(evidenceId)) return null
    // 列表行已带全文（真实后端未来可能在首帧返回）时直接缓存，不重复请求
    const inList = findMerged(evidenceId)
    if (typeof inList?.content === 'string') {
      contents.value = { ...contents.value, [evidenceId]: inList.content }
      return inList.content
    }
    loadingContentIds.value = new Set(loadingContentIds.value).add(evidenceId)
    try {
      const detail = await getEvidence(runId, evidenceId)
      const text = detail.content ?? ''
      contents.value = { ...contents.value, [evidenceId]: text }
      delete contentErrors.value[evidenceId]
      return text
    } catch (err) {
      const apiError = err as ApiError
      contentErrors.value = {
        ...contentErrors.value,
        [evidenceId]: apiError?.detail || '全文加载失败，请稍后重试'
      }
      return null
    } finally {
      const next = new Set(loadingContentIds.value)
      next.delete(evidenceId)
      loadingContentIds.value = next
    }
  }

  function isContentLoading(evidenceId: string): boolean {
    return loadingContentIds.value.has(evidenceId)
  }

  function contentError(evidenceId: string): string | undefined {
    return contentErrors.value[evidenceId]
  }

  if (immediate) {
    onMounted(refresh)
  }

  return {
    items,
    total,
    hasMore,
    loading,
    loadingMore,
    error,
    refresh,
    loadMore,
    contents,
    loadContent,
    isContentLoading,
    contentError
  }
}

// 把 options 中的 MaybeRef/常量统一归一为 Ref（传入 Ref 时保持同一引用，不丢响应性）
function toRefLike<T>(value: MaybeRef<T> | undefined, fallback: T): Ref<T> {
  if (value === undefined) return ref(fallback) as Ref<T>
  return isRef(value) ? (value as Ref<T>) : (ref(value) as Ref<T>)
}

// 稳定 id 并集：REST 行优先保留 content 等详情字段，实时行覆盖进行态字段（如剔除标记）
function unionEvidence(rest: EvidenceResponse[], live: EvidenceResponse[]): EvidenceResponse[] {
  const map = new Map<string, EvidenceResponse>()
  for (const item of rest) map.set(item.id, item)
  for (const item of live) {
    const existing = map.get(item.id)
    if (!existing) {
      map.set(item.id, item)
      continue
    }
    map.set(item.id, {
      ...existing,
      ...compact(item),
      // 实时增量帧 content 恒为 null/缺省，不得覆盖 REST 已拉到的全文
      content: existing.content ?? item.content ?? null
    })
  }
  return Array.from(map.values())
}

// 去掉 undefined，避免增量合并覆盖已有关键字段
function compact<T extends object>(obj: T): Partial<T> {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined)) as Partial<T>
}
