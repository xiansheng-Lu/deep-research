<script setup lang="ts">
// 全局 Toast 宿主（[前端详细设计 §6.1 M0 反馈]）
// 订阅 toast 单例队列并渲染；Teleport 到 body，避免被局部 overflow 裁剪
// 由 App.vue 挂载一次即可；同窗口内复用同一实例
import { computed } from 'vue'
import { toast } from '@/services/toast/toast'
import UiToast from './UiToast.vue'

// 订阅 reactive 队列
const items = computed(() => toast.snapshot())

function onClose(id: string): void {
  toast.dismiss(id)
}
</script>

<template>
  <Teleport to="body">
    <div class="u-toast-host" aria-live="polite" aria-relevant="additions">
      <TransitionGroup name="u-toast-stack" tag="div" class="u-toast-stack">
        <UiToast v-for="it in items" :key="it.id" :item="it" @close="onClose" />
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.u-toast-host {
  position: fixed;
  top: var(--space-6);
  right: var(--space-6);
  z-index: 2000;
  pointer-events: none;
}

.u-toast-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* 进入/退出动画：仅位移 + 透明度，避免重排抖动 */
.u-toast-stack-enter-from,
.u-toast-stack-leave-to {
  opacity: 0;
  transform: translateX(16px);
}

.u-toast-stack-enter-active,
.u-toast-stack-leave-active {
  transition:
    opacity 180ms ease-out,
    transform 180ms ease-out;
}
</style>
