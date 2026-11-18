import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import MainLayout from './layouts/MainLayout'
import { useAuth } from './store/auth'
import Login from './pages/Login'
import Home from './pages/Overview'
import Chat from './pages/AgentChat'
import Specs from './pages/KnowledgeBaseList'
import SchemaMap from './pages/SchemaMap'
import InjectPanels from './pages/InjectPanels'
import RuntimeConfig from './pages/RuntimeConfig'
import { TasksPage } from './pages/TaskCenter'
import { RecordsPage } from './pages/ChatHistory'

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
          <Route index element={<Navigate to="/home" replace />} />
          <Route path="home" element={<Home />} />
          <Route path="chat" element={<Chat />} />
          <Route path="specs" element={<Specs />} />
          <Route path="schema" element={<SchemaMap />} />
          <Route path="inject" element={<InjectPanels />} />
          <Route path="runtime" element={<RuntimeConfig />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="records" element={<RecordsPage />} />

          {/* 旧地址保底：书签 / 老链接仍能落到新的一级页 */}
          <Route path="overview" element={<Navigate to="/home" replace />} />
          <Route path="agent" element={<Navigate to="/chat" replace />} />
          <Route path="kb" element={<Navigate to="/specs" replace />} />
          <Route path="search" element={<Navigate to="/specs" replace />} />
          <Route path="prompts" element={<Navigate to="/inject" replace />} />
          <Route path="conflicts" element={<Navigate to="/specs" replace />} />
          <Route path="tags" element={<Navigate to="/specs" replace />} />
          <Route path="history" element={<Navigate to="/records" replace />} />
          <Route path="audit" element={<Navigate to="/records" replace />} />
          <Route path="permissions" element={<Navigate to="/home" replace />} />
          <Route path="models" element={<Navigate to="/home" replace />} />
          <Route path="system" element={<Navigate to="/home" replace />} />
          <Route path="workflows" element={<Navigate to="/tasks" replace />} />
          <Route path="*" element={<Navigate to="/home" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
