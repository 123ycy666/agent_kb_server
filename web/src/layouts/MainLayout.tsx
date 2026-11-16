import { useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Avatar,
  Dropdown,
  Layout,
  Menu,
  Space,
  Tag,
  Typography,
  theme,
} from 'antd'
import {
  BarChartOutlined,
  BookOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  HistoryOutlined,
  HomeOutlined,
  LogoutOutlined,
  MessageOutlined,
  SettingOutlined,
  ThunderboltOutlined,
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
    const seg = p.split('/')[1] || 'home'
    return `/${seg}`
  }, [location.pathname])

  const items = [
    { key: '/home', icon: <HomeOutlined />, label: <Link to="/home">首页</Link> },
    { key: '/chat', icon: <MessageOutlined />, label: <Link to="/chat">智能问答</Link> },
    { key: '/specs', icon: <BookOutlined />, label: <Link to="/specs">知识库</Link> },
    { key: '/ask-data', icon: <BarChartOutlined />, label: <Link to="/ask-data">问数</Link> },
    {
      key: '/inject',
      icon: <ThunderboltOutlined />,
      label: <Link to="/inject">提示词与注入</Link>,
    },
    {
      key: '/tasks',
      icon: <DeploymentUnitOutlined />,
      label: <Link to="/tasks">任务与预警</Link>,
    },
    { key: '/records', icon: <HistoryOutlined />, label: <Link to="/records">记录</Link> },
    { key: '/schema', icon: <DatabaseOutlined />, label: <Link to="/schema">库结构</Link> },
    { key: '/eval', icon: <ExperimentOutlined />, label: <Link to="/eval">评测</Link> },
    {
      key: '/system',
      icon: <SettingOutlined />,
      label: <Link to="/system">系统</Link>,
    },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} width={216}>
        <div className="kb-logo">
          <ClusterOutlined style={{ fontSize: 20 }} />
          {!collapsed && <span style={{ marginLeft: 8 }}>秘书智能体</span>}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]} items={items} />
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
          <Text strong>统一知识库与智能问答平台 · 管理端</Text>
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
