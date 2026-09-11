// 本地 run 索引（M1 契约无"项目下 run 列表"端点，任务列表靠本地索引恢复）
// localStorage 键 m1.run_history.v1：
// { [projectId]: Array<{ runId, createdAt }> }，新记录在前。
const STORAGE_KEY = 'm1.run_history.v1'

export interface RunHistoryEntry {
  runId: string
  createdAt: string
}

type RunHistoryMap = Record<string, RunHistoryEntry[]>

function readMap(): RunHistoryMap {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      return parsed as RunHistoryMap
    }
  } catch {
    // 存储内容损坏时视为空索引，后续写入会覆盖修复
  }
  return {}
}

function writeMap(map: RunHistoryMap): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(map))
}

export function useRunHistory() {
  // 记录一次发起：同一 runId 去重，新记录置于队首
  function recordRun(projectId: string, runId: string, createdAt: string): void {
    const map = readMap()
    const list = map[projectId] ?? []
    const rest = list.filter((item) => item.runId !== runId)
    map[projectId] = [{ runId, createdAt }, ...rest]
    writeMap(map)
  }

  function listRuns(projectId: string): RunHistoryEntry[] {
    return readMap()[projectId] ?? []
  }

  function removeRun(projectId: string, runId: string): void {
    const map = readMap()
    const list = map[projectId]
    if (!list) return
    map[projectId] = list.filter((item) => item.runId !== runId)
    writeMap(map)
  }

  return { recordRun, listRuns, removeRun }
}
