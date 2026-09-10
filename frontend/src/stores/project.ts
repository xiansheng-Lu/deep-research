import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ProjectResponse } from '@/services/api/types'

// 项目实体缓存：直接持有契约 ProjectResponse（snake_case），不做驼峰映射。
// 异步一律由页面/composable 发起，store 只提供同步态修改（§7.2）。
export const useProjectStore = defineStore('project', () => {
  const projects = ref<ProjectResponse[]>([])
  // 是否已完成过首次列表加载（区分"未加载"与"空列表"）
  const loaded = ref(false)
  const currentProjectId = ref<string | null>(null)

  function setProjects(next: ProjectResponse[]): void {
    projects.value = next
    loaded.value = true
  }

  function upsertProject(next: ProjectResponse): void {
    const idx = projects.value.findIndex((p) => p.id === next.id)
    if (idx === -1) {
      projects.value.unshift(next)
    } else {
      projects.value[idx] = next
    }
    loaded.value = true
  }

  // 内存未命中返回 null，由页面调列表接口兜底，store 内不做异步
  function getProject(id: string): ProjectResponse | null {
    return projects.value.find((p) => p.id === id) ?? null
  }

  function setCurrentProject(projectId: string | null): void {
    currentProjectId.value = projectId
  }

  function clear(): void {
    projects.value = []
    loaded.value = false
    currentProjectId.value = null
  }

  return {
    projects,
    loaded,
    currentProjectId,
    setProjects,
    upsertProject,
    getProject,
    setCurrentProject,
    clear
  }
})
