import request from './request'

// 执行可能耗时较长（Playwright 逐步执行 + 截图录屏），覆盖默认 30s 超时
export const executeCase = (pid, cid, targetUrl, mode = 'programmatic') =>
  request.post(`/projects/${pid}/testcases/${cid}/execute`, { targetUrl, mode }, { timeout: 180000 })
export const batchExecute = (pid, caseIds, targetUrl, mode = 'programmatic') =>
  request.post(`/projects/${pid}/testcases/batch-execute`, { caseIds, targetUrl, mode }, { timeout: 180000 })

// 人工复核：decision = pass / fail
export const manualReview = (eid, decision, note) =>
  request.post(`/executions/${eid}/review`, { decision, note })
// 重新调用视觉模型审核
export const rerunLlmReview = (eid) => request.post(`/executions/${eid}/llm-review`, null, { timeout: 180000 })
// 批量 AI 审核（串行，单次上限 20）
export const batchLlmReview = (executionIds) =>
  request.post('/executions/batch-llm-review', { executionIds }, { timeout: 600000 })
