<script setup lang="ts">
// 滑块组件（[前端详细设计 §6.1 M0]）
// 实现口径：modelValue / min / max / step / ticks[] / disabled（档位/预算）
// 样式仅引用 tokens.css 的 CSS 变量，禁止硬编码色值
type Tick = { value: number; label?: string }

const props = withDefaults(
  defineProps<{
    modelValue: number
    min?: number
    max?: number
    step?: number
    ticks?: Tick[]
    disabled?: boolean
  }>(),
  {
    min: 0,
    max: 100,
    step: 1,
    disabled: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: number): void
  (e: 'change', value: number): void
}>()

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v))
}

function onInput(ev: Event) {
  const target = ev.target as HTMLInputElement
  const value = clamp(Number(target.value), props.min, props.max)
  emit('update:modelValue', value)
  emit('change', value)
}
</script>

<template>
  <div
    class="u-slider"
    :class="{ 'is-disabled': disabled }"
  >
    <input
      class="u-slider__el"
      type="range"
      :value="modelValue"
      :min="min"
      :max="max"
      :step="step"
      :disabled="disabled"
      @input="onInput"
    >
    <div
      v-if="ticks && ticks.length"
      class="u-slider__ticks"
    >
      <span
        v-for="tick in ticks"
        :key="tick.value"
        class="u-slider__tick"
        :class="{ 'is-active': tick.value === modelValue }"
      >
        {{ tick.label ?? tick.value }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.u-slider {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--font-sm);
  color: var(--color-text);
}

.u-slider.is-disabled {
  opacity: 0.6;
  pointer-events: none;
}

.u-slider__el {
  appearance: none;
  -webkit-appearance: none;
  width: 100%;
  height: 4px;
  border-radius: var(--radius-full);
  background: var(--neutral-200);
  outline: 0;
  cursor: pointer;
}

.u-slider__el::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 16px;
  height: 16px;
  border-radius: var(--radius-full);
  background: var(--brand-500);
  border: 2px solid var(--neutral-0);
  box-shadow: 0 1px 2px rgba(15, 17, 23, 0.2);
  cursor: grab;
}

.u-slider__el::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border-radius: var(--radius-full);
  background: var(--brand-500);
  border: 2px solid var(--neutral-0);
  box-shadow: 0 1px 2px rgba(15, 17, 23, 0.2);
  cursor: grab;
}

.u-slider__ticks {
  display: flex;
  justify-content: space-between;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.u-slider__tick.is-active {
  color: var(--brand-500);
  font-weight: 600;
}
</style>