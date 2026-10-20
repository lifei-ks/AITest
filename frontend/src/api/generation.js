import request from './request'

// 用例生成 API
export const generate = (id, payload = {}) => request.post(`/projects/${id}/generate`, payload)
// SSE 流式生成（返回 EventSource，需在组件里 addEventListener）
export const generateStream = (id) => new EventSource(`/api/projects/${id}/generate/stream`)
