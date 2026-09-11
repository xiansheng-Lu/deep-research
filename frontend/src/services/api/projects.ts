// 项目 API（openapi-m1：GET/POST /projects）
import { http } from '../http/http'
import type { CreateProjectRequest, ProjectResponse } from './types'

// 项目列表（M1 契约为全量数组，非分页）
export function listProjects(): Promise<ProjectResponse[]> {
  return http<ProjectResponse[]>('/projects')
}

// 创建项目
export function createProject(body: CreateProjectRequest): Promise<ProjectResponse> {
  return http<ProjectResponse>('/projects', {
    method: 'POST',
    body: JSON.stringify(body)
  })
}
