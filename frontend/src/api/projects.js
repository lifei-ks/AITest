import request from './request'

// 项目管理 API 封装（对应后端 /api/projects）
export const listProjects = (keyword) => request.get('/projects', { params: keyword ? { keyword } : {} })
export const createProject = (data) => request.post('/projects', data)
export const getProject = (id) => request.get(`/projects/${id}`)
export const updateProject = (id, data) => request.put(`/projects/${id}`, data)
export const deleteProject = (id) => request.delete(`/projects/${id}`)
export const getPrd = (id) => request.get(`/projects/${id}/prd`)
export const setPrd = (id, content) => request.put(`/projects/${id}/prd`, { content })
export const uploadPrd = (id, file) => {
  const form = new FormData()
  form.append('file', file)
  return request.post(`/projects/${id}/prd/upload`, form, { timeout: 120000 })
}
