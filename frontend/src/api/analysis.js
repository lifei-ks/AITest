import request from './request'

// 代码分析 API（对应后端 /api/projects/{pid}/analyze、/analysis、/state-machines）
export const triggerAnalysis = (id) => request.post(`/projects/${id}/analyze`)
export const getAnalysis = (id, config) => request.get(`/projects/${id}/analysis`, config)
export const getStateMachines = (id) => request.get(`/projects/${id}/state-machines`)
