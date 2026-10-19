import axios, { AxiosError } from 'axios'

const TOKEN_KEY = 'kb_token'
const USER_KEY = 'kb_user'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}
export const getStoredUser = () => {
  const raw = localStorage.getItem(USER_KEY)
  try {
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}
export const setStoredUser = (u: unknown) =>
  localStorage.setItem(USER_KEY, JSON.stringify(u))

export interface ApiError extends Error {
  code?: string
  status?: number
  required?: string[]
}

/**
 * 统一的 axios 工厂：请求自动带 JWT、响应自动解包 data、401 跳登录。
 *
 * ⚠️ 为什么需要两个实例：本项目接口是**分区**的 ——
 *    · 知识库 / 管理面：`/api/v1/*`
 *    · 问数（智能问答）：`/api/*`（**不带 v1**，见 docs/接口契约.md）
 *  而 axios 会拼接 baseURL 与 url（url 以 "/" 开头也会拼），
 *  若用 v1 实例调 `/api/sessions`，最终会变成 `/api/v1/api/sessions` → 404。
 *  故问数侧用 baseURL 为空串的独立实例，url 直接写全路径。
 */
function createClient(baseURL: string) {
  const inst = axios.create({ baseURL, timeout: 180000 })

  inst.interceptors.request.use((config) => {
    const token = getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  })

  inst.interceptors.response.use(
    (res) => res.data,
    (error: AxiosError<any>) => {
      const status = error.response?.status
      const detail = error.response?.data?.detail
      let code = 'UNKNOWN'
      let msg = error.message

      if (typeof detail === 'string') {
        msg = detail
      } else if (detail && typeof detail === 'object') {
        code = detail.code ?? code
        msg = detail.message ?? msg
      } else if (!error.response) {
        msg = '无法连接后端服务，请确认服务已启动（默认 127.0.0.1:8200）'
      }

      if (status === 401) {
        clearToken()
        // 应用身份（API Key）请求失败时不跳转登录页
        if (!location.pathname.startsWith('/login')) {
          location.href = '/login'
        }
      }

      const err: ApiError = new Error(msg)
      err.code = code
      err.status = status
      if (detail && typeof detail === 'object' && detail.required) {
        err.required = detail.required
      }
      return Promise.reject(err)
    }
  )

  return inst
}

/** 知识库 / 管理面接口：`/api/v1/*` */
export const http = createClient('/api/v1')

/** 问数（智能问答）接口：`/api/*`（无 v1 前缀） */
export const agentHttp = createClient('')
