<script setup lang="ts">
// 通用浮层定位组件（[前端详细设计 §6.1 M0 浮层 / §4.2 浮层底座抽象]）
// 基于 @floating-ui/dom 的 computePosition + autoUpdate
// 默认 placement: bottom-start；支持 click/hover/manual 三种 trigger
// 样式仅引用 tokens.css 的 CSS 变量
import {
  arrow as arrowMiddleware,
  autoUpdate,
  computePosition,
  flip,
  offset as offsetMiddleware,
  shift,
  type Placement
} from '@floating-ui/dom'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

type Trigger = 'click' | 'hover' | 'manual'

const props = withDefaults(
  defineProps<{
    modelValue?: boolean
    trigger?: Trigger
    placement?: Placement
    offset?: number
    closeOnEsc?: boolean
    closeOnMask?: boolean
    arrow?: boolean
    ariaLabel?: string
  }>(),
  {
    modelValue: undefined,
    trigger: 'click',
    placement: 'bottom-start',
    offset: 8,
    closeOnEsc: true,
    closeOnMask: true,
    arrow: false
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
}>()

const triggerRef = ref<HTMLElement | null>(null)
const popoverRef = ref<HTMLElement | null>(null)
const arrowRef = ref<HTMLElement | null>(null)

// 受控/非受控
const internalOpen = ref(false)
const open = computed(() => props.modelValue ?? internalOpen.value)

function setOpen(v: boolean): void {
  if (props.modelValue === undefined) internalOpen.value = v
  emit('update:modelValue', v)
}

let cleanupAuto: (() => void) | null = null

async function updatePosition(): Promise<void> {
  const trigger = triggerRef.value
  const popover = popoverRef.value
  if (!trigger || !popover) return
  const { x, y, middlewareData, placement: finalPlacement } = await computePosition(
    trigger,
    popover,
    {
      placement: props.placement,
      middleware: [
        offsetMiddleware(props.offset),
        flip(),
        shift({ padding: 8 }),
        ...(props.arrow ? [arrowMiddleware({ element: arrowRef.value! })] : [])
      ]
    }
  )
  Object.assign(popover.style, {
    left: `${x}px`,
    top: `${y}px`
  })
  if (props.arrow && arrowRef.value && middlewareData.arrow) {
    const { x: ax, y: ay } = middlewareData.arrow
    Object.assign(arrowRef.value.style, {
      left: ax != null ? `${ax}px` : '',
      top: ay != null ? `${ay}px` : ''
    })
    arrowRef.value.dataset.placement = finalPlacement
  }
  popover.dataset.placement = finalPlacement
}

watch(open, async (on) => {
  if (on) {
    await nextTick()
    if (!popoverRef.value || !triggerRef.value) return
    cleanupAuto = autoUpdate(triggerRef.value, popoverRef.value, updatePosition)
    await updatePosition()
    if (props.trigger === 'click' || props.trigger === 'manual') {
      if (props.closeOnMask) {
        // 下一帧注册，避免触发元素冒泡上来的 click 立刻关掉
        requestAnimationFrame(() => document.addEventListener('mousedown', onDocMouseDown))
      }
    }
    if (props.closeOnEsc) document.addEventListener('keydown', onKeydown)
  } else {
    cleanupAuto?.()
    cleanupAuto = null
    document.removeEventListener('mousedown', onDocMouseDown)
    document.removeEventListener('keydown', onKeydown)
  }
})

function onDocMouseDown(e: MouseEvent): void {
  const t = e.target as Node | null
  if (!t) return
  if (popoverRef.value?.contains(t)) return
  if (triggerRef.value?.contains(t)) return
  setOpen(false)
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    e.stopPropagation()
    setOpen(false)
  }
}

function onTriggerClick(): void {
  if (props.trigger === 'click') setOpen(!open.value)
}

let hoverTimer: number | null = null

function onTriggerEnter(): void {
  if (props.trigger !== 'hover') return
  if (hoverTimer !== null) window.clearTimeout(hoverTimer)
  setOpen(true)
}

function onTriggerLeave(): void {
  if (props.trigger !== 'hover') return
  if (hoverTimer !== null) window.clearTimeout(hoverTimer)
  hoverTimer = window.setTimeout(() => setOpen(false), 120)
}

function onPopoverEnter(): void {
  if (props.trigger !== 'hover') return
  if (hoverTimer !== null) window.clearTimeout(hoverTimer)
}

function onPopoverLeave(): void {
  if (props.trigger !== 'hover') return
  if (hoverTimer !== null) window.clearTimeout(hoverTimer)
  hoverTimer = window.setTimeout(() => setOpen(false), 120)
}

onBeforeUnmount(() => {
  cleanupAuto?.()
  document.removeEventListener('mousedown', onDocMouseDown)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <span
    class="u-popover-wrap"
    @mouseenter="onTriggerEnter"
    @mouseleave="onTriggerLeave"
  >
    <span
      ref="triggerRef"
      class="u-popover-trigger"
      @click="onTriggerClick"
    >
      <slot name="trigger" />
    </span>
    <Teleport to="body">
      <Transition name="u-popover">
        <div
          v-if="open"
          ref="popoverRef"
          class="u-popover"
          role="dialog"
          :aria-label="ariaLabel"
          @mouseenter="onPopoverEnter"
          @mouseleave="onPopoverLeave"
        >
          <span
            v-if="arrow"
            ref="arrowRef"
            class="u-popover__arrow"
            aria-hidden="true"
          />
          <div class="u-popover__body">
            <slot />
          </div>
        </div>
      </Transition>
    </Teleport>
  </span>
</template>

<style scoped>
.u-popover-wrap {
  display: inline-block;
}

.u-popover-trigger {
  display: inline-block;
}

.u-popover {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 1400;
  min-width: 160px;
  max-width: 420px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-popover);
  font-size: var(--font-sm);
  color: var(--color-text);
}

.u-popover__arrow {
  position: absolute;
  width: 8px;
  height: 8px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  transform: rotate(45deg);
}

.u-popover[data-placement^='top'] .u-popover__arrow {
  bottom: -5px;
  border-top: none;
  border-left: none;
}
.u-popover[data-placement^='bottom'] .u-popover__arrow {
  top: -5px;
  border-bottom: none;
  border-right: none;
}
.u-popover[data-placement^='left'] .u-popover__arrow {
  right: -5px;
  border-left: none;
  border-bottom: none;
}
.u-popover[data-placement^='right'] .u-popover__arrow {
  left: -5px;
  border-right: none;
  border-top: none;
}

.u-popover__body {
  padding: var(--space-3);
}

.u-popover-enter-from,
.u-popover-leave-to {
  opacity: 0;
  transform: scale(0.96);
}

.u-popover-enter-active,
.u-popover-leave-active {
  transition:
    opacity var(--motion-fast) var(--ease-out),
    transform var(--motion-fast) var(--ease-out);
}
</style>
