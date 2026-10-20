import request from './request'

export const getSettings = () => request.get('/settings')
export const updateSettings = (data) => request.put('/settings', data)
export const testLlm = () => request.post('/settings/test-llm')
