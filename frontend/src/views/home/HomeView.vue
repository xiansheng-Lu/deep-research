<script setup lang="ts">
// 首页 · 意图路由单入口（[前端详细设计 §11.1 M2]，对齐 M2-1 交接单 §2.3 分流口径）
// 输入 → classify：chat 跳 /assistant（不产生项目数据）；research/uncertain 展开确认层后跳向导。
// 活跃研究卡片：M2-4 前无 run 列表端点，用本地 run 索引 + 逐 run GET 探测，标注"仅本设备"。
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import UiCard from '@/components/ui/UiCard.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import UiTextarea from '@/components/ui/form/UiTextarea.vue'
import { useIntent } from '@/composables/useIntent'
import { useProjectStore } from '@/stores/project'
import { listProjects } from '@/services/api/projects'
import { getRun } from '@/services/api/runs'
import type { RunResponse, RunTier } from '@/services/api/types'
import { useRunHistory, type RunHistoryEntry } from '@/composables/useRunHistory'
import { TIER_METAS } from '@/services/domain/tiers'
import { tierLabel } from '@/services/i18n/zh-CN'
import { formatNumber } from '@/utils/format'

const MAX_INPUT = 2000

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const { phase, result, classify, reset: resetIntent } = useIntent()
const history = useRunHistory()

const question = ref('')
const selectedProjectId = ref<string | null>(null)
const selectedTier = ref<RunTier>('quick')
const projectsLoading = ref(true)

// 活跃研究（本设备索引 + GET 探测）
const activeRuns = ref<Array<{ projectId: string; projectName: string; run: RunResponse }>>([])

const hasProjects = computed(() => projectStore.projects.length > 0)

const classifying = computed(() => phase.value === 'classifying')

// 确认层在 research / uncertain / 降级 fallback 三种结果下出现
const showConfirm = computed(
  () => phase.value === 'result' && result.value !== null && result.value.intent !== 'chat'
)
const isUncertain = computed(() => result.value?.intent === 'uncertain')
const isDegraded = computed(() => result.value?.degraded === true)
const estimatedBudget = computed(() => result.value?.estimated_token_budget ?? null)

const canSubmit = computed(
  () => question.value.trim().length >= 1 && !!selectedProjectId.value && !classifying.value
)

async function ensureProjects(): Promise<void> {
  projectsLoading.value = true
  try {
    if (!projectStore.loaded) {
      projectStore.setProjects(await listProjects())
    }
    if (projectStore.projects.length > 0 && !selectedProjectId.value) {
      selectedProjectId.value = projectStore.projects[0].id
    }
  } finally {
    projectsLoading.value = false
  }
}

async function refreshActiveRuns(): Promise<void> {
  const entries: Array<RunHistoryEntry & { projectId: string }> = []
  for (const project of projectStore.projects) {
    for (const entry of history.listRuns(project.id)) {
      entries.push({ ...entry, projectId: project.id })
    }
  }
  // 仅探测最近 8 条，避免过多请求
  const settled = await Promise.allSettled(
    entries.slice(0, 8).map(async (entry) => {
      const run = await getRun(entry.runId)
      return { entry, run }
    })
  )
  const active: typeof activeRuns.value = []
  for (const item of settled) {
    if (item.status !== 'fulfilled') continue
    const { entry, run } = item.value
    if (run.status !== 'pending' && run.status !== 'running' && run.status !== 'paused') continue
    const project = projectStore.getProject(entry.projectId)
    if (!project) continue
    active.push({ projectId: entry.projectId, projectName: project.name, run })
  }
  activeRuns.value = active
}

onMounted(async () => {
  await ensureProjects()
  void refreshActiveRuns()
  const q = typeof route.query.q === 'string' ? route.query.q : ''
  const force = route.query.force
  if (q) {
    question.value = q
    // 助手面板"深度研究这个问题"入口：force=research 自动判别进确认层
    if (force === 'research') {
      const res = await classify(q, 'research')
      // 与手动发送路径一致回填推荐档位，否则档位与预估 Token 会错配
      selectedTier.value = res.recommended_tier ?? 'quick'
    }
  }
})

async function onClassify(force?: 'chat' | 'research'): Promise<void> {
  const text = question.value.trim()
  if (!text) return
  const res = await classify(text, force)
  if (res.intent === 'chat') {
    goChat()
    return
  }
  // research / uncertain / fallback：预选档位（推荐为空时默认 quick，降级场景同此）
  selectedTier.value = res.recommended_tier ?? 'quick'
}

function goChat(): void {
  // 闲聊不创建 run、不落项目；携带首条消息由助手面板自动发起
  void router.push({ path: '/assistant', query: { q: question.value.trim() } })
}

async function switchToChat(): Promise<void> {
  // 确认层撤回：以 force=chat 重调（交接单 §2.4），结果必为 chat 后跳转
  await classify(question.value.trim(), 'chat')
  goChat()
}

function confirmResearch(): void {
  if (!selectedProjectId.value) return
  void router.push({
    path: '/wizard',
    query: {
      project_id: selectedProjectId.value,
      template_id: result.value?.recommended_template ?? 'generic',
      tier: selectedTier.value,
      q: question.value.trim()
    }
  })
}

function openCockpit(item: { projectId: string; run: RunResponse }): void {
  void router.push(
    `/projects/${item.projectId}/runs/${item.run.id}/cockpit`
  )
}

function backToInput(): void {
  resetIntent()
}
</script>

<template>
  <section class="home-view">
    <header class="home-view__hero">
      <h1 class="home-view__title">
        想研究什么？直接问
      </h1>
      <p class="home-view__subtitle">
        描述你的问题，系统会判断"直接聊聊"还是"发起深度研究"；深度研究将走六阶段多源核验流水线。
      </p>
    </header>

    <UiCard class="home-view__entry">
      <UiTextarea
        v-model="question"
        :maxlength="MAX_INPUT"
        :rows="3"
        placeholder="例如：对比 PostgreSQL 与 MongoDB 在 JSON 文档查询场景下的索引机制差异"
      />

      <div class="home-view__entry-bar">
        <span class="home-view__force-hint">
          <button
            type="button"
            class="home-view__force-link"
            :disabled="!question.trim() || classifying"
            @click="onClassify('chat')"
          >
            直接聊聊
          </button>
          <span class="home-view__force-sep">·</span>
          <button
            type="button"
            class="home-view__force-link"
            :disabled="!question.trim() || classifying"
            @click="onClassify('research')"
          >
            强制深度研究
          </button>
        </span>
        <UiButton
          :loading="classifying"
          :disabled="!question.trim()"
          @click="onClassify()"
        >
          {{ classifying ? '正在识别意图…' : '发送' }}
        </UiButton>
      </div>

      <!-- 研究/不确定/降级确认层 -->
      <div
        v-if="showConfirm"
        class="home-view__confirm"
        role="status"
      >
        <div class="home-view__confirm-head">
          <UiBadge :variant="isUncertain || isDegraded ? 'warn' : 'brand'">
            {{ isDegraded ? '判别暂不可用' : isUncertain ? '意图不确定' : '深度研究' }}
          </UiBadge>
          <span class="home-view__confirm-text">
            <template v-if="isDegraded">智能判别暂不可用，已按深度研究为你准备</template>
            <template v-else-if="isUncertain">识别不太确定，已为你准备深度研究</template>
            <template v-else>将启动深度研究，走六阶段多源核验</template>
          </span>
        </div>

        <div class="home-view__confirm-grid">
          <label class="home-view__field">
            <span class="home-view__field-label">归属项目</span>
            <select
              v-if="hasProjects"
              v-model="selectedProjectId"
              class="home-view__select"
            >
              <option
                v-for="project in projectStore.projects"
                :key="project.id"
                :value="project.id"
              >
                {{ project.name }}
              </option>
            </select>
            <span
              v-else-if="!projectsLoading"
              class="home-view__field-empty"
            >
              暂无项目，请先到「项目」页创建
            </span>
            <span
              v-else
              class="home-view__field-empty"
            >加载项目…</span>
          </label>

          <div class="home-view__field">
            <span class="home-view__field-label">研究档位</span>
            <div class="home-view__tiers">
              <button
                v-for="meta in TIER_METAS"
                :key="meta.tier"
                type="button"
                class="home-view__tier"
                :class="{ 'is-selected': selectedTier === meta.tier }"
                @click="selectedTier = meta.tier"
              >
                {{ tierLabel(meta.tier) }}
              </button>
            </div>
          </div>
        </div>

        <p class="home-view__budget">
          预估 Token 用量：
          <strong v-if="estimatedBudget !== null">{{ formatNumber(estimatedBudget) }}</strong>
          <span
            v-else
            class="home-view__budget-empty"
          >启动后在看板查看</span>
        </p>

        <div class="home-view__confirm-actions">
          <UiButton
            variant="ghost"
            size="sm"
            @click="backToInput"
          >
            返回修改
          </UiButton>
          <UiButton
            variant="ghost"
            size="sm"
            @click="switchToChat"
          >
            改为直接聊聊
          </UiButton>
          <UiButton
            :disabled="!canSubmit"
            @click="confirmResearch"
          >
            确认发起深度研究
          </UiButton>
        </div>
      </div>
    </UiCard>

    <!-- 进行中研究（仅本设备索引，M2-4 列表端点后升级） -->
    <section
      v-if="activeRuns.length > 0"
      class="home-view__active"
    >
      <h2 class="home-view__section-title">
        进行中的研究<span class="home-view__section-note">（仅本设备）</span>
      </h2>
      <div class="home-view__active-list">
        <UiCard
          v-for="item in activeRuns"
          :key="item.run.id"
          interactive
          class="home-view__active-item"
          @click="openCockpit(item)"
        >
          <div class="home-view__active-main">
            <p class="home-view__active-question">
              {{ item.run.question }}
            </p>
            <p class="home-view__active-meta">
              {{ item.projectName }} · {{ item.run.id.slice(0, 10) }}
            </p>
          </div>
          <UiBadge :variant="item.run.status === 'paused' ? 'warn' : 'info'">
            {{ item.run.status === 'paused' ? '已暂停' : '进行中' }}
          </UiBadge>
        </UiCard>
      </div>
    </section>
  </section>
</template>

<style scoped>
.home-view {
  flex: 1 1 auto;
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  padding: var(--space-8) var(--space-6);
}

.home-view__hero {
  margin-bottom: var(--space-5);
  text-align: center;
}

.home-view__title {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-2xl);
  font-weight: 600;
  color: var(--color-text-strong);
  margin-bottom: var(--space-2);
}

.home-view__subtitle {
  font-size: var(--font-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
}

.home-view__entry-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.home-view__force-hint {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.home-view__force-link {
  border: none;
  background: none;
  padding: 0;
  color: var(--brand-700);
  font-size: var(--font-xs);
  cursor: pointer;
}

.home-view__force-link:hover:not(:disabled) {
  color: var(--brand-600);
  text-decoration: underline;
}

.home-view__force-link:disabled {
  color: var(--neutral-400);
  cursor: not-allowed;
}

.home-view__confirm {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.home-view__confirm-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.home-view__confirm-text {
  font-size: var(--font-sm);
  color: var(--color-text);
}

.home-view__confirm-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-4);
  margin-bottom: var(--space-3);
}

@media (min-width: 720px) {
  .home-view__confirm-grid {
    grid-template-columns: 1fr 1.2fr;
  }
}

.home-view__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.home-view__field-label {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.home-view__select {
  height: 38px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: var(--font-sm);
}

.home-view__field-empty {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.home-view__tiers {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.home-view__tier {
  padding: 6px var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: var(--font-xs);
  cursor: pointer;
}

.home-view__tier.is-selected {
  border-color: var(--brand-500);
  color: var(--brand-700);
  background: var(--brand-50);
  font-weight: 500;
}

.home-view__budget {
  font-size: var(--font-sm);
  color: var(--color-text);
  margin-bottom: var(--space-3);
}

.home-view__budget-empty {
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

.home-view__confirm-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.home-view__active {
  margin-top: var(--space-7);
}

.home-view__section-title {
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
  margin-bottom: var(--space-3);
}

.home-view__section-note {
  font-size: var(--font-xs);
  font-weight: 400;
  color: var(--color-text-muted);
}

.home-view__active-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.home-view__active-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.home-view__active-question {
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-text);
  margin-bottom: 4px;
}

.home-view__active-meta {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
</style>
