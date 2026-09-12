<script setup lang="ts">
// 澄清问题卡（[前端详细设计 §11.3 介入态 / 契约草案 §4.1]，WP-15）
// 按 interrupt.requested.payload.questions[] 渲染 2-4 道单选；
// 初始答案取推荐项（recommended 下标），无推荐时回退 defaults 中同键值；
// expires_in_seconds 倒计时归零后转只读，标注「已按默认假设」，不再允许提交（与文档口径一致）。
// 纯受控业务组件：不发请求，提交答案经 emit('submit') 交页面走 resume 通道。
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import type { ClarificationQuestion } from '@/services/realtime/types'
import UiRadio from '@/components/ui/form/UiRadio.vue'
import UiButton from '@/components/ui/UiButton.vue'
import { formatDuration } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    questions: ClarificationQuestion[]
    defaults?: Record<string, string>
    // 服务端允许的作答时长（秒）
    expiresInSeconds: number
    // interrupt 帧到达本机的时间戳；不传入时以组件挂载时刻起算
    receivedAt?: number
    submitting?: boolean
  }>(),
  {
    defaults: () => ({}),
    receivedAt: undefined,
    submitting: false
  }
)

const emit = defineEmits<{
  (e: 'submit', answers: Record<string, string>): void
}>()

// 题项当前答案（key → 选项文本）
const answers = reactive<Record<string, string>>({})

function initialAnswer(question: ClarificationQuestion): string {
  if (question.recommended !== null && question.options[question.recommended] !== undefined) {
    return question.options[question.recommended]
  }
  const fallback = props.defaults[question.key]
  if (fallback !== undefined && question.options.includes(fallback)) return fallback
  return ''
}

function resetAnswers(): void {
  for (const key of Object.keys(answers)) delete answers[key]
  for (const question of props.questions) {
    answers[question.key] = initialAnswer(question)
  }
}

watch(() => props.questions, resetAnswers, { immediate: true })

// ─── 超时倒计时 ───

const nowTs = ref(Date.now())
const tickTimer = window.setInterval(() => {
  nowTs.value = Date.now()
}, 1000)
onUnmounted(() => window.clearInterval(tickTimer))

const remainingSeconds = computed(() => {
  const base = props.receivedAt ?? nowTs.value
  return Math.max(0, props.expiresInSeconds - Math.floor((nowTs.value - base) / 1000))
})

const expired = computed(() => remainingSeconds.value <= 0)

// 每题均有答案才允许提交
const allAnswered = computed(() =>
  props.questions.every((question) => typeof answers[question.key] === 'string' && answers[question.key] !== '')
)

// 超时后系统采用的假设口径文案：逐题展示推荐项或默认值
const assumedAnswers = computed(() =>
  props.questions.map((question) => {
    const initial = initialAnswer(question)
    return { key: question.key, text: question.text, value: initial || '未指定' }
  })
)

function onSubmit(): void {
  if (props.submitting || expired.value || !allAnswered.value) return
  const payload: Record<string, string> = {}
  for (const question of props.questions) payload[question.key] = answers[question.key]
  emit('submit', payload)
}
</script>

<template>
  <div class="clarify-card">
    <p class="clarify-card__lead">
      研究继续前需要确认以下问题，系统将按你的选择收窄研究范围。
    </p>

    <ul class="clarify-card__list">
      <li
        v-for="(question, index) in questions"
        :key="question.key"
        class="clarify-card__item"
      >
        <p class="clarify-card__question">
          <span class="clarify-card__no">{{ index + 1 }}</span>
          {{ question.text }}
        </p>
        <div
          class="clarify-card__options"
          :class="{ 'is-readonly': expired }"
        >
          <UiRadio
            v-for="option in question.options"
            :key="option"
            :model-value="answers[question.key]"
            :value="option"
            :name="`clarify-${question.key}`"
            :disabled="expired"
            class="clarify-card__option"
            @update:model-value="(value) => (answers[question.key] = String(value))"
          >
            {{ option }}
          </UiRadio>
        </div>
      </li>
    </ul>

    <div
      v-if="!expired"
      class="clarify-card__footer"
    >
      <span class="clarify-card__countdown">
        剩余作答时间 {{ formatDuration(remainingSeconds) }}
      </span>
      <UiButton
        size="sm"
        :loading="submitting"
        :disabled="!allAnswered"
        @click="onSubmit"
      >
        提交答案并继续
      </UiButton>
    </div>

    <div
      v-else
      class="clarify-card__expired"
      role="status"
    >
      <p class="clarify-card__expired-title">
        已超过作答时限
      </p>
      <p class="clarify-card__expired-text">
        系统将按以下默认假设继续推进研究：
      </p>
      <ul class="clarify-card__assumed">
        <li
          v-for="item in assumedAnswers"
          :key="item.key"
        >
          {{ item.text }}：{{ item.value }}
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.clarify-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.clarify-card__lead {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.6;
  color: var(--color-text-muted);
}

.clarify-card__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.clarify-card__question {
  margin: 0 0 var(--space-2);
  font-size: var(--font-sm);
  font-weight: 500;
  line-height: 1.6;
  color: var(--color-text-strong);
  display: flex;
  gap: var(--space-2);
}

.clarify-card__no {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: var(--radius-sm);
  background: var(--brand-50);
  color: var(--brand-700);
  font-size: var(--font-xs);
}

.clarify-card__options {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-left: 26px;
}

.clarify-card__options.is-readonly {
  opacity: 0.55;
}

.clarify-card__option {
  font-size: var(--font-sm);
}

.clarify-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.clarify-card__countdown {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

.clarify-card__expired {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--neutral-100);
}

.clarify-card__expired-title {
  margin: 0 0 var(--space-1);
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-text-strong);
}

.clarify-card__expired-text {
  margin: 0 0 var(--space-1);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.clarify-card__assumed {
  margin: 0;
  padding-left: var(--space-4);
  font-size: var(--font-xs);
  line-height: 1.7;
  color: var(--color-text);
}
</style>
