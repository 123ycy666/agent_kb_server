import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Result, Button } from 'antd'
import MainLayout from './layouts/MainLayout'
import { useAuth } from './store/auth'
import Login from './pages/Login'
import Overview from './pages/Overview'
import KnowledgeBaseList from './pages/KnowledgeBaseList'
import DocumentList from './pages/DocumentList'
import SearchTest from './pages/SearchTest'
import ConflictList from './pages/ConflictList'
import TagManagement from './pages/TagManagement'
import PermissionManagement from './pages/PermissionManagement'
import AuditLogs from './pages/AuditLogs'
import ModelConfig from './pages/ModelConfig'
import TaskCenter from './pages/TaskCenter'
import AgentChat from './pages/AgentChat'

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
          <Button type="primary" href="/kb">
            返回知识库
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
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<Overview />} />
          <Route path="kb" element={<KnowledgeBaseList />} />
          <Route path="kb/:kbId/documents" element={<DocumentList />} />
          <Route path="search" element={<SearchTest />} />
          {/* 智能问答：SPA 内页面，iframe 嵌后端 /agent/（侧边栏与身份区保留） */}
          <Route path="agent" element={<AgentChat />} />
          <Route path="conflicts" element={<ConflictList />} />
          <Route
            path="tags"
            element={
              <RequirePermission perm="tag:read">
                <TagManagement />
              </RequirePermission>
            }
          />
          <Route
            path="permissions"
            element={
              <RequirePermission perm="user:manage">
                <PermissionManagement />
              </RequirePermission>
            }
          />
          <Route
            path="audit"
            element={
              <RequirePermission perm="audit:read">
                <AuditLogs />
              </RequirePermission>
            }
          />
          <Route
            path="models"
            element={
              <RequirePermission perm="system:config">
                <ModelConfig />
              </RequirePermission>
            }
          />
          <Route path="tasks" element={<TaskCenter />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
