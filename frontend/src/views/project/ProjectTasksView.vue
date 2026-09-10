<script setup lang="ts">
// 项目内任务列表（[前端详细设计 §12.2 M1]）
// 契约无"项目下 run 列表"端点：run 集合来自本地索引（useRunHistory），
// 进入时并发 GET /runs/{id} 恢复实体；404 条目剔除索引；
// pending/running 行每 5 秒轮询，终态停止。
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useRunHistory } from '@/composables/useRunHistory'
import { listProjects } from '@/services/api/projects'
import { getRun } from '@/services/api/runs'
import type { ApiError } from '@/services/http/error'
import type { ProjectResponse, RunResponse } from '@/services/api/types'
import { runStatusLabel, runStatusVariant, stageLabel, tierLabel } from '@/services/i18n/zh-CN'
import { formatDateTime } from '@/utils/format'
import UiButton from '@/components/ui/UiButton.vue'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import UiEmpty from '@/components/ui/feedback/UiEmpty.vue'
import UiErrorState from '@/components/ui/feedback/UiErrorState.vue'
import UiSkeleton from '@/components/ui/feedback/UiSkeleton.vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const history = useRunHistory()

const projectId = String(route.params.projectId)

const project = ref<ProjectResponse | null>(null)
const projectLoading = ref(true)
const projectLoadError = ref<ApiError | null>(null)
const notFound = ref(false)

// 索引存在但实体临时拉取失败（如网络抖动）的占位行
interface UnavailableRow {
  id: string
  createdAt: string
  unavailable: true
}

type RunRow = RunResponse | UnavailableRow

function isUnavailable(row: RunRow): row is UnavailableRow {
  return (row as UnavailableRow).unavailable === true
}

const rows = ref<RunRow[]>([])
const runsLoading = ref(false)

const POLL_INTERVAL_MS = 5000
let pollTimer: number | undefined

function isActiveStatus(status: RunResponse['status']): boolean {
  return status === 'pending' || status === 'running'
}

async function resolveProject(): Promise<void> {
  projectLoading.value = true
  projectLoadError.value = null
  notFound.value = false
  try {
    let target = projectStore.getProject(projectId)
    // 内存未命中：契约无单项目端点，用列表刷新兜底一次
    if (!target) {
      projectStore.setProjects(await listProjects())
      target = projectStore.getProject(projectId)
    }
    if (!target) {
      notFound.value = true
      return
    }
    project.value = target
    await loadRuns()
  } catch (err) {
    projectLoadError.value = err as ApiError
  } finally {
    projectLoading.value = false
  }
}

async function loadRuns(): Promise<void> {
  runsLoading.value = true
  try {
    const entries = history.listRuns(projectId)
    const settled = await Promise.all(
      entries.map(async (entry) => {
        try {
          const run = await getRun(entry.runId)
          return run
        } catch (err) {
          if ((err as ApiError).status === 404) {
            history.removeRun(projectId, entry.runId)
            return null
          }
          return { id: entry.runId, createdAt: entry.createdAt, unavailable: true } as UnavailableRow
        }
      })
    )
    rows.value = settled.filter((item): item is RunRow => item !== null)
    syncPolling()
  } finally {
    runsLoading.value = false
  }
}

async function pollActiveRuns(): Promise<void> {
  const active = rows.value.filter(
    (row): row is RunResponse => !isUnavailable(row) && isActiveStatus(row.status)
  )
  if (active.length === 0) {
    stopPolling()
    return
  }
  await Promise.all(
    active.map(async (item) => {
      try {
        const latest = await getRun(item.id)
        const idx = rows.value.findIndex((row) => row.id === latest.id)
        if (idx !== -1) rows.value[idx] = latest
      } catch {
        // 单轮单条刷新失败保留下一轮重试，不打断整体轮询
      }
    })
  )
  syncPolling()
}

function syncPolling(): void {
  const hasActive = rows.value.some(
    (row) => !isUnavailable(row) && isActiveStatus(row.status)
  )
  if (hasActive && pollTimer === undefined) {
    pollTimer = window.setInterval(() => {
      void pollActiveRuns()
    }, POLL_INTERVAL_MS)
  } else if (!hasActive && pollTimer !== undefined) {
    stopPolling()
  }
}

function stopPolling(): void {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

onMounted(resolveProject)
onUnmounted(stopPolling)

function openCockpit(runId: string): void {
  router.push(`/projects/${projectId}/runs/${runId}/cockpit`)
}

function startWizard(): void {
  router.push({ path: '/wizard', query: { project_id: projectId } })
}
</script>

<template>
  <section class="tasks-view">
    <UiErrorState
      v-if="notFound"
      title="资源不存在或无权访问"
      :show-retry="false"
      show-back
      @back="router.push('/projects')"
    />

    <UiErrorState
      v-else-if="projectLoadError"
      :error="projectLoadError"
      @retry="resolveProject"
    />

    <template v-else>
      <header class="tasks-view__head">
        <div class="tasks-view__title">
          <RouterLink
            to="/projects"
            class="tasks-view__back"
          >
            项目
          </RouterLink>
          <span class="tasks-view__sep">/</span>
          <h1>{{ projectLoading ? '加载中…' : project?.name }}</h1>
        </div>
        <UiButton
          variant="primary"
          :disabled="projectLoading"
          @click="startWizard"
        >
          + 发起研究
        </UiButton>
      </header>

      <UiSkeleton
        v-if="projectLoading || runsLoading"
        width="100%"
        height="120px"
        radius="md"
      />

      <UiEmpty
        v-else-if="rows.length === 0"
        title="还没有研究任务"
        hint="发起一次研究，任务进度与报告会汇集在这里"
      >
        <template #action>
          <UiButton
            variant="primary"
            @click="startWizard"
          >
            + 发起研究
          </UiButton>
        </template>
      </UiEmpty>

      <ul
        v-else
        class="run-list"
      >
        <li
          v-for="row in rows"
          :key="row.id"
        >
          <UiCard
            v-if="!isUnavailable(row)"
            interactive
            class="run-row"
            @click="openCockpit(row.id)"
            @keydown.enter="openCockpit(row.id)"
          >
            <div class="run-row__main">
              <p class="run-row__question">
                {{ row.question }}
              </p>
              <div class="run-row__meta">
                <UiBadge :variant="runStatusVariant(row.status)">
                  {{ runStatusLabel(row.status) }}
                </UiBadge>
                <UiBadge variant="brand">
                  {{ tierLabel(row.tier) }}
                </UiBadge>
                <span class="run-row__stage">
                  {{ row.current_stage ? stageLabel(row.current_stage) : '阶段待启动' }}
                </span>
                <span class="run-row__time">{{ formatDateTime(row.created_at) }}</span>
              </div>
            </div>
          </UiCard>
          <UiCard
            v-else
            class="run-row run-row--unavailable"
          >
            <p class="run-row__question">
              任务状态暂时不可用
            </p>
            <div class="run-row__meta">
              <span class="run-row__time">{{ formatDateTime(row.createdAt) }}</span>
            </div>
          </UiCard>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.tasks-view {
  flex: 1 1 auto;
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  padding: var(--space-8);
}

.tasks-view__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-6);
}

.tasks-view__title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.tasks-view__title h1 {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
}

.tasks-view__back {
  font-size: var(--font-sm);
  color: var(--neutral-600);
}

.tasks-view__back:hover {
  color: var(--brand-700);
}

.tasks-view__sep {
  color: var(--color-text-muted);
  font-size: var(--font-sm);
}

.run-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.run-row {
  outline: none;
}

.run-row:focus-visible {
  border-color: var(--brand-500);
}

.run-row__question {
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-text-strong);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin-bottom: var(--space-3);
}

.run-row__meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.run-row__stage,
.run-row__time {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.run-row--unavailable {
  opacity: 0.75;
}
</style>
