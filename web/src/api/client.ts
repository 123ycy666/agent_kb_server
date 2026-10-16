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

export const http = axios.create({
  baseURL: '/api/v1',
  timeout: 180000,
})

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export interface ApiError extends Error {
  code?: string
  status?: number
  required?: string[]
}

http.interceptors.response.use(
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
      msg = '无法连接后端服务，请确认服务已启动（默认 127.0.0.1:8000）'
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
