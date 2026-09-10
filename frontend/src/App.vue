<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import UiToastHost from '@/components/ui/feedback/UiToastHost.vue'

// 在应用挂载后绑定系统主题变化监听；仅在 themeMode === 'system' 时生效
const ui = useUiStore()

onMounted(() => {
  if (!window.matchMedia) return
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', () => {
    if (ui.themeMode === 'system') {
      document.documentElement.dataset.theme = media.matches ? 'dark' : 'light'
    }
  })
})
</script>

<template>
  <RouterView />
  <UiToastHost />
</template>
