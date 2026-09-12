// demo_full 剧本（WP-10：M0 被裁剪、M2 补回的全分支演示剧本）
// 分支覆盖：澄清挂起 -> resume 续跑 -> 子问题并行 -> 证据流（含一条可剔除低可信源）
//          -> 冲突 detected -> 成本 70%/90% 两档预警 -> 结构化报告
// 时序约定：证据帧间隔 500ms（模拟后端 500ms 节流长发），token 帧约 1s 一帧（模拟 1s 节流）
//
// 本文件被 vite Node 上下文加载，使用相对路径，禁止 @/ 别名

import { sequence, emit, wait, gate } from '../script/types'
import type { ScriptNode } from '../script/types'
import type {
  MockCitation,
  MockConflict,
  MockEvidence,
  MockReportBlock,
  MockRun,
  MockStructuredReport,
  MockSubQuestion
} from '../store'
import { buildReportMarkdown } from './happy_path'

// ─── 静态实体（id 在单 run 内唯一，集合按 run_id 隔离，跨 run 不冲突）───

export function buildDemoSubQuestions(runId: string): MockSubQuestion[] {
  const make = (
    id: string,
    question: string,
    dependsOn: string[],
    status: MockSubQuestion['status']
  ): MockSubQuestion => {
    const ts = new Date().toISOString()
    return {
      id,
      run_id: runId,
      question,
      depends_on: dependsOn,
      status,
      evidence_count: 0,
      created_at: ts,
      updated_at: ts
    }
  }
  return [
    make('sq1', '主流向量数据库的架构与索引机制有何差异？', [], 'queued'),
    make('sq2', '不同方案在千万级数据下的检索性能与扩展性如何？', ['sq1'], 'queued'),
    make('sq3', '各方案的运维成本、生态成熟度与托管选项如何？', ['sq1'], 'queued')
  ]
}

// 信源覆盖 A/B/C/D 四级；ev12 为 D 级低可信源，与 ev05 数据矛盾，供冲突演示与剔除介入
export function buildDemoEvidence(runId: string): MockEvidence[] {
  const rows: Array<Omit<MockEvidence, 'run_id' | 'excluded_by_user'>> = [
    {
      id: 'ev01', sub_question_id: 'sq1',
      url: 'https://docs.pinecone.io/learn/vector-index-types', domain: 'docs.pinecone.io',
      title: 'Vector Index Types: HNSW vs IVF vs PQ',
      snippet: '官方文档对比了 HNSW、IVF 与乘积量化三类索引在召回率、延迟与内存占用上的权衡，建议按召回 SLA 选型。',
      content: 'Pinecone 官方文档指出：HNSW 在高召回场景下延迟稳定但内存占用较高；IVF+PQ 以少量召回损失换取显著的内存与成本下降，适合超大规模语料。',
      source_type: 'official_doc', source_level: 'primary', credibility: 'A', relevance_score: 0.96,
      published_at: '2025-11-02', fetched_at: '2026-09-12T08:00:00Z'
    },
    {
      id: 'ev02', sub_question_id: 'sq1',
      url: 'https://www.techpress.example/news/vector-db-2026-roundup', domain: 'techpress.example',
      title: '2026 向量数据库年度盘点：架构流派之争',
      snippet: '年度盘点文章梳理了专用向量库、检索引擎插件与轻量化嵌入式三类技术路线的代表产品。',
      source_type: 'news', source_level: 'secondary', credibility: 'B', relevance_score: 0.82,
      published_at: '2026-02-18', fetched_at: '2026-09-12T08:00:01Z'
    },
    {
      id: 'ev03', sub_question_id: 'sq1',
      url: 'https://forum.dev.example/t/hnsw-tuning-notes/8842', domain: 'forum.dev.example',
      title: '社区调参笔记：HNSW 的 M 与 efConstruction 实战经验',
      snippet: '社区帖子分享了构建参数 M、efConstruction 对写入放大与查询延迟的实测影响。',
      source_type: 'community', source_level: 'tertiary', credibility: 'C', relevance_score: 0.71,
      published_at: '2025-09-30', fetched_at: '2026-09-12T08:00:02Z'
    },
    {
      id: 'ev04', sub_question_id: 'sq1',
      url: 'https://search.example.com/aggregated/vector-index-blog', domain: 'search.example',
      title: '聚合博客：向量索引入门（作者与更新日期不详）',
      snippet: '聚合搜索到的入门博客，内容与官方文档多有重合但未标注引用来源，发布时间无法确认。',
      source_type: 'search', source_level: 'tertiary', credibility: 'D', relevance_score: 0.52,
      published_at: null, fetched_at: '2026-09-12T08:00:03Z'
    },
    {
      id: 'ev05', sub_question_id: 'sq2',
      url: 'https://docs.vectorvendor-a.example/benchmarks/scale', domain: 'docs.vectorvendor-a.example',
      title: '官方规模化基准：十亿向量下的 QPS 与召回报告',
      snippet: '厂商 A 官方基准报告称其托管集群在十亿向量、95% 召回下 P99 延迟低于 80ms。',
      content: '基准环境：32 个查询节点、副本因子 3、HNSW(M=32, ef=256)；报告同时披露该数据为厂商自测，未经第三方复核。',
      source_type: 'official_doc', source_level: 'primary', credibility: 'A', relevance_score: 0.93,
      published_at: '2026-01-15', fetched_at: '2026-09-12T08:00:04Z'
    },
    {
      id: 'ev06', sub_question_id: 'sq2',
      url: 'https://engineering.vectorvendor-b.example/2026/03/billion-scale', domain: 'engineering.vectorvendor-b.example',
      title: '厂商 B 工程博客：十亿级向量检索的分片实践',
      snippet: '厂商 B 公开了分片路由与两阶段检索架构，在相近硬件下报告 P99 约 120ms。',
      source_type: 'official_doc', source_level: 'secondary', credibility: 'B', relevance_score: 0.88,
      published_at: '2026-03-08', fetched_at: '2026-09-12T08:00:05Z'
    },
    {
      id: 'ev07', sub_question_id: 'sq2',
      url: 'https://stackoverflow.example/questions/7788/vector-recall-tuning', domain: 'stackoverflow.example',
      title: '问答：召回率随数据量下滑如何排查',
      snippet: '高赞回答建议先核对分片裁剪策略与 ef 参数，再排查量化造成的召回损失。',
      source_type: 'community', source_level: 'secondary', credibility: 'B', relevance_score: 0.79,
      published_at: '2025-12-05', fetched_at: '2026-09-12T08:00:06Z'
    },
    {
      id: 'ev08', sub_question_id: 'sq2',
      url: 'https://search.example.com/benchmark-mirror/qps-comparison', domain: 'search.example',
      title: '第三方基准镜像：六款向量数据库 QPS 对比',
      snippet: '镜像站汇总了六款产品的公开 QPS 数据，但未说明测试硬件与数据集版本。',
      source_type: 'search', source_level: 'tertiary', credibility: 'C', relevance_score: 0.63,
      published_at: '2025-08-21', fetched_at: '2026-09-12T08:00:07Z'
    },
    {
      id: 'ev09', sub_question_id: 'sq3',
      url: 'https://www.techpress.example/news/managed-vector-pricing-2026', domain: 'techpress.example',
      title: '2026 托管向量服务定价与运维成本分析',
      snippet: '报道比较了三家托管服务按维度量计费的口径，自建集群的隐性运维人力成本常被低估。',
      source_type: 'news', source_level: 'secondary', credibility: 'B', relevance_score: 0.85,
      published_at: '2026-04-02', fetched_at: '2026-09-12T08:00:08Z'
    },
    {
      id: 'ev10', sub_question_id: 'sq3',
      url: 'https://forum.dev.example/t/self-hosted-vector-ops/1290', domain: 'forum.dev.example',
      title: '社区讨论：自建向量库一年的运维账单',
      snippet: '多名实践者分享了备份、压缩与索引重建的夜间维护窗口经验，人力投入差异较大。',
      source_type: 'community', source_level: 'tertiary', credibility: 'C', relevance_score: 0.68,
      published_at: '2026-02-11', fetched_at: '2026-09-12T08:00:09Z'
    },
    {
      id: 'ev11', sub_question_id: 'sq3',
      url: 'https://docs.vectorvendor-b.example/integrations/overview', domain: 'docs.vectorvendor-b.example',
      title: '厂商 B 官方：生态集成与 SDK 支持矩阵',
      snippet: '官方集成矩阵列出了与主流编排框架、Embedding 提供商及对象存储的对接情况。',
      source_type: 'official_doc', source_level: 'primary', credibility: 'A', relevance_score: 0.9,
      published_at: '2026-05-20', fetched_at: '2026-09-12T08:00:10Z'
    },
    {
      id: 'ev12', sub_question_id: 'sq3',
      url: 'https://random-blog.example/2019/vector-db-magic', domain: 'random-blog.example',
      title: '个人旧博客：向量数据库性能"神话"（2019）',
      snippet: '2019 年个人博客声称某方案在千万级下轻松达到数十万 QPS 且零召回损失，无任何测试细节。',
      source_type: 'community', source_level: 'tertiary', credibility: 'D', relevance_score: 0.31,
      published_at: '2019-06-09', fetched_at: '2026-09-12T08:00:11Z'
    }
  ]
  return rows.map((row) => ({
    ...row,
    run_id: runId,
    excluded_by_user: false,
    created_at: '2026-09-12T08:00:00Z',
    updated_at: '2026-09-12T08:00:00Z'
  }))
}

export function buildDemoConflict(runId: string, evidence: MockEvidence[]): MockConflict {
  const ev05 = evidence.find((e) => e.id === 'ev05')
  const ev12 = evidence.find((e) => e.id === 'ev12')
  return {
    id: 'cf1',
    run_id: runId,
    claim: '向量检索在千万级以上数据规模下的 QPS 与召回能否同时保持高水平',
    evidence_a_id: ev05?.id ?? 'ev05',
    evidence_b_id: ev12?.id ?? 'ev12',
    // type 为后端冲突分类键，mock 使用可读的性能数据类
    type: 'performance_data',
    severity: 'high',
    status: 'detected',
    created_at: '2026-09-12T08:01:00Z',
    updated_at: '2026-09-12T08:01:00Z'
  }
}

// ─── 澄清问题（interrupt.requested payload，契约草案 §4.1）───

export const DEMO_CLARIFY_QUESTIONS = [
  {
    key: 'scope',
    text: '本次研究的方案范围如何界定？',
    options: ['仅开源方案', '开源与商业方案均覆盖', '仅评估商业方案'],
    recommended: 1
  },
  {
    key: 'time_range',
    text: '重点考察的时间跨度？',
    options: ['近一年', '近三年', '近五年'],
    recommended: 1
  }
]

export const DEMO_CLARIFY_DEFAULTS: Record<string, string> = {
  scope: '开源与商业方案均覆盖',
  time_range: '近三年'
}

// ─── token 用量序列（按预算比率，保证任意档位都稳定触发 70%/90% 两档预警）───

const TOKEN_RATIO_SEQUENCE = [0.12, 0.28, 0.45, 0.6, 0.72, 0.83, 0.91]
const FINAL_TOKEN_RATIO = 0.96

function tokenEvent(run: MockRun, ratio: number, stage: string) {
  return emit(() => ({
    type: 'token.usage.update',
    run_id: run.id,
    stage,
    used: Math.round(run.token_budget * ratio),
    budget: run.token_budget,
    model_breakdown: {
      'deepseek-chat': Math.round(run.token_budget * ratio * 0.7),
      'rerank-model': Math.round(run.token_budget * ratio * 0.3)
    }
  }))
}

function costWarningEvent(run: MockRun, ratio: number, level: 'warning' | 'danger', stage: string) {
  return emit(() => ({
    type: 'cost.warning',
    run_id: run.id,
    stage,
    level,
    used: Math.round(run.token_budget * ratio),
    budget: run.token_budget,
    ratio: Number(ratio.toFixed(2))
  }))
}

// ─── phase1：澄清挂起（run.finished status=paused，与后端 HITL 语义一致）───

export function buildDemoClarifyScript(run: MockRun): ScriptNode {
  return sequence(
    wait(400),
    emit({ type: 'stage.started', run_id: run.id, stage: 'clarify', attempt: 1 }),
    wait(1200),
    emit({
      type: 'interrupt.requested',
      run_id: run.id,
      stage: 'clarify',
      payload: {
        reason: 'clarification',
        questions: DEMO_CLARIFY_QUESTIONS,
        defaults: DEMO_CLARIFY_DEFAULTS,
        expires_in_seconds: 300
      }
    }),
    wait(300),
    // 澄清挂起同样发 run.finished，但 status=paused（M1 联调已确认的后端契约）
    emit(() => ({
      type: 'run.finished',
      run_id: run.id,
      status: 'paused',
      current_stage: 'clarify',
      token_used: 0,
      occurred_at: new Date().toISOString()
    }))
  )
}

// ─── phase2：resume 后续跑的完整剧本 ───
// answers 为用户澄清答案，mock 不改变分支内容，仅用于签名对齐与后续报告引用
export function buildDemoResumeScript(run: MockRun, _answers: Record<string, string>): ScriptNode {
  const ev = (id: string, stage: string, extra: Record<string, unknown> = {}) =>
    emit({ type: 'evidence.fetched', run_id: run.id, stage, ...pickEvidence(id), ...extra })

  const sq = (
    type: string,
    id: string,
    stage: string,
    patch: Record<string, unknown>
  ) => emit({ type, run_id: run.id, stage, sub_question_id: id, ...patch })

  return sequence(
    // 留出 WS 重新订阅窗口（paused 后通道已关闭，resume 由前端重新建连）
    wait(600),
    emit({ type: 'stage.finished', run_id: run.id, stage: 'clarify', attempt: 1 }),
    // 问题分解：三个子问题（sq2/sq3 依赖 sq1）
    emit({ type: 'stage.started', run_id: run.id, stage: 'decompose', attempt: 1 }),
    wait(600),
    sq('sub_question.created', 'sq1', 'decompose', {
      question: '主流向量数据库的架构与索引机制有何差异？',
      depends_on: [], status: 'queued'
    }),
    sq('sub_question.created', 'sq2', 'decompose', {
      question: '不同方案在千万级数据下的检索性能与扩展性如何？',
      depends_on: ['sq1'], status: 'queued'
    }),
    sq('sub_question.created', 'sq3', 'decompose', {
      question: '各方案的运维成本、生态成熟度与托管选项如何？',
      depends_on: ['sq1'], status: 'queued'
    }),
    wait(700),
    gate(),
    emit({ type: 'stage.finished', run_id: run.id, stage: 'decompose', attempt: 1 }),

    // 证据检索：sq1 先行，sq2/sq3 并行；证据帧 500ms 间隔（模拟节流长发）
    emit({ type: 'stage.started', run_id: run.id, stage: 'retrieve', attempt: 1 }),
    wait(400),
    sq('sub_question.started', 'sq1', 'retrieve', { status: 'running' }),
    wait(500),
    ev('ev01', 'retrieve'), wait(500),
    ev('ev02', 'retrieve'), wait(500),
    tokenEvent(run, TOKEN_RATIO_SEQUENCE[0], 'retrieve'),
    ev('ev03', 'retrieve'), wait(500),
    ev('ev04', 'retrieve'), wait(500),
    tokenEvent(run, TOKEN_RATIO_SEQUENCE[1], 'retrieve'),
    sq('sub_question.finished', 'sq1', 'retrieve', { status: 'succeeded', evidence_count: 4 }),
    wait(300),
    sq('sub_question.started', 'sq2', 'retrieve', { status: 'running' }),
    sq('sub_question.started', 'sq3', 'retrieve', { status: 'running' }),
    wait(500),
    ev('ev05', 'retrieve'), wait(500),
    ev('ev06', 'retrieve'), wait(500),
    tokenEvent(run, TOKEN_RATIO_SEQUENCE[2], 'retrieve'),
    ev('ev07', 'retrieve'), wait(500),
    ev('ev08', 'retrieve'), wait(500),
    sq('sub_question.finished', 'sq2', 'retrieve', { status: 'succeeded', evidence_count: 4 }),
    ev('ev09', 'retrieve'), wait(500),
    tokenEvent(run, TOKEN_RATIO_SEQUENCE[3], 'retrieve'),
    ev('ev10', 'retrieve'), wait(500),
    ev('ev11', 'retrieve'), wait(500),
    ev('ev12', 'retrieve'), wait(500),
    sq('sub_question.finished', 'sq3', 'retrieve', { status: 'succeeded', evidence_count: 4 }),
    tokenEvent(run, TOKEN_RATIO_SEQUENCE[4], 'retrieve'),
    // 越过 70%：warning 预警
    costWarningEvent(run, TOKEN_RATIO_SEQUENCE[4], 'warning', 'retrieve'),
    wait(300),
    gate(),
    emit({ type: 'stage.finished', run_id: run.id, stage: 'retrieve', attempt: 1 }),

    // 证据标准化
    emit({ type: 'stage.started', run_id: run.id, stage: 'standardize', attempt: 1 }),
    wait(1000),
    tokenEvent(run, TOKEN_RATIO_SEQUENCE[5], 'standardize'),
    wait(400),
    gate(),
    emit({ type: 'stage.finished', run_id: run.id, stage: 'standardize', attempt: 1 }),

    // 交叉审校：检测到高严重度冲突；越过 90%：danger 预警
    emit({ type: 'stage.started', run_id: run.id, stage: 'critique', attempt: 1 }),
    wait(600),
    // 冲突字段嵌套在 payload 中：其内 type 是冲突分类键（performance_data），
    // 不能与事件帧 type=conflict.detected 平铺，否则展开时互相覆盖
    emit({
      type: 'conflict.detected',
      run_id: run.id,
      stage: 'critique',
      payload: pickConflictShape()
    }),
    wait(800),
    tokenEvent(run, TOKEN_RATIO_SEQUENCE[6], 'critique'),
    costWarningEvent(run, TOKEN_RATIO_SEQUENCE[6], 'danger', 'critique'),
    wait(800),
    emit({ type: 'stage.finished', run_id: run.id, stage: 'critique', attempt: 1 }),

    // 报告生成
    emit({ type: 'stage.started', run_id: run.id, stage: 'report', attempt: 1 }),
    wait(1200),
    emit(() => ({
      type: 'report.finished',
      run_id: run.id,
      stage: 'report',
      report_id: `rpt_${run.id}`,
      status: 'final'
    })),
    wait(200),
    emit({ type: 'stage.finished', run_id: run.id, stage: 'report', attempt: 1 }),
    wait(200),
    emit(() => ({
      type: 'run.finished',
      run_id: run.id,
      status: 'succeeded',
      current_stage: 'report',
      token_used: Math.round(run.token_budget * FINAL_TOKEN_RATIO),
      occurred_at: new Date().toISOString()
    }))
  )
}

// 证据帧只携带列表字段（§9.4：全文懒加载），与 EvidenceFetchedPayload 对齐
function pickEvidence(id: string): Record<string, unknown> {
  const full = buildDemoEvidence('').find((e) => e.id === id)
  if (!full) throw new Error(`demo evidence 不存在：${id}`)
  return {
    id: full.id,
    sub_question_id: full.sub_question_id,
    url: full.url,
    domain: full.domain,
    title: full.title,
    snippet: full.snippet,
    source_type: full.source_type,
    source_level: full.source_level,
    credibility: full.credibility,
    relevance_score: full.relevance_score,
    published_at: full.published_at ?? null
  }
}

function pickConflictShape(): Record<string, unknown> {
  return {
    id: 'cf1',
    claim: '向量检索在千万级以上数据规模下的 QPS 与召回能否同时保持高水平',
    evidence_a_id: 'ev05',
    evidence_b_id: 'ev12',
    type: 'performance_data',
    severity: 'high',
    status: 'detected'
  }
}

// ─── 终态产物：Markdown 报告 + 结构化报告 + citations ───

// marker 与证据一一对应（[1]..[12]），blocks 引用时直接选取
const CITATION_MARKERS: Record<string, string> = Object.fromEntries(
  buildDemoEvidence('').map((e, i) => [e.id, `[${i + 1}]`])
)

function cite(evidenceId: string): { evidence_id: string; marker: string; snippet: string } {
  const full = buildDemoEvidence('').find((e) => e.id === evidenceId)
  return {
    evidence_id: evidenceId,
    marker: CITATION_MARKERS[evidenceId],
    snippet: full?.snippet ?? ''
  }
}

function buildDemoBlocks(run: MockRun): MockReportBlock[] {
  const c = (...ids: string[]) => ids.map(cite)
  return [
    {
      id: 'block-01', type: 'conclusion', claim_id: 'claim-01',
      text: `围绕「${run.question}」的多源核验显示，向量数据库已形成专用数据库、检索引擎插件与嵌入式轻量方案三条清晰的技术路线，选型应回到召回 SLA、数据规模与运维能力三个维度。`,
      confidence: 'cross_verified', citations: c('ev01', 'ev09')
    },
    {
      id: 'block-02', type: 'evidence',
      text: '官方文档对 HNSW、IVF 与乘积量化三类索引的权衡有明确表述：高召回低延迟偏向 HNSW，超大规模成本敏感场景偏向 IVF+PQ。',
      citations: c('ev02')
    },
    {
      id: 'block-03', type: 'conclusion', claim_id: 'claim-02',
      text: '索引机制层面，HNSW 是当前高召回场景的事实标准；IVF+PQ 的价值在于以可控的召回损失换取内存与成本下降，二者并非替代关系。',
      confidence: 'cross_verified', citations: c('ev01', 'ev06')
    },
    {
      id: 'block-04', type: 'conclusion', claim_id: 'claim-03',
      text: 'HNSW 的构建参数 M 与 efConstruction 对写入放大和查询延迟影响显著，调参空间真实存在，但社区数据点样本有限。',
      confidence: 'single_source', citations: c('ev03')
    },
    {
      id: 'block-05', type: 'evidence',
      text: '召回率随规模下滑的常见根因是分片裁剪策略与 ef 参数，其次才是量化损失——该排查路径在社区高赞回答与厂商工程博客中互证。',
      citations: c('ev07')
    },
    {
      id: 'block-06', type: 'conclusion', claim_id: 'claim-04',
      text: '规模化性能上，官方与工程来源在十亿向量量级给出 P99 80ms 至 120ms 的区间，但测试条件差异明显，跨厂商数字不可直接横比。',
      confidence: 'cross_verified', citations: c('ev05', 'ev06', 'ev08')
    },
    {
      id: 'block-07', type: 'dispute', claim_id: 'claim-05', conflict_id: 'cf1',
      text: '存在一处高严重度数据分歧：厂商 A 官方基准称十亿向量 95% 召回下 P99 低于 80ms，而一篇 2019 年个人博客声称"数十万 QPS 且零召回损失"且无任何测试细节。前者为可核验一手来源，后者可信度为 D 级，建议以官方口径为准并要求第三方复核。',
      confidence: 'cross_verified', citations: c('ev05', 'ev12')
    },
    {
      id: 'block-08', type: 'conclusion', claim_id: 'claim-06',
      text: '扩展能力的关键不在单节点 QPS，而在分片路由与两阶段检索架构；两家主流厂商均公开了相近的横向扩展思路。',
      confidence: 'cross_verified', citations: c('ev06', 'ev11')
    },
    {
      id: 'block-09', type: 'evidence',
      text: '自建集群的隐性运维成本在多名实践者的账单复盘中被反复提及：备份、压缩与索引重建需要固定的夜间维护窗口。',
      citations: c('ev10')
    },
    {
      id: 'block-10', type: 'conclusion', claim_id: 'claim-07',
      text: '运维与生态维度，托管服务按维度量计费的口径差异与自建人力成本共同决定总拥有成本；主流编排框架与 Embedding 提供商的集成成熟度正在快速拉平。',
      confidence: 'cross_verified', citations: c('ev09', 'ev11')
    },
    {
      id: 'block-11', type: 'conclusion', claim_id: 'claim-08',
      text: '价格报道显示托管方案在中小规模下通常更省，超过一定数据量后自建的单位成本才开始占优——但该拐点高度依赖团队运维成熟度。',
      confidence: 'single_source', citations: c('ev09')
    },
    {
      id: 'block-12', type: 'limitation',
      text: '部分聚合类内容来源陈旧、发布时间不可考（如 D 级入门博客），本报告未将其作为结论依据；时效敏感的定价数字以 2026 年公开材料为限。',
      citations: c('ev04')
    },
    {
      id: 'block-13', type: 'limitation',
      text: '性能分歧（见分歧区块）在本次研究中未获第三方独立基准复核，相关结论应保留置信区间，不宜作为硬性采购指标。',
      citations: c('ev12')
    },
    {
      id: 'block-14', type: 'conclusion', claim_id: 'claim-09',
      text: '建议以单一高价值场景先行、以可复现的召回与延迟基线驱动选型，优先选择生态集成完善且支持平滑迁移的方案，避免被单一托管口径锁定。',
      confidence: 'inferred', citations: c('ev01', 'ev11')
    }
  ]
}

function buildDemoCitations(evidence: MockEvidence[]): MockCitation[] {
  return evidence.map((e) => ({
    evidence_id: e.id,
    marker: CITATION_MARKERS[e.id],
    snippet: e.snippet,
    url: e.url,
    title: e.title,
    domain: e.domain,
    source_type: e.source_type,
    source_level: e.source_level,
    credibility: e.credibility,
    published_at: e.published_at ?? null
  }))
}

export function buildDemoArtifacts(run: MockRun): {
  markdown: string
  structured: MockStructuredReport
  citations: MockCitation[]
} {
  const evidence = buildDemoEvidence(run.id)
  const ts = nowIsoStatic()
  const structured: MockStructuredReport = {
    id: `rpt_${run.id}`,
    run_id: run.id,
    status: 'final',
    outline: [
      { id: 'sec-1', title: '调研概述', type: 'conclusion' },
      { id: 'sec-2', title: '架构与索引机制', type: 'conclusion' },
      { id: 'sec-3', title: '规模化性能与数据分歧', type: 'dispute' },
      { id: 'sec-4', title: '运维生态、局限与建议', type: 'limitation' }
    ],
    blocks: buildDemoBlocks(run),
    token_used: run.token_used,
    created_at: ts,
    updated_at: ts
  }
  return {
    markdown: buildReportMarkdown(run),
    structured,
    citations: buildDemoCitations(evidence)
  }
}

// 避免直接依赖 store 的 nowIso（fixture 保持纯函数风格）
function nowIsoStatic(): string {
  return new Date().toISOString()
}
