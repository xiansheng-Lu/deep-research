<script setup lang="ts">
// 项目列表（[前端详细设计 §12.1 M1]）
// 卡片网格 + 新建项目对话框；加载/空/错误三态齐备
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { listProjects, createProject } from '@/services/api/projects'
import type { ApiError } from '@/services/http/error'
import type { ProjectResponse, RunTier } from '@/services/api/types'
import { TIER_METAS } from '@/services/domain/tiers'
import { tierLabel } from '@/services/i18n/zh-CN'
import { formatDateTime, formatNumber } from '@/utils/format'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiInput from '@/components/ui/form/UiInput.vue'
import UiTextarea from '@/components/ui/form/UiTextarea.vue'
import UiRadio from '@/components/ui/form/UiRadio.vue'
import UiDialog from '@/components/ui/overlay/UiDialog.vue'
import UiBadge from '@/components/ui/feedback/UiBadge.vue'
import UiEmpty from '@/components/ui/feedback/UiEmpty.vue'
import UiErrorState from '@/components/ui/feedback/UiErrorState.vue'
import UiSkeleton from '@/components/ui/feedback/UiSkeleton.vue'

const router = useRouter()
const projectStore = useProjectStore()

const loading = ref(false)
const loadError = ref<ApiError | null>(null)

async function load(): Promise<void> {
  // WP-17：错误卡自动重试期间保留旧错误与倒计时，不回退骨架屏，
  // 避免卡片被卸载重建导致退避计数归零；成功后再清错渲染列表
  const retrying = loadError.value !== null
  if (!retrying) loading.value = true
  try {
    projectStore.setProjects(await listProjects())
    loadError.value = null
  } catch (err) {
    loadError.value = err as ApiError
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ─── 新建项目对话框 ───

const dialogOpen = ref(false)
const submitting = ref(false)
const formName = ref('')
const formDescription = ref('')
const formTier = ref<RunTier>('standard')
const nameError = ref('')
const submitError = ref('')

function openCreate(): void {
  formName.value = ''
  formDescription.value = ''
  formTier.value = 'standard'
  nameError.value = ''
  submitError.value = ''
  dialogOpen.value = true
}

async function submitCreate(): Promise<void> {
  if (submitting.value) return
  const trimmedName = formName.value.trim()
  nameError.value = ''
  submitError.value = ''
  if (trimmedName.length < 1 || trimmedName.length > 128) {
    nameError.value = '项目名称长度需在 1-128 个字符之间'
    return
  }
  submitting.value = true
  try {
    const description = formDescription.value.trim()
    const created: ProjectResponse = await createProject({
      name: trimmedName,
      description: description ? description : null,
      default_tier: formTier.value
    })
    projectStore.upsertProject(created)
    dialogOpen.value = false
    router.push(`/projects/${created.id}/tasks`)
  } catch (err) {
    submitError.value = (err as ApiError).detail || '项目创建失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

function openTasks(projectId: string): void {
  router.push(`/projects/${projectId}/tasks`)
}
</script>

<template>
  <section class="project-list-view">
    <header class="project-list-view__head">
      <h1>项目</h1>
      <UiButton
        variant="primary"
        @click="openCreate"
      >
        新建项目
      </UiButton>
    </header>

    <UiErrorState
      v-if="loadError"
      :error="loadError"
      auto-retry
      @retry="load"
    />

    <div
      v-else-if="loading"
      class="project-grid"
    >
      <UiCard
        v-for="i in 6"
        :key="i"
        class="project-card-skeleton"
      >
        <UiSkeleton
          width="55%"
          height="18px"
        />
        <UiSkeleton
          width="90%"
          height="12px"
        />
        <UiSkeleton
          width="40%"
          height="18px"
        />
      </UiCard>
    </div>

    <UiEmpty
      v-else-if="projectStore.projects.length === 0"
      title="还没有项目"
      hint="创建第一个项目，开始发起深度研究"
    >
      <template #action>
        <UiButton
          variant="primary"
          @click="openCreate"
        >
          新建项目
        </UiButton>
      </template>
    </UiEmpty>

    <div
      v-else
      class="project-grid"
    >
      <UiCard
        v-for="project in projectStore.projects"
        :key="project.id"
        interactive
        class="project-card"
        @click="openTasks(project.id)"
        @keydown.enter="openTasks(project.id)"
      >
        <h2 class="project-card__name">
          {{ project.name }}
        </h2>
        <p class="project-card__desc">
          {{ project.description || '暂无描述' }}
        </p>
        <div class="project-card__meta">
          <UiBadge variant="brand">
            {{ tierLabel(project.default_tier) }}
          </UiBadge>
          <span class="project-card__time">{{ formatDateTime(project.created_at) }}</span>
        </div>
      </UiCard>
    </div>

    <UiDialog
      v-model="dialogOpen"
      title="新建项目"
    >
      <div class="create-form">
        <UiInput
          v-model="formName"
          label="项目名称"
          placeholder="1-128 个字符"
          :maxlength="128"
          :error="nameError"
        />
        <UiTextarea
          v-model="formDescription"
          label="项目描述（可选）"
          :rows="3"
          placeholder="简要说明这个项目关注的研究方向"
        />
        <fieldset class="create-form__tiers">
          <legend>默认研究档位</legend>
          <UiRadio
            v-for="meta in TIER_METAS"
            :key="meta.tier"
            :model-value="formTier"
            :value="meta.tier"
            name="default-tier"
            class="create-form__tier"
            @update:model-value="(v) => (formTier = v as RunTier)"
          >
            <span class="tier-option">
              <span class="tier-option__name">{{ tierLabel(meta.tier) }}</span>
              <span class="tier-option__hint">
                上限 {{ meta.subQuestionLimit }} 个子问题 · {{ formatNumber(meta.tokenBudget) }} tokens
              </span>
            </span>
          </UiRadio>
        </fieldset>
        <p
          v-if="submitError"
          class="create-form__error"
          role="alert"
        >
          {{ submitError }}
        </p>
      </div>
      <template #footer>
        <UiButton
          variant="ghost"
          @click="dialogOpen = false"
        >
          取消
        </UiButton>
        <UiButton
          variant="primary"
          :loading="submitting"
          @click="submitCreate"
        >
          创建
        </UiButton>
      </template>
    </UiDialog>
  </section>
</template>

<style scoped>
.project-list-view {
  flex: 1 1 auto;
  width: 100%;
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--space-8);
}

.project-list-view__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-6);
}

.project-list-view__head h1 {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-4);
}

.project-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  outline: none;
}

.project-card:focus-visible {
  border-color: var(--brand-500);
}

.project-card__name {
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-strong);
}

.project-card__desc {
  flex: 1 1 auto;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.project-card__meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.project-card__time {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.project-card-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.create-form__tiers {
  border: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.create-form__tiers legend {
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-text);
  margin-bottom: var(--space-1);
  padding: 0;
}

.create-form__tier {
  width: 100%;
}

.tier-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tier-option__name {
  font-size: var(--font-sm);
  font-weight: 500;
}

.tier-option__hint {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.create-form__error {
  color: var(--danger-500);
  font-size: var(--font-sm);
}
</style>
