import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import MainLayout from './layouts/MainLayout'
import { useAuth } from './store/auth'
import Login from './pages/Login'
import Overview from './pages/Overview'
import AgentChat from './pages/AgentChat'
import Agent from './pages/Agent'
import Skills from './pages/Skills'
import Kb from './pages/Kb'
import Prompts from './pages/Prompts'
import Alerts from './pages/Alerts'
import Tasks from './pages/Tasks'
import Settings from './pages/Settings'

function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  if (!user) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

export default function App() {
  // 部署在子路径（/sandbox/）时路由也要带前缀；根路径部署时为 undefined，行为不变
  const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || undefined
  return (
    <BrowserRouter basename={basename}>
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
          <Route path="agent" element={<Agent />} />
          <Route path="skills" element={<Skills />} />
          <Route path="kb" element={<Kb />} />
          <Route path="prompts" element={<Prompts />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="settings" element={<Settings />} />

          {/* 旧地址保底：书签 / 老链接仍能落到新的一级页 */}
          <Route path="overview" element={<Navigate to="/home" replace />} />
          <Route path="specs" element={<Navigate to="/kb" replace />} />
          <Route path="inject" element={<Navigate to="/prompts" replace />} />
          <Route path="runtime" element={<Navigate to="/settings" replace />} />
          <Route path="records" element={<Navigate to="/chat?tab=records" replace />} />
          <Route path="schema" element={<Navigate to="/kb?tab=libs" replace />} />
          <Route path="workflows" element={<Navigate to="/alerts" replace />} />
          <Route path="conflicts" element={<Navigate to="/kb" replace />} />
          <Route path="tags" element={<Navigate to="/kb" replace />} />
          <Route path="search" element={<Navigate to="/kb?tab=search" replace />} />
          <Route path="permissions" element={<Navigate to="/settings" replace />} />
          <Route path="models" element={<Navigate to="/settings" replace />} />
          <Route path="system" element={<Navigate to="/settings" replace />} />
          <Route path="history" element={<Navigate to="/chat?tab=records" replace />} />
          <Route path="audit" element={<Navigate to="/chat?tab=records" replace />} />
          <Route path="*" element={<Navigate to="/home" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
