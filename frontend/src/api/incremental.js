import request from './request'

// 需求版本接口快照：增量对比的只读基线
export const getRequirementSnapshot = (rid) => request.get(`/requirements/${rid}/snapshot`)
export const createRequirementSnapshot = (rid, analysisId = null) =>
  request.post(`/requirements/${rid}/snapshot`, { analysisId })

// 变更分析与增量生成
export const analyzeIncremental = (rid, data) =>
  request.post(`/requirements/${rid}/incremental/analyze`, data, { timeout: 120000 })
export const listIncrementalRuns = (rid, limit = 20) =>
  request.get(`/requirements/${rid}/incremental/runs`, { params: { limit } })
export const getLatestIncrementalRun = (rid) =>
  request.get(`/requirements/${rid}/incremental/latest`)
export const getIncrementalRun = (runId) => request.get(`/incremental/runs/${runId}`)
export const generateIncremental = (runId, data = {}) =>
  request.post(`/incremental/runs/${runId}/generate`, data, { timeout: 600000 })

// 人工确认变更与回归推荐
export const reviewIncrementalChange = (runId, changeId, status) =>
  request.post(`/incremental/runs/${runId}/changes/${changeId}/review`, { status })
export const reviewIncrementalRegression = (caseChangeId, status) =>
  request.post(`/incremental/case-changes/${caseChangeId}/regression`, { status })
