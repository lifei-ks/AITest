import request from './request'

// 多 Agent 工作流：需求版本 → 测试点 → 用例 → 审核 → 执行 → 分析
export const listWorkflows = (pid, limit = 20) =>
  request.get(`/projects/${pid}/workflows`, { params: { limit } })

export const createWorkflow = (pid, data) => request.post(`/projects/${pid}/workflows`, data)

export const getWorkflow = (runId) => request.get(`/workflows/${runId}`)

export const workflowReportUrl = (runId) => `/api/workflows/${runId}/report`
