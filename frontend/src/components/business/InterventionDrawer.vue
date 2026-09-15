<script setup lang="ts">
// 统一用户介入抽屉（[前端详细设计 §10.1/§11.3 M2]，WP-15）
// 复用 M0 RightDrawer（UiDrawer）单例承载两类受控介入：
// - clarify：阶段1 澄清挂起，内嵌 ClarificationCard，答案经 resume 通道提交；
// - followup：阶段3 证据检索中追加追问，经 intervene {ask_followup} 提交。
// 纯编排壳：不直接发请求，提交事件交页面走 useRunStream.actions；
// 提交中锁定遮罩/ESC 关闭，防止提交中途丢上下文。
import { ref, watch } from 'vue'
import UiDrawer from '@/components/ui/overlay/UiDrawer.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiRadio from '@/components/ui/form/UiRadio.vue'
import UiTextarea from '@/components/ui/form/UiTextarea.vue'
import ClarificationCard from '@/components/business/ClarificationCard.vue'
import type { SubQuestionResponse } from '@/services/api/types'
import type { InterruptRequestedPayload } from '@/services/realtime/types'

export type InterventionMode = 'clarify' | 'followup'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    mode: InterventionMode
    // clarify 模式数据（interrupt.requested 帧 payload + stage/receivedAt）
    interrupt?: (InterruptRequestedPayload & { stage?: string; receivedAt?: number }) | null
    // followup 模式可选目标（证据检索阶段的子问题）
    subQuestions?: SubQuestionResponse[]
    submitting?: boolean
  }>(),
  {
    interrupt: null,
    subQuestions: () => [],
    submitting: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'submit-answers', answers: Record<string, string>): void
  (e: 'submit-followup', payload: { subQuestionId: string | null; question: string }): void
}>()

// ─── 追问表单态（每次打开重置）───

const followupQuestion = ref('')
const followupSqId = ref<string>('')

watch(
  () => props.modelValue,
  (open) => {
    if (open && props.mode === 'followup') {
      followupQuestion.value = ''
      // 默认绑定当前唯一/首个进行中的子问题；多个时由用户显式选择
      const running = props.subQuestions.find((item) => item.status === 'running')
      followupSqId.value = running?.id ?? ''
    }
  }
)

const followupValid = () => followupQuestion.value.trim().length > 0

function submitFollowup(): void {
  if (props.submitting || !followupValid()) return
  emit('submit-followup', {
    subQuestionId: followupSqId.value || null,
    question: followupQuestion.value.trim()
  })
}

function close(open: boolean): void {
  emit('update:modelValue', open)
}
</script>

<template>
  <UiDrawer
    :model-value="modelValue"
    size="md"
    :persistent="submitting"
    :close-on-mask="!submitting"
    :close-on-esc="!submitting"
    :title="mode === 'clarify' ? '澄清研究边界' : '追加追问'"
    @update:model-value="close"
  >
    <!-- 澄清模式：阻塞介入，提交前不可省略 -->
    <ClarificationCard
      v-if="mode === 'clarify' && interrupt"
      :questions="interrupt.questions"
      :defaults="interrupt.defaults"
      :expires-in-seconds="interrupt.expires_in_seconds"
      :received-at="interrupt.receivedAt"
      :submitting="submitting"
      @submit="(answers) => emit('submit-answers', answers)"
    />
    <p
      v-else-if="mode === 'clarify'"
      class="intervention-drawer__hint"
    >
      未获取到本次澄清问题。请保持研究页面打开以接收澄清问题；若已超过作答时限，系统将按默认假设继续推进。
    </p>

    <!-- 追问模式：证据检索阶段补充检索方向 -->
    <div
      v-else
      class="followup-form"
    >
      <p class="followup-form__lead">
        在证据检索阶段补充研究方向，系统会将追问纳入当前子问题的检索与分析。
      </p>

      <fieldset
        v-if="subQuestions.length > 0"
        class="followup-form__sqs"
      >
        <legend class="followup-form__legend">
          关联子问题
        </legend>
        <UiRadio
          model-value=""
          value=""
          name="followup-sq"
          class="followup-form__sq"
          @update:model-value="followupSqId = String($event)"
        >
          不针对特定子问题
        </UiRadio>
        <UiRadio
          v-for="(item, index) in subQuestions"
          :key="item.id"
          :model-value="followupSqId"
          :value="item.id"
          name="followup-sq"
          class="followup-form__sq"
          @update:model-value="followupSqId = String($event)"
        >
          子问题 {{ index + 1 }}：{{ item.question }}
        </UiRadio>
      </fieldset>

      <UiTextarea
        v-model="followupQuestion"
        :rows="5"
        label="追问内容"
        :maxlength="500"
        placeholder="例如：补充关注方案在混合检索场景下的最新实践"
        hint="最多 500 字"
      />
    </div>

    <template
      v-if="mode === 'followup'"
      #footer
    >
      <UiButton
        variant="secondary"
        size="sm"
        :disabled="submitting"
        @click="emit('update:modelValue', false)"
      >
        取消
      </UiButton>
      <UiButton
        size="sm"
        :loading="submitting"
        :disabled="!followupValid()"
        @click="submitFollowup"
      >
        提交追问
      </UiButton>
    </template>
  </UiDrawer>
</template>

<style scoped>
.intervention-drawer__hint {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.7;
  color: var(--color-text-muted);
}

.followup-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.followup-form__lead {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.6;
  color: var(--color-text-muted);
}

.followup-form__sqs {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.followup-form__legend {
  padding: 0 var(--space-1);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.followup-form__sq {
  font-size: var(--font-sm);
  line-height: 1.5;
}
</style>
