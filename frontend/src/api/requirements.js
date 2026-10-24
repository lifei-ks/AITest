import request from './request'

// 需求版本（一期需求 = 一个版本）
export const listRequirements = (pid) => request.get(`/projects/${pid}/requirements`)
export const createRequirement = (pid, data) => request.post(`/projects/${pid}/requirements`, data)
export const getRequirement = (rid) => request.get(`/requirements/${rid}`)
export const updateRequirement = (rid, data) => request.put(`/requirements/${rid}`, data)
export const deleteRequirement = (rid) => request.delete(`/requirements/${rid}`)
export const uploadRequirementPrd = (rid, file) => {
  const form = new FormData()
  form.append('file', file)
  return request.post(`/requirements/${rid}/upload`, form, { timeout: 120000 })
}

// 需求文档 → 测试点（LLM 优先，rule 兜底），后台任务
export const analyzeRequirement = (rid, mode = 'auto', profileId = null) =>
  request.post(`/requirements/${rid}/analyze`, null, {
    params: { mode, ...(profileId ? { profileId } : {}) },
  })
export const listTestPoints = (rid) => request.get(`/requirements/${rid}/test-points`)

// 按需求版本生成用例（支持流式）
export const generateRequirementCases = (rid, mode = 'auto', profileId = null) =>
  request.post(
    `/requirements/${rid}/generate`,
    { mode, llmProfileId: profileId || null },
    { timeout: 600000 },
  )
export const generateRequirementStream = (rid, mode = 'auto', profileId = null) => {
  const query = new URLSearchParams({ mode })
  if (profileId) query.set('profileId', profileId)
  return new EventSource(`/api/requirements/${rid}/generate/stream?${query.toString()}`)
}
