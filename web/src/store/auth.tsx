import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { authApi } from '../api'
import { clearToken, getStoredUser, getToken, setStoredUser, setToken } from '../api/client'

export interface AuthUser {
  id: number
  username: string
  display_name?: string
}

interface AuthContextValue {
  user: AuthUser | null
  roles: string[]
  permissions: string[]
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  has: (...codes: string[]) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser())
  const [roles, setRoles] = useState<string[]>(() => getStoredUser()?.roles ?? [])
  const [permissions, setPermissions] = useState<string[]>(
    () => getStoredUser()?.permissions ?? []
  )
  const [loading, setLoading] = useState(false)

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
    setRoles([])
    setPermissions([])
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    setLoading(true)
    try {
      const res = await authApi.login(username, password)
      setToken(res.access_token)
      const payload = {
        ...res.user,
        roles: res.roles,
        permissions: res.permissions,
      }
      setStoredUser(payload)
      setUser(res.user)
      setRoles(res.roles)
      setPermissions(res.permissions)
    } finally {
      setLoading(false)
    }
  }, [])

  // 刷新页面后用 /auth/me 校正权限（token 有效但本地缓存过期时）
  useEffect(() => {
    if (!getToken()) return
    authApi
      .me()
      .then((me) => {
        setPermissions(me.permissions ?? [])
        setRoles(me.roles ?? [])
      })
      .catch(() => {
        /* 由拦截器统一处理 401 */
      })
  }, [])

  const has = useCallback(
    (...codes: string[]) => codes.length === 0 || codes.some((c) => permissions.includes(c)),
    [permissions]
  )

  const value = useMemo(
    () => ({ user, roles, permissions, loading, login, logout, has }),
    [user, roles, permissions, loading, login, logout, has]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return ctx
}
