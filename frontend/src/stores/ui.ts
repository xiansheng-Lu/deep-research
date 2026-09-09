import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'

const THEME_KEY = 'themeMode'

function applyTheme(mode: ThemeMode): void {
  const prefersDark =
    typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  const resolved = mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode
  document.documentElement.dataset.theme = resolved
  document.documentElement.dataset.themeMode = mode
}

function loadThemeMode(): ThemeMode {
  try {
    const raw = localStorage.getItem(THEME_KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'system') return raw
  } catch {
    // localStorage 不可用时使用默认 system
  }
  return 'system'
}

// 全局 UI 偏好：主题 + 命令面板开关 + 助手面板开关
export const useUiStore = defineStore('ui', () => {
  const themeMode = ref<ThemeMode>(loadThemeMode())
  const commandPaletteOpen = ref(false)
  const assistantOpen = ref(false)

  function setThemeMode(mode: ThemeMode) {
    themeMode.value = mode
  }

  function toggleCommandPalette(next?: boolean) {
    commandPaletteOpen.value = next ?? !commandPaletteOpen.value
  }

  function toggleAssistant(next?: boolean) {
    assistantOpen.value = next ?? !assistantOpen.value
  }

  // 持久化主题偏好
  watch(
    themeMode,
    (mode) => {
      try {
        localStorage.setItem(THEME_KEY, mode)
      } catch {
        // localStorage 写入失败时静默
      }
      applyTheme(mode)
    },
    { immediate: true }
  )

  return {
    themeMode,
    commandPaletteOpen,
    assistantOpen,
    setThemeMode,
    toggleCommandPalette,
    toggleAssistant
  }
})
