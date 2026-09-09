// 主题组合式：包装 ui store 中的 themeMode 暴露切换/读取入口
// 实际持久化与 DOM 写入由 store 内部完成（[前端详细设计 §5.2]）
import { storeToRefs } from 'pinia'
import { useUiStore } from '@/stores/ui'

export function useDarkMode() {
  const ui = useUiStore()
  const { themeMode } = storeToRefs(ui)
  return {
    themeMode,
    setThemeMode: ui.setThemeMode,
    cycleMode() {
      const next = themeMode.value === 'light' ? 'dark' : themeMode.value === 'dark' ? 'system' : 'light'
      ui.setThemeMode(next)
    }
  }
}
