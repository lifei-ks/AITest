import { defineStore } from 'pinia'
import { listProjects } from '../api/projects'

// 全局配置 + 当前项目（TASK_BOOK 3.2：Pinia 只存全局配置和当前项目）
export const useConfigStore = defineStore('config', {
  state: () => ({
    currentProjectId: localStorage.getItem('currentProjectId') || null,
    projects: [],
    backendOnline: false,
  }),
  actions: {
    setProject(id) {
      this.currentProjectId = id || null
      if (this.currentProjectId) localStorage.setItem('currentProjectId', this.currentProjectId)
      else localStorage.removeItem('currentProjectId')
    },
    async loadProjects() {
      this.projects = await listProjects()
      // 项目可能已被删除：清理失效的当前项目
      if (this.currentProjectId && !this.projects.some((p) => p.id === this.currentProjectId)) {
        this.setProject(null)
      }
    },
  },
})
