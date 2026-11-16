import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Result, Button } from 'antd'
import MainLayout from './layouts/MainLayout'
import { useAuth } from './store/auth'
import Login from './pages/Login'
import Home from './pages/Overview'
import Chat from './pages/AgentChat'
import Specs from './pages/KnowledgeBaseList'
import AskData from './pages/SearchTest'
import Inject from './pages/DocumentList'
import { TasksPage } from './pages/TaskCenter'
import { RecordsPage } from './pages/ChatHistory'
import { EvalPage } from './pages/ConflictList'
import { SystemPage } from './pages/ModelConfig'

function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  if (!user) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

/** 按权限点保护页面：无权限时不渲染白屏，而是给出明确说明 */
function RequirePermission({ perm, children }: { perm: string; children: ReactNode }) {
  const { has } = useAuth()
  if (!has(perm)) {
    return (
      <Result
        status="403"
        title="没有访问权限"
        subTitle={`当前账号缺少权限点「${perm}」。系统只有「管理员 / 普通用户」两种角色，请联系管理员调整你的账号角色。`}
        extra={
          <Button type="primary" href="/home">
            返回首页
          </Button>
        }
      />
    )
  }
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
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
          <Route path="ask-data" element={<AskData />} />
          <Route path="inject" element={<Inject />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="records" element={<RecordsPage />} />
          <Route path="eval" element={<EvalPage />} />
          <Route
            path="system"
            element={
              <RequirePermission perm="system:config">
                <SystemPage />
              </RequirePermission>
            }
          />

          {/* 旧地址保底：书签 / 老链接仍能落到新的一级页 */}
          <Route path="overview" element={<Navigate to="/home" replace />} />
          <Route path="agent" element={<Navigate to="/chat" replace />} />
          <Route path="kb" element={<Navigate to="/specs" replace />} />
          <Route path="search" element={<Navigate to="/specs" replace />} />
          <Route path="prompts" element={<Navigate to="/ask-data" replace />} />
          <Route path="conflicts" element={<Navigate to="/specs" replace />} />
          <Route path="tags" element={<Navigate to="/specs" replace />} />
          <Route path="history" element={<Navigate to="/records" replace />} />
          <Route path="audit" element={<Navigate to="/records" replace />} />
          <Route path="permissions" element={<Navigate to="/system" replace />} />
          <Route path="models" element={<Navigate to="/system" replace />} />
          <Route path="workflows" element={<Navigate to="/tasks" replace />} />
          <Route path="*" element={<Navigate to="/home" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
