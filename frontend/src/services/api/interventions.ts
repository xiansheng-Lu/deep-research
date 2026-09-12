// 运行控制与用户介入 API（M2-5：契约草案 §6.1/§6.2）
// 所有动作均为编排层受控通道；WS 指令通道在 RealtimeClient 层提供等价封装。
import { http } from '../http/http'
import type {
  CancelRunRequest,
  HumanInput,
  InterveneRequest,
  PauseRunRequest,
  ResumeRunRequest,
  RunControlResponse
} from './types'

// POST /runs/{id}/pause：软暂停（仅 running/queued 可暂停，409 RUN_NOT_PAUSABLE）
export function pauseRun(
  runId: string,
  body: PauseRunRequest = {},
  options?: { idempotencyKey?: string }
): Promise<RunControlResponse> {
  return http<RunControlResponse>(`/runs/${encodeURIComponent(runId)}/pause`, {
    method: 'POST',
    body: JSON.stringify(body),
    idempotencyKey: options?.idempotencyKey
  })
}

// POST /runs/{id}/resume：恢复/提交人类输入
// - 纯继续：{ human_input: { kind: 'proceed' } } 或空对象
// - 澄清回答：{ human_input: { answers: { scope: '近三年' } } }
// - 裁决/动作：见 HumanInput 三选一模型
export function resumeRun(
  runId: string,
  body: ResumeRunRequest = {},
  options?: { idempotencyKey?: string }
): Promise<RunControlResponse> {
  return http<RunControlResponse>(`/runs/${encodeURIComponent(runId)}/resume`, {
    method: 'POST',
    body: JSON.stringify(body),
    idempotencyKey: options?.idempotencyKey
  })
}

// 便捷封装：提交澄清答案（resume 的 answers 分支）
export function submitClarificationAnswers(
  runId: string,
  answers: HumanInput['answers'],
  options?: { idempotencyKey?: string }
): Promise<RunControlResponse> {
  return resumeRun(runId, { human_input: { answers } }, options)
}

// 便捷封装：纯恢复继续（软暂停后）
export function proceedRun(
  runId: string,
  options?: { idempotencyKey?: string }
): Promise<RunControlResponse> {
  return resumeRun(runId, { human_input: { kind: 'proceed' } }, options)
}

// POST /runs/{id}/cancel：硬中断（幂等，重复 cancel 返回当前状态）
export function cancelRun(
  runId: string,
  body: CancelRunRequest = { keep_partial: true },
  options?: { idempotencyKey?: string }
): Promise<RunControlResponse> {
  return http<RunControlResponse>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    body: JSON.stringify(body),
    idempotencyKey: options?.idempotencyKey
  })
}

// POST /runs/{id}/intervene：主动介入快捷通道
// M2 开放 ask_followup（追加追问）/ exclude_evidence（剔除证据）
// revert_stage / mark_doubt 为 M4，前端不提供入口
export function intervene(
  runId: string,
  body: InterveneRequest,
  options?: { idempotencyKey?: string }
): Promise<RunControlResponse> {
  return http<RunControlResponse>(`/runs/${encodeURIComponent(runId)}/intervene`, {
    method: 'POST',
    body: JSON.stringify(body),
    idempotencyKey: options?.idempotencyKey
  })
}
