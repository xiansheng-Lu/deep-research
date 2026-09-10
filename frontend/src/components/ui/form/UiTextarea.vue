<script setup lang="ts">
// 多行文本组件（[前端详细设计 §6.1 M0]）
// 实现口径：modelValue / rows / label / hint / error / autosize
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
withDefaults(
  defineProps<{
    modelValue: string
    rows?: number
    label?: string
    hint?: string
    error?: string
    placeholder?: string
    disabled?: boolean
    readonly?: boolean
    maxlength?: number
    autosize?: boolean
  }>(),
  {
    rows: 4,
    disabled: false,
    readonly: false,
    autosize: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'focus', ev: FocusEvent): void
  (e: 'blur', ev: FocusEvent): void
}>()

function onInput(ev: Event) {
  const target = ev.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <label class="u-textarea" :class="{ 'is-error': !!error, 'is-disabled': disabled }">
    <span v-if="label" class="u-textarea__label">{{ label }}</span>
    <span class="u-textarea__field">
      <textarea
        class="u-textarea__el"
        :value="modelValue"
        :rows="rows"
        :placeholder="placeholder"
        :disabled="disabled"
        :readonly="readonly"
        :maxlength="maxlength"
        @input="onInput"
        @focus="(ev) => $emit('focus', ev)"
        @blur="(ev) => $emit('blur', ev)"
      />
    </span>
    <span v-if="error" class="u-textarea__msg u-textarea__msg--error">{{ error }}</span>
    <span v-else-if="hint" class="u-textarea__msg">{{ hint }}</span>
  </label>
</template>

<style scoped>
.u-textarea {
  display: inline-flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--font-sm);
  color: var(--color-text);
  width: 100%;
}

.u-textarea__label {
  font-size: var(--font-xs);
  color: var(--color-text-strong);
  font-weight: 500;
}

.u-textarea__field {
  display: flex;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  transition: border-color var(--motion-base) var(--ease-out);
}

.u-textarea__field:focus-within {
  border-color: var(--brand-500);
  outline: 2px solid var(--brand-50);
  outline-offset: 0;
}

.u-textarea.is-error .u-textarea__field {
  border-color: var(--danger-500);
}

.u-textarea.is-disabled .u-textarea__field {
  background: var(--neutral-100);
  cursor: not-allowed;
}

.u-textarea__el {
  flex: 1;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  padding: var(--space-2) var(--space-3);
  font-size: inherit;
  color: inherit;
  font-family: inherit;
  resize: vertical;
  min-height: 80px;
}

.u-textarea__el:disabled {
  cursor: not-allowed;
  color: var(--color-text-muted);
}

.u-textarea__msg {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
.u-textarea__msg--error {
  color: var(--danger-500);
}
</style>