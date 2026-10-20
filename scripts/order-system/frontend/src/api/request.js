import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '/api',
  timeout: 15000
})

request.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && body.code === 0) {
      return body
    }
    const message = (body && body.message) || '请求失败'
    ElMessage.error(message)
    return Promise.reject(new Error(message))
  },
  (error) => {
    const message =
      (error.response && error.response.data && error.response.data.message) ||
      error.message ||
      '网络异常，请稍后重试'
    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default request
