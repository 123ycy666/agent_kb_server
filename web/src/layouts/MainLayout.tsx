import { useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Avatar,
  Badge,
  Dropdown,
  Layout,
  Menu,
  Space,
  Tag,
  Typography,
  theme,
} from 'antd'
import {
  AuditOutlined,
  BookOutlined,
  ClusterOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  ExclamationCircleOutlined,
  HistoryOutlined,
  LogoutOutlined,
  MessageOutlined,
  SearchOutlined,
  SettingOutlined,
  TagsOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useAuth } from '../store/auth'

const { Header, Sider, Content } = Layout
const { Text } = Typography

export default function MainLayout() {
  const { user, roles, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const {
    token: { colorBgContainer },
  } = theme.useToken()

  const selectedKey = useMemo(() => {
    const p = location.pathname
    if (p.startsWith('/kb')) return '/kb'
    const seg = p.split('/')[1] || 'overview'
    return `/${seg}`
  }, [location.pathname])

  const items = [
    { key: '/overview', icon: <DashboardOutlined />, label: <Link to="/overview">概览</Link> },
    { key: '/kb', icon: <BookOutlined />, label: <Link to="/kb">知识库</Link> },
    { key: '/search', icon: <SearchOutlined />, label: <Link to="/search">检索测试</Link> },
    {
      key: '/agent',
      icon: <MessageOutlined />,
      // 2026-10-15 融合改造：问答页现在是 SPA 内的路由页（内部 iframe 嵌 /agent/），
      // 因此用 <Link> 在框架内切换 —— 侧边栏、身份区保留，不再整页跳出。
      label: <Link to="/agent">智能问答</Link>,
    },
    { key: '/history', icon: <HistoryOutlined />, label: <Link to="/history">历史对话</Link> },
    {
      key: '/conflicts',
      icon: <ExclamationCircleOutlined />,
      label: <Link to="/conflicts">冲突管理</Link>,
    },
    { key: '/tags', icon: <TagsOutlined />, label: <Link to="/tags">标签管理</Link> },
    { key: '/tasks', icon: <DeploymentUnitOutlined />, label: <Link to="/tasks">任务中心</Link> },
    { type: 'divider' as const },
    {
      key: '/permissions',
      icon: <TeamOutlined />,
      label: <Link to="/permissions">员工列表</Link>,
    },
    { key: '/audit', icon: <AuditOutlined />, label: <Link to="/audit">审计日志</Link> },
    { key: '/models', icon: <SettingOutlined />, label: <Link to="/models">模型配置</Link> },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} width={216}>
        <div className="kb-logo">
          <ClusterOutlined style={{ fontSize: 20 }} />
          {!collapsed && <span style={{ marginLeft: 8 }}>统一智能平台</span>}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={items}
        />
      </Sider>

      <Layout>
        <Header
          style={{
            background: colorBgContainer,
            padding: '0 20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <Text strong>统一知识库与智能问数平台</Text>
          <Space size="middle">
            {roles.map((r) => (
              <Tag key={r} color={r === 'admin' ? 'gold' : 'blue'}>
                {r}
              </Tag>
            ))}
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'logout',
                    icon: <LogoutOutlined />,
                    label: '退出登录',
                    onClick: () => {
                      logout()
                      navigate('/login')
                    },
                  },
                ],
              }}
            >
              <Space style={{ cursor: 'pointer' }}>
                <Avatar size="small" icon={<UserOutlined />} />
                <Text>{user?.display_name || user?.username}</Text>
              </Space>
            </Dropdown>
          </Space>
        </Header>

        <Content style={{ margin: 16 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
