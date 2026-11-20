import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { siteApi } from '../api'

export interface SiteStatus {
  configured: boolean
  ok: boolean
  error: string
  site_id: string
  site: { host: string; port: number; dbname: string; user: string }
  business_empty: boolean
  system_token_configured: boolean
}

interface SiteContextValue {
  status: SiteStatus | null
  loading: boolean
  /** 现场能否正常工作（连不上/没配置 = false → 只呈现现场配置页） */
  ready: boolean
  refresh: () => Promise<void>
}

const SiteContext = createContext<SiteContextValue | null>(null)

const DOWN: SiteStatus = {
  configured: false,
  ok: false,
  error: '无法连接后端服务',
  site_id: '',
  site: { host: '', port: 0, dbname: '', user: '' },
  business_empty: true,
  system_token_configured: false,
}

/**
 * 现场状态：全局唯一来源（UI-002）。
 *
 * 未配置 / 连不上时 `ready=false`，`App` 只渲染「现场配置」页 —— 登录页与业务页都不出现。
 * 降级期间每 5 秒重试一次状态（后端连上库就能自愈，不用刷新页面）。
 */
export function SiteProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SiteStatus | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const s = (await siteApi.status()) as SiteStatus
      setStatus(s)
    } catch {
      setStatus(DOWN)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const onDown = () => void refresh()
    window.addEventListener('kb-site-down', onDown)
    return () => window.removeEventListener('kb-site-down', onDown)
  }, [refresh])

  useEffect(() => {
    if (!status || status.ok) return
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => window.clearInterval(timer)
  }, [status, refresh])

  const value = useMemo(
    () => ({ status, loading, ready: !!status?.ok, refresh }),
    [status, loading, refresh]
  )

  return <SiteContext.Provider value={value}>{children}</SiteContext.Provider>
}

export function useSite() {
  const ctx = useContext(SiteContext)
  if (!ctx) throw new Error('useSite 必须在 SiteProvider 内使用')
  return ctx
}
