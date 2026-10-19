import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd'
import { ClusterOutlined, LockOutlined, UserOutlined } from '@ant-design/icons'
import { useAuth } from '../store/auth'

const { Title, Text, Paragraph } = Typography

export default function Login() {
  const { login, user, loading } = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState<string>('')

  if (user) {
    return <Navigate to="/overview" replace />
  }

  const onFinish = async (values: { username: string; password: string }) => {
    setError('')
    try {
      await login(values.username, values.password)
      navigate('/overview')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="kb-login-wrap">
      <Card className="kb-login-card">
        <Space direction="vertical" size={4} style={{ width: '100%', textAlign: 'center' }}>
          <ClusterOutlined style={{ fontSize: 34, color: '#1677ff' }} />
          <Title level={4} style={{ marginBottom: 0 }}>
            统一知识库与智能问数平台
          </Title>
          <Text type="secondary">知识库 · 文档权限 · 冲突检测 · 智能问数</Text>
        </Space>

        <Form layout="vertical" onFinish={onFinish} style={{ marginTop: 20 }} size="large">
          <Form.Item name="username" rules={[{ required: true, message: '请输入登录账号' }]}>
            <Input prefix={<UserOutlined />} placeholder="登录账号（员工账号）" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>

          {error && (
            <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />
          )}

          <Button type="primary" htmlType="submit" block loading={loading}>
            登录
          </Button>
        </Form>

        <Paragraph type="secondary" style={{ marginTop: 18, marginBottom: 0, fontSize: 12 }}>
          请使用<Text strong>员工账号</Text>登录（与人力资源系统同一套账号密码）。
          <br />
          账号权限由人力资源系统维护：系统管理员自动获得管理权限，其他员工为普通用户。
          密码修改请联系人力资源系统管理员。
        </Paragraph>
      </Card>
    </div>
  )
}
