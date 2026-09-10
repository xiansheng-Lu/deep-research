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
  },
  // 领域枚举中文展示（与后端枚举值一一对应，未知值原样回显）
  domain: {
    role: {
      owner: '所有者',
      admin: '管理员',
      researcher: '研究者',
      reviewer: '审阅人'
    },
    runStatus: {
      pending: '等待中',
      running: '进行中',
      paused: '已暂停',
      succeeded: '已完成',
      failed: '失败',
      cancelled: '已取消'
    },
    stage: {
      clarify: '澄清界定',
      decompose: '问题分解',
      retrieve: '证据检索',
      standardize: '证据标准化',
      critique: '交叉审校',
      report: '报告撰写'
    },
    tier: {
      quick: '快速档',
      standard: '标准档',
      deep: '深度档',
      extreme: '极致档'
    }
  }
} as const

export type Messages = typeof zhCN

// 默认导出读取函数；后续如接 vue-i18n，仅替换此函数实现
export function t(): Messages {
  return zhCN
}

function labelOf(map: Record<string, string>, key: string): string {
  return map[key] ?? key
}

export function roleLabel(role: string): string {
  return labelOf(zhCN.domain.role, role)
}

export function runStatusLabel(status: string): string {
  return labelOf(zhCN.domain.runStatus, status)
}

export function stageLabel(stage: string): string {
  return labelOf(zhCN.domain.stage, stage)
}

export function tierLabel(tier: string): string {
  return labelOf(zhCN.domain.tier, tier)
}

// run 状态到 UiBadge 语义色的映射（指挥舱/任务列表共用）
export type BadgeVariant = 'neutral' | 'info' | 'success' | 'warn' | 'danger' | 'brand'

export function runStatusVariant(status: string): BadgeVariant {
  switch (status) {
    case 'running':
      return 'info'
    case 'succeeded':
      return 'success'
    case 'paused':
      return 'warn'
    case 'failed':
      return 'danger'
    default:
      return 'neutral'
  }
}
