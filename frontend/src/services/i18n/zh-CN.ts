// 文案模块（[前端详细设计 §8.6]）
// 当前仅中文；保留对象化结构便于将来接 vue-i18n 只换读取层
export const zhCN = {
  errors: {
    unknown: '未知错误（请稍后重试）',
    notFound: '资源不存在或无权访问',
    network: '网络异常，请检查连接',
    unauthorized: '会话已过期，请重新登录'
  },
  common: {
    confirm: '确认',
    cancel: '取消',
    submit: '提交',
    loading: '加载中…'
  }
} as const

export type Messages = typeof zhCN

// 默认导出读取函数；后续如接 vue-i18n，仅替换此函数实现
export function t(): Messages {
  return zhCN
}
