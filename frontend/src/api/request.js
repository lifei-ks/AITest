import axios from 'axios'
import { ElMessage } from 'element-plus'

// 统一响应拦截：code === 0 成功；否则抛出带业务码的错误并提示
// 单次请求可通过 { silent: true } 关闭错误提示（如健康检查、轮询）
const request = axios.create({ baseURL: '/api', timeout: 30000 })

export class ApiError extends Error {
  constructor(message, { code, status, data, cause } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.data = data
    this.cause = cause
  }
}

// 相同文案 1.5s 内只提示一次，避免并发失败时刷屏
const lastToast = { text: '', at: 0 }
function notify(text) {
  if (!text) return
  const now = Date.now()
  if (text === lastToast.text && now - lastToast.at < 1500) return
  lastToast.text = text
  lastToast.at = now
  ElMessage.error(text)
}

function netErrorText(err) {
  if (err.code === 'ECONNABORTED' || /timeout/i.test(err.message || '')) return '请求超时，请稍后重试'
  if (err.code === 'ERR_NETWORK') return '无法连接后端服务，请确认服务已启动'
  return err.message || '网络错误'
}

// 导出类接口（responseType=blob）失败时后端仍返回 JSON 信封
async function readBlobError(blob) {
  try {
    return JSON.parse(await blob.text())
  } catch {
    return null
  }
}

request.interceptors.response.use(
  async (resp) => {
    const body = resp.data
    if (body instanceof Blob) {
      if (body.type?.includes('json')) {
        const payload = await readBlobError(body)
        const msg = payload?.message || '下载失败'
        if (!resp.config?.silent) notify(msg)
        throw new ApiError(msg, { code: payload?.code, status: resp.status })
      }
      return body
    }
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) return body.data
      const msg = body.message || '请求失败'
      if (!resp.config?.silent) notify(msg)
      throw new ApiError(msg, { code: body.code, status: resp.status, data: body.data })
    }
    return body
  },
  async (err) => {
    if (err.code === 'ERR_CANCELED') return Promise.reject(err)
    let body = err.response?.data
    // 下载类接口失败时响应体是 Blob，需要先解析出 JSON 信封
    if (body instanceof Blob && body.type?.includes('json')) {
      body = await readBlobError(body)
    }
    const msg = body?.message || netErrorText(err)
    if (!err.config?.silent) notify(msg)
    return Promise.reject(
      new ApiError(msg, {
        code: body?.code,
        status: err.response?.status,
        data: body?.data,
        cause: err,
      }),
    )
  },
)

export default request
