<script setup lang="ts">
// 发起研究向导（[前端详细设计 §11.2 M1]）
// 步骤：[选择项目（无 project_id query 时前置）] → 模板 → 档位 → 问题 → 确认 → 启动
// M1 仅 generic 单模板；提交 POST /runs，成功写本地索引并跳指挥舱。
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useRunHistory } from '@/composables/useRunHistory'
import { listProjects } from '@/services/api/projects'
import { createRun } from '@/services/api/runs'
import type { ApiError } from '@/services/http/error'
import type { ProjectResponse, RunTier } from '@/services/api/types'
import { TIER_METAS } from '@/services/domain/tiers'
import { tierLabel } from '@/services/i18n/zh-CN'
import { formatNumber } from '@/utils/format'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiTextarea from '@/components/ui/form/UiTextarea.vue'
import UiEmpty from '@/components/ui/feedback/UiEmpty.vue'
import UiErrorState from '@/components/ui/feedback/UiErrorState.vue'
import UiSkeleton from '@/components/ui/feedback/UiSkeleton.vue'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'

const GENERIC_TEMPLATE_ID = 'generic'
const QUESTION_MIN = 5
const QUESTION_MAX = 4096

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const history = useRunHistory()

const projectsLoading = ref(true)
const projectsError = ref<ApiError | null>(null)
// 是否需要项目选择步：加载完成后一次性确定，向导过程中不再变化
const hasProjectStep = ref(false)
const selectedProjectId = ref<string | null>(null)
const selectedTier = ref<RunTier | null>(null)
const question = ref('')
const questionError = ref('')
const step = ref(0)
const submitting = ref(false)
const submitError = ref('')

// 同一次向导内复用同一幂等键，保证失败重试不会创建多个 run
const idempotencyKey = crypto.randomUUID()

interface StepDef {
  key: string
  label: string
}

const steps = computed<StepDef[]>(() => {
  const list: StepDef[] = []
  if (hasProjectStep.value) list.push({ key: 'project', label: '选择项目' })
  list.push(
    { key: 'template', label: '模板' },
    { key: 'tier', label: '档位' },
    { key: 'question', label: '问题' },
    { key: 'confirm', label: '确认' },
    { key: 'launch', label: '启动' }
  )
  return list
})

const selectedProject = computed<ProjectResponse | null>(() =>
  selectedProjectId.value ? projectStore.getProject(selectedProjectId.value) : null
)

const trimmedQuestionLength = computed(() => question.value.trim().length)

async function ensureProjects(): Promise<void> {
  projectsLoading.value = true
  projectsError.value = null
  try {
    if (!projectStore.loaded) {
      projectStore.setProjects(await listProjects())
    }
    const queryProjectId =
      typeof route.query.project_id === 'string' ? route.query.project_id : null
    const visible = queryProjectId ? projectStore.getProject(queryProjectId) : null
    if (visible) {
      selectedProjectId.value = visible.id
      hasProjectStep.value = false
    } else {
      selectedProjectId.value = null
      hasProjectStep.value = true
    }
  } catch (err) {
    projectsError.value = err as ApiError
  } finally {
    projectsLoading.value = false
  }
}

onMounted(ensureProjects)

const currentStepKey = computed(() => steps.value[step.value]?.key ?? '')

function pickProject(id: string): void {
  selectedProjectId.value = id
}

function pickTier(tier: RunTier): void {
  selectedTier.value = tier
}

const canGoNext = computed(() => {
  switch (currentStepKey.value) {
    case 'project':
      return selectedProjectId.value !== null
    case 'tier':
      return selectedTier.value !== null
    case 'question':
      return (
        trimmedQuestionLength.value >= QUESTION_MIN &&
        trimmedQuestionLength.value <= QUESTION_MAX
      )
    default:
      return true
  }
})

function goNext(): void {
  if (!canGoNext.value) return
  if (currentStepKey.value === 'question') {
    questionError.value = ''
  }
  // 首次进入档位步：默认取项目默认档位
  const nextKey = steps.value[step.value + 1]?.key
  if (nextKey === 'tier' && selectedTier.value === null && selectedProject.value) {
    selectedTier.value = selectedProject.value.default_tier
  }
  step.value += 1
}

function goPrev(): void {
  if (step.value > 0) step.value -= 1
}

// 步骤条仅允许回到已经过的步骤
function gotoStep(index: number): void {
  if (index <= step.value) step.value = index
}

function cancel(): void {
  if (selectedProjectId.value) {
    router.push(`/projects/${selectedProjectId.value}/tasks`)
  } else {
    router.push('/projects')
  }
}

async function launch(): Promise<void> {
  if (submitting.value) return
  submitError.value = ''
  if (!selectedProjectId.value || !selectedTier.value) {
    submitError.value = '请先完成项目与档位选择'
    return
  }
  if (trimmedQuestionLength.value < QUESTION_MIN || trimmedQuestionLength.value > QUESTION_MAX) {
    questionError.value = `研究问题长度需在 ${QUESTION_MIN}-${QUESTION_MAX} 个字符之间`
    return
  }
  submitting.value = true
  try {
    const run = await createRun(
      {
        project_id: selectedProjectId.value,
        question: question.value.trim(),
        tier: selectedTier.value,
        template_id: GENERIC_TEMPLATE_ID
      },
      { idempotencyKey }
    )
    history.recordRun(run.project_id, run.id, run.created_at)
    router.replace(`/projects/${run.project_id}/runs/${run.id}/cockpit`)
  } catch (err) {
    submitError.value = (err as ApiError).detail || '启动失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="wizard-view">
    <header class="wizard-view__head">
      <h1>发起研究</h1>
    </header>

    <UiErrorState
      v-if="projectsError"
      :error="projectsError"
      @retry="ensureProjects"
    />

    <UiSkeleton
      v-else-if="projectsLoading"
      width="100%"
      height="360px"
      radius="lg"
    />

    <template v-else>
      <ol class="wizard-steps">
        <li
          v-for="(item, idx) in steps"
          :key="item.key"
          class="wizard-steps__item"
          :class="{
            'is-current': idx === step,
            'is-done': idx < step,
            'is-clickable': idx < step
          }"
        >
          <button
            type="button"
            class="wizard-steps__button"
            :disabled="idx > step"
            @click="gotoStep(idx)"
          >
            <span class="wizard-steps__index">{{ idx + 1 }}</span>
            <span class="wizard-steps__label">{{ item.label }}</span>
          </button>
        </li>
      </ol>

      <UiCard
        size="lg"
        class="wizard-panel"
      >
        <!-- 选择项目 -->
        <div
          v-if="currentStepKey === 'project'"
          class="step-body"
        >
          <h2>选择研究所属项目</h2>
          <p class="step-body__hint">
            研究任务、证据与报告会归集到所选项目下
          </p>
          <UiEmpty
            v-if="projectStore.projects.length === 0"
            title="还没有可用项目"
            hint="请先创建一个项目，再发起研究"
          >
            <template #action>
              <UiButton
                variant="primary"
                @click="router.push('/projects')"
              >
                去新建项目
              </UiButton>
            </template>
          </UiEmpty>
          <div
            v-else
            class="select-grid"
          >
            <UiCard
              v-for="project in projectStore.projects"
              :key="project.id"
              interactive
              class="select-card"
              :class="{ 'is-selected': selectedProjectId === project.id }"
              @click="pickProject(project.id)"
              @keydown.enter="pickProject(project.id)"
            >
              <p class="select-card__title">
                {{ project.name }}
              </p>
              <p class="select-card__desc">
                {{ project.description || '暂无描述' }}
              </p>
              <UiBadge variant="brand">
                {{ tierLabel(project.default_tier) }}
              </UiBadge>
            </UiCard>
          </div>
        </div>

        <!-- 模板 -->
        <div
          v-else-if="currentStepKey === 'template'"
          class="step-body"
        >
          <h2>选择研究模板</h2>
          <p class="step-body__hint">
            模板定义研究流程与产出结构；M1 提供通用模板，更多模板将在模板库中提供
          </p>
          <div class="select-grid select-grid--single">
            <UiCard
              interactive
              class="select-card is-selected"
            >
              <p class="select-card__title">
                通用研究模板
              </p>
              <p class="select-card__desc">
                澄清界定 → 问题分解 → 证据检索 → 标准化 → 交叉审校 → 报告撰写
              </p>
              <UiBadge variant="brand">
                generic
              </UiBadge>
            </UiCard>
          </div>
        </div>

        <!-- 档位 -->
        <div
          v-else-if="currentStepKey === 'tier'"
          class="step-body"
        >
          <h2>选择研究档位</h2>
          <p class="step-body__hint">
            档位决定子问题拆解规模与 token 预算
          </p>
          <div class="select-grid">
            <UiCard
              v-for="meta in TIER_METAS"
              :key="meta.tier"
              interactive
              class="select-card"
              :class="{ 'is-selected': selectedTier === meta.tier }"
              @click="pickTier(meta.tier)"
              @keydown.enter="pickTier(meta.tier)"
            >
              <p class="select-card__title">
                {{ tierLabel(meta.tier) }}
              </p>
              <p class="select-card__desc">
                上限 {{ meta.subQuestionLimit }} 个子问题
              </p>
              <UiBadge variant="neutral">
                {{ formatNumber(meta.tokenBudget) }} tokens
              </UiBadge>
            </UiCard>
          </div>
        </div>

        <!-- 问题 -->
        <div
          v-else-if="currentStepKey === 'question'"
          class="step-body"
        >
          <h2>描述你的研究问题</h2>
          <p class="step-body__hint">
            清楚说明研究对象、时间范围与关注维度，系统会先做澄清界定再分解执行
          </p>
          <UiTextarea
            v-model="question"
            :rows="8"
            :maxlength="QUESTION_MAX"
            placeholder="例如：调研 2024-2026 年 AI 编程助手领域的主要技术趋势、代表产品与商业化路径"
            :error="questionError"
          />
          <p
            class="step-body__counter"
            :class="{
              'is-over':
                trimmedQuestionLength > 0 && trimmedQuestionLength < QUESTION_MIN
            }"
          >
            {{ trimmedQuestionLength }} / {{ QUESTION_MAX }}（至少 {{ QUESTION_MIN }} 个字符）
          </p>
        </div>

        <!-- 确认 -->
        <div
          v-else-if="currentStepKey === 'confirm'"
          class="step-body"
        >
          <h2>确认研究配置</h2>
          <dl class="confirm-list">
            <div class="confirm-list__row">
              <dt>所属项目</dt>
              <dd>{{ selectedProject?.name }}</dd>
            </div>
            <div class="confirm-list__row">
              <dt>研究模板</dt>
              <dd>通用研究模板（generic）</dd>
            </div>
            <div class="confirm-list__row">
              <dt>研究档位</dt>
              <dd>
                {{ tierLabel(selectedTier ?? 'standard') }}
                <span class="confirm-list__sub">
                  （上限 {{ TIER_METAS.find((m) => m.tier === selectedTier)?.subQuestionLimit ?? 5 }}
                  个子问题 ·
                  {{ formatNumber(TIER_METAS.find((m) => m.tier === selectedTier)?.tokenBudget ?? 150000) }}
                  tokens）
                </span>
              </dd>
            </div>
            <div class="confirm-list__row">
              <dt>研究问题</dt>
              <dd class="confirm-list__question">
                {{ question.trim() }}
              </dd>
            </div>
          </dl>
        </div>

        <!-- 启动 -->
        <div
          v-else
          class="step-body step-body--launch"
        >
          <h2>准备启动</h2>
          <p class="step-body__hint">
            启动后研究将进入六阶段流水线，可在指挥舱实时查看进度
          </p>
          <div class="launch-summary">
            <p><strong>项目：</strong>{{ selectedProject?.name }}</p>
            <p>
              <strong>档位：</strong>{{ tierLabel(selectedTier ?? 'standard') }}
            </p>
            <p class="launch-summary__question">
              {{ question.trim() }}
            </p>
          </div>
          <p
            v-if="submitError"
            class="launch-error"
            role="alert"
          >
            {{ submitError }}
          </p>
          <UiButton
            variant="primary"
            size="lg"
            :loading="submitting"
            @click="launch"
          >
            启动研究
          </UiButton>
        </div>
      </UiCard>

      <footer class="wizard-actions">
        <UiButton
          variant="ghost"
          @click="step === 0 ? cancel() : goPrev()"
        >
          {{ step === 0 ? '取消' : '上一步' }}
        </UiButton>
        <UiButton
          v-if="currentStepKey !== 'launch'"
          variant="primary"
          :disabled="!canGoNext"
          @click="goNext"
        >
          {{ currentStepKey === 'confirm' ? '去启动' : '下一步' }}
        </UiButton>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.wizard-view {
  flex: 1 1 auto;
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  padding: var(--space-8);
}

.wizard-view__head h1 {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  margin-bottom: var(--space-6);
}

.wizard-steps {
  display: flex;
  list-style: none;
  gap: var(--space-2);
  margin-bottom: var(--space-6);
  overflow-x: auto;
}

.wizard-steps__button {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  border: none;
  background: transparent;
  padding: var(--space-1) var(--space-2);
  font-size: var(--font-sm);
  color: var(--color-text-muted);
  cursor: default;
}

.wizard-steps__item.is-clickable .wizard-steps__button {
  cursor: pointer;
}

.wizard-steps__index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-border);
  font-size: var(--font-xs);
}

.wizard-steps__item.is-current .wizard-steps__index {
  background: var(--brand-500);
  border-color: var(--brand-500);
  color: #fff;
}

.wizard-steps__item.is-current .wizard-steps__label {
  color: var(--color-text-strong);
  font-weight: 600;
}

.wizard-steps__item.is-done .wizard-steps__index {
  border-color: var(--brand-500);
  color: var(--brand-700);
}

.wizard-panel {
  min-height: 320px;
}

.step-body h2 {
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
  margin-bottom: var(--space-2);
}

.step-body__hint {
  font-size: var(--font-sm);
  color: var(--color-text-muted);
  margin-bottom: var(--space-5);
}

.select-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}

.select-grid--single {
  grid-template-columns: 1fr;
}

.select-card {
  outline: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.select-card.is-selected {
  border-color: var(--brand-500);
  box-shadow: inset 0 0 0 1px var(--brand-500);
}

.select-card:focus-visible {
  border-color: var(--brand-500);
}

.select-card__title {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-strong);
}

.select-card__desc {
  flex: 1 1 auto;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.step-body__counter {
  margin-top: var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  text-align: right;
}

.step-body__counter.is-over {
  color: var(--danger-500);
}

.confirm-list {
  display: flex;
  flex-direction: column;
}

.confirm-list__row {
  display: flex;
  gap: var(--space-6);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border);
  font-size: var(--font-sm);
}

.confirm-list__row:first-child {
  padding-top: 0;
}

.confirm-list__row dt {
  width: 72px;
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.confirm-list__row dd {
  color: var(--color-text);
}

.confirm-list__sub {
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

.confirm-list__question {
  white-space: pre-wrap;
  word-break: break-word;
}

.step-body--launch {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.launch-summary {
  width: 100%;
  background: var(--neutral-50);
  border-radius: var(--radius-md);
  padding: var(--space-4) var(--space-5);
  margin-bottom: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--font-sm);
}

.launch-summary__question {
  color: var(--color-text-muted);
  white-space: pre-wrap;
  word-break: break-word;
}

.launch-error {
  color: var(--danger-500);
  font-size: var(--font-sm);
  margin-bottom: var(--space-3);
}

.wizard-actions {
  display: flex;
  justify-content: space-between;
  margin-top: var(--space-5);
}
</style>
