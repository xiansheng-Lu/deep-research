// 统一 ApiError 形态（[前端详细设计 §8.2 § 错误归一]）
export interface ApiError {
  status: number
  code: string
  title: string
  detail?: string
  traceId?: string
}
