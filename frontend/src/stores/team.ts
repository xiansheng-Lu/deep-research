import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface TeamMember {
  id: string
  name: string
  email: string
  role: 'owner' | 'admin' | 'researcher' | 'reviewer'
}

// 当前租户态：当前团队 ID 与成员缓存（M6 起扩展完整能力）
export const useTeamStore = defineStore('team', () => {
  const currentTeamId = ref<string | null>(null)
  const members = ref<TeamMember[]>([])

  function setCurrentTeam(teamId: string | null) {
    currentTeamId.value = teamId
  }

  function setMembers(next: TeamMember[]) {
    members.value = next
  }

  return { currentTeamId, members, setCurrentTeam, setMembers }
})
