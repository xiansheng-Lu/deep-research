// 焦点困于工具（[前端详细设计 §6.2 自研小实现]）
// 适用于 UiDialog / UiDrawer 等模态浮层
// 启用后：保存激活元素 → 监听 keydown → Tab/Shift+Tab 在容器内循环
// 停用时：恢复激活元素；避免遗留焦点
import { onBeforeUnmount, watch, type Ref } from 'vue'

// 容器内可聚焦元素选择器
const FOCUSABLE = [
  'a[href]',
  'area[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  'audio[controls]',
  'video[controls]',
  'iframe',
  'object',
  'embed',
  '[contenteditable]:not([contenteditable="false"])'
].join(',')

function getFocusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute('disabled') && el.tabIndex !== -1 && isVisible(el)
  )
}

function isVisible(el: HTMLElement): boolean {
  // 通过 offsetParent/rect 粗略判断可见
  if (el.offsetParent === null && getComputedStyle(el).position !== 'fixed') {
    return false
  }
  const rect = el.getBoundingClientRect()
  return rect.width > 0 && rect.height > 0
}

// 安装焦点困于；返回释放函数
export function useFocusTrap(
  containerRef: Ref<HTMLElement | null>,
  active: Ref<boolean>
): void {
  let previouslyFocused: HTMLElement | null = null

  function onKeydown(e: KeyboardEvent): void {
    if (!active.value) return
    if (e.key !== 'Tab') return
    const root = containerRef.value
    if (!root) return
    const focusable = getFocusable(root)
    if (focusable.length === 0) {
      // 无可聚焦元素时把焦点留在容器本身
      e.preventDefault()
      root.focus()
      return
    }
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    const current = document.activeElement as HTMLElement | null

    if (e.shiftKey) {
      if (current === first || !root.contains(current)) {
        e.preventDefault()
        last.focus()
      }
    } else {
      if (current === last || !root.contains(current)) {
        e.preventDefault()
        first.focus()
      }
    }
  }

  function activate(): void {
    previouslyFocused = document.activeElement as HTMLElement | null
    // 下一帧再聚焦，确保挂载完成
    requestAnimationFrame(() => {
      const root = containerRef.value
      if (!root) return
      const focusable = getFocusable(root)
      const target = focusable[0] ?? root
      target.focus()
    })
    document.addEventListener('keydown', onKeydown, true)
  }

  function deactivate(): void {
    document.removeEventListener('keydown', onKeydown, true)
    if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
      previouslyFocused.focus()
    }
    previouslyFocused = null
  }

  watch(
    active,
    (on) => {
      if (on) activate()
      else deactivate()
    },
    { immediate: true, flush: 'post' }
  )

  onBeforeUnmount(() => {
    if (active.value) deactivate()
  })
}
