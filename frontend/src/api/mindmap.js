import request from './request'

export const previewMindmap = (pid, requirementId) =>
  request.get(`/projects/${pid}/mindmap/preview`, { params: requirementId ? { requirementId } : {} })
export const downloadMindmap = (pid, format, requirementId) =>
  request.get(`/projects/${pid}/mindmap/download`, {
    params: { format, ...(requirementId ? { requirementId } : {}) },
    responseType: 'blob',
  })
export const getCoverageMatrix = (pid) => request.get(`/projects/${pid}/coverage/matrix`)
export const getReport = (pid, requirementId) =>
  request.get(`/projects/${pid}/report`, {
    params: requirementId ? { requirementId } : {},
    responseType: 'blob',
  })
export const listExecutions = (pid, params) => request.get(`/projects/${pid}/executions`, { params })
export const getExecution = (eid) => request.get(`/executions/${eid}`)
export const getSteps = (eid) => request.get(`/executions/${eid}/steps`)
