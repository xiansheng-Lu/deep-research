<script setup lang="ts">
// 表单输入组件（[前端详细设计 §6.1 M0]）
// 实现口径：modelValue / type / label / hint / error / autofocus / disabled / size
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
type Size = 'sm' | 'md' | 'lg'

withDefaults(
  defineProps<{
    modelValue: string | number
    type?: 'text' | 'password' | 'email' | 'number' | 'url' | 'tel' | 'search'
    label?: string
    hint?: string
    error?: string
    placeholder?: string
    disabled?: boolean
    readonly?: boolean
    autofocus?: boolean
    size?: Size
    maxlength?: number
  }>(),
  {
    type: 'text',
    disabled: false,
    readonly: false,
    autofocus: false,
    size: 'md'
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'focus', ev: FocusEvent): void
  (e: 'blur', ev: FocusEvent): void
  (e: 'enter', ev: KeyboardEvent): void
}>()

function onInput(ev: Event) {
  const target = ev.target as HTMLInputElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <label class="u-input" :class="[`u-input--${size}`, { 'is-error': !!error, 'is-disabled': disabled }]">
    <span v-if="label" class="u-input__label">{{ label }}</span>
    <span class="u-input__field">
      <input
        class="u-input__el"
        :type="type"
        :value="modelValue"
        :placeholder="placeholder"
        :disabled="disabled"
        :readonly="readonly"
        :maxlength="maxlength"
        :autofocus="autofocus"
        @input="onInput"
        @focus="(ev) => $emit('focus', ev)"
        @blur="(ev) => $emit('blur', ev)"
        @keyup.enter="(ev) => $emit('enter', ev)"
      />
    </span>
    <span v-if="error" class="u-input__msg u-input__msg--error">{{ error }}</span>
    <span v-else-if="hint" class="u-input__msg">{{ hint }}</span>
  </label>
</template>

<style scoped>
.u-input {
  display: inline-flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--font-sm);
  color: var(--color-text);
}

.u-input__label {
  font-size: var(--font-xs);
  color: var(--color-text-strong);
  font-weight: 500;
}

.u-input__field {
  display: flex;
  align-items: center;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  transition: border-color var(--motion-base) var(--ease-out);
}

.u-input__field:focus-within {
  border-color: var(--brand-500);
  outline: 2px solid var(--brand-50);
  outline-offset: 0;
}

.u-input.is-error .u-input__field {
  border-color: var(--danger-500);
}

.u-input.is-disabled .u-input__field {
  background: var(--neutral-100);
  cursor: not-allowed;
}

.u-input__el {
  flex: 1;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  padding: 0 var(--space-3);
  font-size: inherit;
  color: inherit;
}

.u-input__el:disabled {
  cursor: not-allowed;
  color: var(--color-text-muted);
}

.u-input--sm .u-input__el {
  height: 28px;
}
.u-input--md .u-input__el {
  height: 36px;
}
.u-input--lg .u-input__el {
  height: 44px;
  font-size: var(--font-base);
}

.u-input__msg {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
.u-input__msg--error {
  color: var(--danger-500);
}
</style>