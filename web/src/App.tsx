import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import MainLayout from './layouts/MainLayout'
import { useAuth } from './store/auth'
import { useSite } from './store/site'
import Login from './pages/Login'
import SiteConfig from './pages/SiteConfig'
import Overview from './pages/Overview'
import AgentChat from './pages/AgentChat'
import ScreenAsk from './pages/ScreenAsk'
import Agent from './pages/Agent'
import Skills from './pages/Skills'
import Kb from './pages/Kb'
import Prompts from './pages/Prompts'
import Flows from './pages/Flows'
import { Spin } from 'antd'

function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  if (!user) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

/**
 * 部署前缀由**运行时**决定（FR-FE-001）：包内不写死，nginx 剥前缀。
 * 根路径部署 → undefined；挂在 /sandbox/ 下 → '/sandbox'。
 */
function runtimeBasename(): string | undefined {
  const p = new URL('./', window.location.href).pathname.replace(/\/$/, '')
  return p || undefined
}

export default function App() {
  const { loading, ready } = useSite()

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}>
        <Spin />
      </div>
    )
  }

  // UI-002：未配置 / 连不上时只呈现现场配置页，登录页与业务页都不出现。
  if (!ready) {
    return <SiteConfig />
  }

  return (
    <BrowserRouter basename={runtimeBasename()}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="home" element={<Overview />} />
          <Route path="chat" element={<AgentChat />} />
          <Route path="screen" element={<ScreenAsk />} />
          <Route path="agent" element={<Agent />} />
          <Route path="skills" element={<Skills />} />
          <Route path="kb" element={<Kb />} />
          <Route path="prompts" element={<Prompts />} />
          <Route path="alerts" element={<Flows />} />
          <Route path="tasks" element={<Navigate to="/alerts" replace />} />
          {/* 「配置」= 原「设置」+「现场」合成一页（导航只剩一个入口） */}
          <Route path="site" element={<SiteConfig />} />
          <Route path="settings" element={<Navigate to="/site" replace />} />
          <Route path="config" element={<Navigate to="/site" replace />} />

          {/* 旧地址保底：书签 / 老链接仍能落到新的一级页 */}
          <Route path="overview" element={<Navigate to="/home" replace />} />
          <Route path="specs" element={<Navigate to="/kb" replace />} />
          <Route path="inject" element={<Navigate to="/prompts" replace />} />
          <Route path="runtime" element={<Navigate to="/site" replace />} />
          <Route path="records" element={<Navigate to="/chat?tab=records" replace />} />
          <Route path="schema" element={<Navigate to="/kb?tab=libs" replace />} />
          <Route path="workflows" element={<Navigate to="/alerts" replace />} />
          <Route path="conflicts" element={<Navigate to="/kb" replace />} />
          <Route path="tags" element={<Navigate to="/kb" replace />} />
          <Route path="search" element={<Navigate to="/kb?tab=search" replace />} />
          <Route path="permissions" element={<Navigate to="/site" replace />} />
          <Route path="models" element={<Navigate to="/site" replace />} />
          <Route path="system" element={<Navigate to="/site" replace />} />
          <Route path="history" element={<Navigate to="/chat?tab=records" replace />} />
          <Route path="audit" element={<Navigate to="/chat?tab=records" replace />} />
          <Route path="*" element={<Navigate to="/home" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
