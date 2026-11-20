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
 *  故问数侧用 baseURL 为 `api` 的独立实例，url 直接写全路径。
 *
 * ★ 2026-11-19 单包化（FR-FE-001）：baseURL **不带前导斜杠**，拼出来就是相对路径
 *   （`api/v1/kb`）。站点挂在 `/` 还是 `/sandbox/` 由 nginx 剥前缀决定，包内不写死前缀 ——
 *   同一份产物可以同时喂给两个实例。
 */
function createClient(baseURL: string) {
  const inst = axios.create({ baseURL, timeout: 180000 })

  inst.interceptors.request.use((config) => {
    const token = getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    // ★ 一律走**相对路径**：去掉 url 的前导斜杠，浏览器按当前页面 URL 解析。
    //   这样根路径站点是 /api/...，带前缀站点自动是 /sandbox/api/...（前缀由 nginx 剥）。
    //   调用方仍然照旧写 '/api/sessions'、'/kb'，不用改上百处。
    if (config.url && config.url.startsWith('/')) {
      config.url = config.url.slice(1)
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

      // 现场没配好：不跳登录，交给现场配置页
      const needSetup = code === 'SITE_NOT_CONFIGURED'
      if (needSetup) {
        window.dispatchEvent(new Event('kb-site-down'))
      }

      if (status === 401 && !needSetup) {
        clearToken()
        if (!location.pathname.endsWith('/login')) {
          location.replace('./login')
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

/** 知识库 / 管理面接口：`api/v1/*`（相对路径，前缀由 nginx 剥） */
export const http = createClient('api/v1')

/**
 * 问数 / 智能体 / 工作流接口。
 * ⚠️ 调用方写的是完整路径（'/api/sessions'、'/api/workflows/docs'），所以 baseURL 必须为空：
 *    前导斜杠由请求拦截器去掉，浏览器按当前页面 URL 解析成相对路径。
 *    （baseURL 若再给 'api'，拼出来就是 api/api/sessions → 404，2026-11-19 踩过。）
 */
export const agentHttp = createClient('')

/** 现场配置接口：`api/v1/site/*` */
export const siteHttp = createClient('api/v1/site')
