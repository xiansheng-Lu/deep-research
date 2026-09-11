<script setup lang="ts">
// 文字提示组件（[前端详细设计 §6.1 M0 浮层]）
// 实现口径：content / placement / delay=400ms / 支持纯文本或富内容插槽
// 基于 @floating-ui/dom 定位；hover 触发，离开后延迟消失
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
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    content?: string
    placement?: Placement
    delay?: number
  }>(),
  {
    placement: 'top',
    delay: 400
  }
)

const triggerRef = ref<HTMLElement | null>(null)
const tooltipRef = ref<HTMLElement | null>(null)
const arrowRef = ref<HTMLElement | null>(null)
const open = ref(false)

let cleanupAuto: (() => void) | null = null
let showTimer: number | null = null
let hideTimer: number | null = null

async function updatePosition(): Promise<void> {
  const trigger = triggerRef.value
  const tip = tooltipRef.value
  if (!trigger || !tip) return
  const { x, y, middlewareData, placement: finalPlacement } = await computePosition(
    trigger,
    tip,
    {
      placement: props.placement,
      middleware: [
        offsetMiddleware(6),
        flip(),
        shift({ padding: 8 }),
        arrowMiddleware({ element: arrowRef.value! })
      ]
    }
  )
  Object.assign(tip.style, {
    left: `${x}px`,
    top: `${y}px`
  })
  if (arrowRef.value && middlewareData.arrow) {
    const { x: ax, y: ay } = middlewareData.arrow
    Object.assign(arrowRef.value.style, {
      left: ax != null ? `${ax}px` : '',
      top: ay != null ? `${ay}px` : ''
    })
  }
  tip.dataset.placement = finalPlacement
}

watch(open, async (on) => {
  if (on) {
    await nextTick()
    if (!tooltipRef.value || !triggerRef.value) return
    cleanupAuto = autoUpdate(triggerRef.value, tooltipRef.value, updatePosition)
    await updatePosition()
  } else {
    cleanupAuto?.()
    cleanupAuto = null
  }
})

function show(): void {
  if (hideTimer !== null) {
    window.clearTimeout(hideTimer)
    hideTimer = null
  }
  if (open.value) return
  if (showTimer !== null) window.clearTimeout(showTimer)
  showTimer = window.setTimeout(() => {
    open.value = true
  }, props.delay)
}

function hide(): void {
  if (showTimer !== null) {
    window.clearTimeout(showTimer)
    showTimer = null
  }
  if (hideTimer !== null) window.clearTimeout(hideTimer)
  hideTimer = window.setTimeout(() => {
    open.value = false
  }, 80)
}

onBeforeUnmount(() => {
  cleanupAuto?.()
  if (showTimer !== null) window.clearTimeout(showTimer)
  if (hideTimer !== null) window.clearTimeout(hideTimer)
})
</script>

<template>
  <span
    ref="triggerRef"
    class="u-tooltip-trigger"
    @mouseenter="show"
    @mouseleave="hide"
    @focusin="show"
    @focusout="hide"
  >
    <slot />
  </span>
  <Teleport to="body">
    <Transition name="u-tooltip">
      <div
        v-if="open"
        ref="tooltipRef"
        class="u-tooltip"
        role="tooltip"
      >
        <span
          ref="arrowRef"
          class="u-tooltip__arrow"
          aria-hidden="true"
        />
        <span v-if="content">{{ content }}</span>
        <slot v-else />
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.u-tooltip-trigger {
  display: inline-block;
}

.u-tooltip {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 1450;
  max-width: 280px;
  padding: var(--space-2) var(--space-3);
  background: var(--neutral-900);
  color: var(--neutral-0);
  font-size: var(--font-xs);
  line-height: 1.4;
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-popover);
  word-break: break-word;
}

.u-tooltip__arrow {
  position: absolute;
  width: 6px;
  height: 6px;
  background: var(--neutral-900);
  transform: rotate(45deg);
}

.u-tooltip[data-placement^='top'] .u-tooltip__arrow {
  bottom: -3px;
}
.u-tooltip[data-placement^='bottom'] .u-tooltip__arrow {
  top: -3px;
}
.u-tooltip[data-placement^='left'] .u-tooltip__arrow {
  right: -3px;
}
.u-tooltip[data-placement^='right'] .u-tooltip__arrow {
  left: -3px;
}

.u-tooltip-enter-from,
.u-tooltip-leave-to {
  opacity: 0;
}

.u-tooltip-enter-active,
.u-tooltip-leave-active {
  transition: opacity var(--motion-fast) var(--ease-out);
}
</style>
