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
    },
    // 研究模板展示名；技术 key 仅用于副标题/title 提示，不进入卡片主视觉
    template: {
      generic: '通用研究'
    },
    // M2 阶段执行状态
    stageStatus: {
      pending: '待执行',
      running: '进行中',
      succeeded: '已完成',
      failed: '失败',
      skipped: '已跳过'
    },
    // M2 子问题状态（evidence_short=证据不足，PRD A9 需显式提示）
    subQuestionStatus: {
      pending: '待执行',
      queued: '排队中',
      running: '进行中',
      succeeded: '已完成',
      failed: '失败',
      evidence_short: '证据不足'
    },
    // M2 冲突状态与严重度
    conflictStatus: {
      detected: '已发现',
      awaiting_human: '待裁决',
      resolved: '已裁决',
      abandoned: '已忽略'
    },
    conflictSeverity: {
      low: '轻微',
      medium: '中等',
      high: '严重'
    },
    // M2-7 信源元数据
    sourceType: {
      official_doc: '官方文档',
      news: '新闻媒体',
      community: '社区内容',
      search: '搜索结果',
      internal: '内部材料'
    },
    sourceLevel: {
      primary: '一手来源',
      secondary: '二手来源',
      tertiary: '三手来源'
    },
    credibility: {
      A: 'A 高可信',
      B: 'B 较可信',
      C: 'C 待核实',
      D: 'D 低可信'
    },
    // M2-6 论断置信度（推断不得按事实呈现）
    claimConfidence: {
      single_source: '单一来源',
      cross_verified: '多源印证',
      inferred: '推断'
    },
    // M2-1 意图分类
    intent: {
      chat: '闲聊',
      research: '深度研究',
      uncertain: '意图不确定'
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

export function templateLabel(templateId: string): string {
  return labelOf(zhCN.domain.template, templateId)
}

export function stageStatusLabel(status: string): string {
  return labelOf(zhCN.domain.stageStatus, status)
}

export function subQuestionStatusLabel(status: string): string {
  return labelOf(zhCN.domain.subQuestionStatus, status)
}

export function conflictStatusLabel(status: string): string {
  return labelOf(zhCN.domain.conflictStatus, status)
}

export function conflictSeverityLabel(severity: string): string {
  return labelOf(zhCN.domain.conflictSeverity, severity)
}

export function sourceTypeLabel(sourceType: string): string {
  return labelOf(zhCN.domain.sourceType, sourceType)
}

export function sourceLevelLabel(sourceLevel: string): string {
  return labelOf(zhCN.domain.sourceLevel, sourceLevel)
}

export function credibilityLabel(credibility: string): string {
  return labelOf(zhCN.domain.credibility, credibility)
}

export function claimConfidenceLabel(confidence: string): string {
  return labelOf(zhCN.domain.claimConfidence, confidence)
}

export function intentLabel(intent: string): string {
  return labelOf(zhCN.domain.intent, intent)
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

// 阶段执行状态语义色
export function stageStatusVariant(status: string): BadgeVariant {
  switch (status) {
    case 'running':
      return 'info'
    case 'succeeded':
      return 'success'
    case 'failed':
      return 'danger'
    case 'skipped':
      return 'neutral'
    default:
      return 'neutral'
  }
}

// 子问题状态语义色：证据不足走 warn（需显式提示，不能冒充事实）
export function subQuestionStatusVariant(status: string): BadgeVariant {
  switch (status) {
    case 'running':
    case 'queued':
      return 'info'
    case 'succeeded':
      return 'success'
    case 'failed':
      return 'danger'
    case 'evidence_short':
      return 'warn'
    default:
      return 'neutral'
  }
}

// 冲突状态语义色：待裁决/已发现走 danger 红点提示
export function conflictStatusVariant(status: string): BadgeVariant {
  switch (status) {
    case 'detected':
    case 'awaiting_human':
      return 'danger'
    case 'resolved':
      return 'success'
    default:
      return 'neutral'
  }
}

// 冲突严重度语义色
export function conflictSeverityVariant(severity: string): BadgeVariant {
  switch (severity) {
    case 'high':
      return 'danger'
    case 'medium':
      return 'warn'
    default:
      return 'neutral'
  }
}

// 可信分级语义色：A/B 正向，C 中性，D 警示
export function credibilityVariant(credibility: string): BadgeVariant {
  switch (credibility) {
    case 'A':
    case 'B':
      return 'success'
    case 'C':
      return 'neutral'
    case 'D':
      return 'warn'
    default:
      return 'neutral'
  }
}

// 论断置信度语义色：推断走中性，禁止使用 success 暗示已证实
export function claimConfidenceVariant(confidence: string): BadgeVariant {
  switch (confidence) {
    case 'cross_verified':
      return 'success'
    case 'single_source':
      return 'info'
    case 'inferred':
      return 'neutral'
    default:
      return 'neutral'
  }
}

// 意图分类语义色
export function intentVariant(intent: string): BadgeVariant {
  switch (intent) {
    case 'research':
      return 'brand'
    case 'chat':
      return 'info'
    default:
      return 'warn'
  }
}
