import request from './request'

// 用例管理 API
export const listTestcases = (pid, params) => request.get(`/projects/${pid}/testcases`, { params })
export const getTestcase = (pid, cid) => request.get(`/projects/${pid}/testcases/${cid}`)
export const updateTestcase = (pid, cid, data) => request.put(`/projects/${pid}/testcases/${cid}`, data)
export const deleteTestcase = (pid, cid) => request.delete(`/projects/${pid}/testcases/${cid}`)
export const batchDelete = (pid, ids) => request.delete(`/projects/${pid}/testcases/batch`, { data: { ids } })
export const listVersions = (pid, cid) => request.get(`/projects/${pid}/testcases/${cid}/versions`)
export const rollbackVersion = (pid, cid, vid) => request.post(`/projects/${pid}/testcases/${cid}/versions/${vid}/rollback`)
export const copyTo = (pid, ids, targetProjectId) => request.post(`/projects/${pid}/testcases/copy-to`, { ids, targetProjectId })
export const aiReviewCases = (pid, caseIds) =>
  request.post(`/projects/${pid}/testcases/ai-review`, { caseIds }, { timeout: 600000 })
export const aiFixCase = (pid, caseId, issues = []) =>
  request.post(`/projects/${pid}/testcases/ai-fix`, { caseId, issues }, { timeout: 600000 })
export const exportCsv = (pid) => request.get(`/projects/${pid}/testcases/export`, { responseType: 'blob' })
export const importCsv = (pid, file) => {
  const form = new FormData()
  form.append('file', file)
  return request.post(`/projects/${pid}/testcases/import`, form)
}
