// 埋点模块骨架（[前端详细设计 §16.1]）
// 当前里程碑仅暴露 track() 接口；批量上报、缓冲、端点联调在 M2 接入
export function track(event: string, props?: Record<string, unknown>): void {
  // dev 环境走 console，便于验收；生产再补批量上报（§16.1 第二段）
  if (import.meta.env.DEV) {
     
    console.warn('[telemetry]', event, props)
  }
}
