import request from './request'

// 兼容旧配置接口（默认 profile）
export const getSettings = () => request.get('/settings')
export const updateSettings = (data) => request.put('/settings', data)
export const testLlm = (data = {}) => request.post('/settings/test-llm', data)

// 多 LLM 配置：text / vision 各自多条
export const listLlmProfiles = () => request.get('/settings/llm-profiles')
export const createLlmProfile = (data) => request.post('/settings/llm-profiles', data)
export const updateLlmProfile = (id, data) => request.put(`/settings/llm-profiles/${id}`, data)
export const deleteLlmProfile = (id) => request.delete(`/settings/llm-profiles/${id}`)
export const setDefaultLlmProfile = (id) => request.post(`/settings/llm-profiles/${id}/default`)
export const testLlmProfile = (id) => request.post(`/settings/llm-profiles/${id}/test`)
