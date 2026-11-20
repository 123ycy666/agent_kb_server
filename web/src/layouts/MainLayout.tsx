import { useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Avatar, Dropdown, Layout, Menu, Typography } from 'antd'
import {
  AlertOutlined,
  AppstoreOutlined,
  BookOutlined,
  CloudServerOutlined,
  ClusterOutlined,
  DeploymentUnitOutlined,
  DesktopOutlined,
  LogoutOutlined,
  MessageOutlined,
  RobotOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useAuth } from '../store/auth'
import { uiPrefs } from '../components/ui/prefs'
import { useSite } from '../store/site'

const { Sider, Content } = Layout
const { Text } = Typography

const NAV = [
  { key: '/home', icon: <AppstoreOutlined />, label: '概览' },
  { key: '/chat', icon: <MessageOutlined />, label: '对话' },
  { key: '/screen', icon: <DesktopOutlined />, label: '大屏问数' },
  { key: '/agent', icon: <RobotOutlined />, label: '智能体' },
  { key: '/skills', icon: <ThunderboltOutlined />, label: '技能' },
  { key: '/kb', icon: <BookOutlined />, label: '知识库' },
  { key: '/prompts', icon: <ClusterOutlined />, label: '提示词' },
  { key: '/alerts', icon: <AlertOutlined />, label: '预警' },
  { key: '/tasks', icon: <DeploymentUnitOutlined />, label: '任务' },
  { key: '/site', icon: <CloudServerOutlined />, label: '配置' },
]

export default function MainLayout() {
  const { user, logout } = useAuth()
  const { status } = useSite()
  const navigate = useNavigate()
  const location = useLocation()
  const [prefs, setPrefs] = useState(() => uiPrefs())

  useEffect(() => {
    const onPrefs = () => setPrefs(uiPrefs())
    window.addEventListener('kb-ui-prefs', onPrefs)
    return () => window.removeEventListener('kb-ui-prefs', onPrefs)
  }, [])

  const dark = prefs.sider === 'dark'

  const selectedKey = useMemo(() => {
    const seg = '/' + (location.pathname.split('/')[1] || 'home')
    return NAV.some((n) => n.key === seg) ? seg : '/home'
  }, [location.pathname])

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={220}
        theme={dark ? 'dark' : 'light'}
        style={{
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
          height: '100vh',
          background: dark ? '#001529' : '#fff',
          borderRight: dark ? undefined : '1px solid #f0f0f0',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div className="kb-logo" style={dark ? undefined : { background: '#fff', color: '#1677ff' }}>
            <ClusterOutlined style={{ fontSize: 18 }} />
            <span style={{ marginLeft: 8 }}>秘书智能体</span>
          </div>
          <Menu
            theme={dark ? 'dark' : 'light'}
            mode="inline"
            selectedKeys={[selectedKey]}
            items={NAV.map((n) => ({
              key: n.key,
              icon: n.icon,
              label: <Link to={n.key}>{n.label}</Link>,
            }))}
            style={{ borderInlineEnd: 0 }}
          />
          <div
            style={{
              marginTop: 'auto',
              padding: 12,
              borderTop: `1px solid ${dark ? 'rgba(255,255,255,.12)' : '#f0f0f0'}`,
            }}
          >
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
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <Avatar size="small" icon={<UserOutlined />} />
                <Text
                  style={{ color: dark ? 'rgba(255,255,255,.85)' : undefined, fontSize: 13 }}
                  ellipsis
                >
                  {user?.display_name || user?.username}
                </Text>
              </div>
            </Dropdown>
          </div>
        </div>
      </Sider>

      <Layout style={{ marginLeft: 220 }}>
        <Content style={{ padding: 16, minHeight: '100vh', fontSize: prefs.font }}>
          {/* UI-004：业务配置为空的现场，先说清「还没配」，不显示任何现场内容 */}
          {status?.business_empty && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="该现场尚未配置"
              description="这个库还没有写入业务配置。请先到「现场配置」或「提示词 / 知识库」把本现场的内容配进去。"
            />
          )}
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
