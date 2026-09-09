import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Project {
  id: string
  name: string
  description?: string
  createdAt: string
  recentRunId?: string | null
  memberCount?: number
}

// 项目实体缓存：分页缓存 + 当前项目指针；项目边界守卫以此为依据
export const useProjectStore = defineStore('project', () => {
  const projects = ref<Project[]>([])
  const currentProjectId = ref<string | null>(null)

  function setProjects(next: Project[]) {
    projects.value = next
  }

  function upsertProject(next: Project) {
    const idx = projects.value.findIndex((p) => p.id === next.id)
    if (idx === -1) {
      projects.value.unshift(next)
    } else {
      projects.value[idx] = next
    }
  }

  function setCurrentProject(projectId: string | null) {
    currentProjectId.value = projectId
  }

  function clear() {
    projects.value = []
    currentProjectId.value = null
  }

  return { projects, currentProjectId, setProjects, upsertProject, setCurrentProject, clear }
})
