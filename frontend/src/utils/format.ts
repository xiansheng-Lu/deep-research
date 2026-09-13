// 原生 Intl 格式化（M1 不引入 dayjs/numeral）

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium',
  timeStyle: 'short'
})

export function formatDateTime(iso: string): string {
  return dateTimeFormatter.format(new Date(iso))
}

// 发布时间可能是纯日期（YYYY-MM-DD）：原样展示，避免被格式化成带时分；带时间的 ISO 走完整格式
export function formatPublishedAt(value: string): string {
  return value.length === 10 ? value : formatDateTime(value)
}

const numberFormatter = new Intl.NumberFormat('zh-CN')

export function formatNumber(value: number): string {
  return numberFormatter.format(value)
}

// 时长格式化（指挥舱「已用时长」）：不足 1 小时 mm:ss，超过则 h:mm:ss
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remain = seconds % 60
  const pad = (value: number) => String(value).padStart(2, '0')
  if (hours > 0) return `${hours}:${pad(minutes)}:${pad(remain)}`
  return `${pad(minutes)}:${pad(remain)}`
}
