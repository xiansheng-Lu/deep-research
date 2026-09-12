<script setup lang="ts">
// 全局助手面板（[前端详细设计 §12.6 M2]，对齐 M2-1 交接单 §3）
// 闲聊专属：不检索、不引用来源、不产生项目数据；可随时"改用深度研究"（force=research 回首页）。
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import UiButton from '@/components/ui/UiButton.vue'
import { useChat } from '@/composables/useChat'
import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const userId = session.user?.id ?? 'anonymous'

const { messages, sending, hasMessages, maxMessageChars, send, stop, resend, clear } = useChat(userId)

const draft = ref('')
const listEl = ref<HTMLElement | null>(null)
// 防止 q 参数在同一会话内重复触发（如来回导航）
const autoSendKey = ref<string | null>(null)

function scrollToBottom(): void {
  void nextTick(() => {
    const el = listEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

watch(
  () => messages.value.length,
  () => scrollToBottom()
)

onMounted(() => {
  const q = typeof route.query.q === 'string' ? route.query.q.trim() : ''
  if (q && autoSendKey.value !== q) {
    autoSendKey.value = q
    draft.value = ''
    void send(q)
  }
})

function onSend(): void {
  const text = draft.value.trim()
  if (!text || sending.value) return
  draft.value = ''
  void send(text)
}

function onEnterSend(event: KeyboardEvent): void {
  // Enter 发送，Shift+Enter 换行（与文本域常规约定一致）
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    onSend()
  }
}

// 改走深度研究：携带最后一条用户消息回首页并 force=research
function switchToResearch(): void {
  const lastUser = [...messages.value].reverse().find((m) => m.role === 'user')
  const text = lastUser?.content ?? draft.value.trim()
  void router.push({ path: '/home', query: text ? { q: text, force: 'research' } : {} })
}

function onClear(): void {
  clear()
}

const canSend = (): boolean => draft.value.trim().length > 0 && !sending.value
</script>

<template>
  <section class="assistant-view">
    <header class="assistant-view__head">
      <div>
        <h1 class="assistant-view__title">
          助手
        </h1>
        <p class="assistant-view__subtitle">
          直接问答，不联网检索、不产生研究项目；需要多源核验的问题请改用深度研究。
        </p>
      </div>
      <div class="assistant-view__head-actions">
        <UiButton
          variant="ghost"
          size="sm"
          :disabled="!hasMessages"
          @click="switchToResearch"
        >
          深度研究这个问题
        </UiButton>
        <UiButton
          variant="ghost"
          size="sm"
          :disabled="!hasMessages || sending"
          @click="onClear"
        >
          清空
        </UiButton>
      </div>
    </header>

    <div
      ref="listEl"
      class="assistant-view__messages"
    >
      <div
        v-if="!hasMessages"
        class="assistant-view__empty"
      >
        <p class="assistant-view__empty-title">
          有什么可以直接聊的？
        </p>
        <p class="assistant-view__empty-hint">
          例如「用通俗的话解释什么是向量数据库」。涉及实时信息或需要引用来源的问题，请回首页发起深度研究。
        </p>
      </div>

      <div
        v-for="msg in messages"
        :key="msg.id"
        class="assistant-view__msg"
        :class="`is-${msg.role}`"
      >
        <div class="assistant-view__bubble">
          <p class="assistant-view__bubble-text">
            {{ msg.content || (msg.status === 'streaming' ? '…' : '') }}
          </p>
          <span
            v-if="msg.status === 'streaming'"
            class="assistant-view__cursor"
            aria-hidden="true"
          >▍</span>
          <div
            v-if="msg.status === 'error'"
            class="assistant-view__error"
          >
            <span>{{ msg.errorText }}</span>
            <button
              type="button"
              class="assistant-view__retry"
              @click="resend(msg.id)"
            >
              重新发送
            </button>
          </div>
        </div>
      </div>
    </div>

    <footer class="assistant-view__composer">
      <textarea
        v-model="draft"
        class="assistant-view__input"
        rows="2"
        :maxlength="maxMessageChars"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        @keydown="onEnterSend"
      />
      <div class="assistant-view__composer-bar">
        <span class="assistant-view__counter">{{ draft.length }}/{{ maxMessageChars }}</span>
        <UiButton
          v-if="sending"
          variant="ghost"
          size="sm"
          @click="stop"
        >
          停止
        </UiButton>
        <UiButton
          v-else
          size="sm"
          :disabled="!canSend()"
          @click="onSend"
        >
          发送
        </UiButton>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.assistant-view {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 820px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-6) var(--space-4);
  min-height: 0;
}

.assistant-view__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.assistant-view__title {
  font-family: 'Newsreader', 'Noto Serif SC', Georgia, serif;
  font-size: var(--font-xl);
  font-weight: 600;
  color: var(--color-text-strong);
  margin-bottom: 4px;
}

.assistant-view__subtitle {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.assistant-view__head-actions {
  display: flex;
  gap: var(--space-2);
  flex-shrink: 0;
}

.assistant-view__messages {
  flex: 1 1 auto;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-2) var(--space-1) var(--space-4);
  min-height: 160px;
}

.assistant-view__empty {
  margin: auto;
  text-align: center;
  max-width: 420px;
}

.assistant-view__empty-title {
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: var(--space-2);
}

.assistant-view__empty-hint {
  font-size: var(--font-sm);
  color: var(--color-text-muted);
  line-height: 1.7;
}

.assistant-view__msg {
  display: flex;
}

.assistant-view__msg.is-user {
  justify-content: flex-end;
}

.assistant-view__bubble {
  max-width: 78%;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-lg);
  font-size: var(--font-sm);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.is-user .assistant-view__bubble {
  background: var(--brand-500);
  color: #fff;
  border-bottom-right-radius: var(--radius-sm);
}

.is-assistant .assistant-view__bubble {
  background: var(--neutral-100);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-bottom-left-radius: var(--radius-sm);
}

.assistant-view__bubble-text {
  display: inline;
}

.assistant-view__cursor {
  animation: assistant-blink 1s steps(2, start) infinite;
  margin-left: 2px;
  color: var(--brand-700);
}

@keyframes assistant-blink {
  to {
    visibility: hidden;
  }
}

.assistant-view__error {
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--danger-100, #fde2e2);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  color: var(--danger-500);
  font-size: var(--font-xs);
}

.assistant-view__retry {
  border: none;
  background: none;
  padding: 0;
  color: var(--danger-500);
  font-size: var(--font-xs);
  text-decoration: underline;
  cursor: pointer;
}

.assistant-view__composer {
  border-top: 1px solid var(--color-border);
  padding-top: var(--space-3);
}

.assistant-view__input {
  width: 100%;
  resize: none;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: var(--font-sm);
  font-family: inherit;
  line-height: 1.6;
}

.assistant-view__input:focus {
  outline: none;
  border-color: var(--brand-500);
}

.assistant-view__composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--space-2);
}

.assistant-view__counter {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
</style>
