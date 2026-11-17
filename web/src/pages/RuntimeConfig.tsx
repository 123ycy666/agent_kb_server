import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { SaveOutlined, ThunderboltOutlined, ReloadOutlined } from '@ant-design/icons'
import { runtimeApi } from '../api'

const { Title, Text, Paragraph } = Typography

/** 运行配置：模型 / 业务库连接 / 步数超时。存了立即生效，代码里的 .env 只是默认值。 */
export default function RuntimeConfig() {
  const [data, setData] = useState<any>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<any>(null)

  const load = () =>
    runtimeApi
      .config()
      .then((r: any) => {
        setData(r)
        setValues(r?.values ?? {})
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    void load()
  }, [])

  const set = (k: string, v: string) => setValues((p) => ({ ...p, [k]: v }))

  const save = async () => {
    setSaving(true)
    try {
      const r: any = await runtimeApi.save(values)
      if (r?.error) message.error(r.error)
      else {
        message.success('已保存，下一个请求生效')
        await load()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await runtimeApi.test(values))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setTesting(false)
    }
  }

  const defaults = data?.defaults ?? {}
  const models: any[] = data?.models ?? []

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space align="baseline">
        <Title level={4} style={{ margin: 0 }}>
          运行配置
        </Title>
        <Tag color="blue">存了立即生效</Tag>
      </Space>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        这里管「跑起来的环境」：用哪个模型、连哪个业务库、几步、SQL 超时多久。
        回答内容里的业务规则在「提示词」页。空着 = 用代码里的默认值（见右侧灰字）。
      </Paragraph>

      <Card size="small" title="模型与循环">
        <Form layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label={<span>问数用的模型 <Text type="secondary">默认 {defaults.model}</Text></span>}>
                <Select
                  showSearch
                  allowClear
                  value={values.model || undefined}
                  placeholder={defaults.model}
                  onChange={(v) => set('model', v || '')}
                  options={models.map((m) => ({ value: m.id, label: `${m.label} · ${m.note || ''}` }))}
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item label={<span>步数上限 <Text type="secondary">默认 {defaults.max_steps}</Text></span>}>
                <Input value={values.max_steps ?? ''} placeholder={String(defaults.max_steps)}
                  onChange={(e) => set('max_steps', e.target.value)} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item label={<span>SQL 超时(ms) <Text type="secondary">默认 {defaults.sql_timeout_ms}</Text></span>}>
                <Input value={values.sql_timeout_ms ?? ''} placeholder={String(defaults.sql_timeout_ms)}
                  onChange={(e) => set('sql_timeout_ms', e.target.value)} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Card>

      <Card
        size="small"
        title="业务库连接（只读账号）"
        extra={
          <Button size="small" icon={<ThunderboltOutlined />} loading={testing} onClick={() => void test()}>
            测试连接
          </Button>
        }
      >
        <Form layout="vertical">
          <Row gutter={16}>
            <Col span={10}>
              <Form.Item label={<span>地址 <Text type="secondary">默认 {defaults.db_host}</Text></span>}>
                <Input value={values.db_host ?? ''} placeholder={defaults.db_host}
                  onChange={(e) => set('db_host', e.target.value)} />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item label={<span>端口 <Text type="secondary">{defaults.db_port}</Text></span>}>
                <Input value={values.db_port ?? ''} placeholder={String(defaults.db_port)}
                  onChange={(e) => set('db_port', e.target.value)} />
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item label={<span>库名 <Text type="secondary">默认 {defaults.db_name}</Text></span>}>
                <Input value={values.db_name ?? ''} placeholder={defaults.db_name}
                  onChange={(e) => set('db_name', e.target.value)} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label={<span>账号 <Text type="secondary">默认 {defaults.db_user}</Text></span>}>
                <Input value={values.db_user ?? ''} placeholder={defaults.db_user}
                  onChange={(e) => set('db_user', e.target.value)} />
              </Form.Item>
            </Col>
            <Col span={10}>
              <Form.Item label="密码（留空 = 不改）">
                <Input.Password value={values.db_password ?? ''} onChange={(e) => set('db_password', e.target.value)} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item label=" ">
                <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
                  保存
                </Button>
              </Form.Item>
            </Col>
          </Row>
        </Form>

        {testResult ? (
          <Alert
            type={testResult.ok ? 'success' : 'error'}
            showIcon
            message={testResult.ok ? `连上了：${testResult.host} / ${testResult.db}` : '连不上'}
            description={testResult.ok ? '' : testResult.error}
          />
        ) : null}
      </Card>

      <Card size="small" title="现在生效的值">
        <Space wrap size={16}>
          {Object.entries(defaults).map(([k, v]) => (
            <Text key={k} type="secondary">
              {k}：<Text strong>{String(v ?? '')}</Text>
            </Text>
          ))}
        </Space>
        <div style={{ marginTop: 8 }}>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()}>
            重新读取
          </Button>
          <Text type="secondary" style={{ marginLeft: 8 }}>
            页面存过的项以页面为准；表在 cfg.runtime。
          </Text>
        </div>
      </Card>

      <Card size="small" title="已保存的覆盖项">
        <Table
          size="small"
          rowKey="k"
          pagination={false}
          dataSource={Object.entries(values).map(([k, v]) => ({ k, v }))}
          columns={[
            { title: '键', dataIndex: 'k', width: 200 },
            { title: '值', dataIndex: 'v', render: (v: string) => (v ? String(v) : <Text type="secondary">（用默认）</Text>) },
          ]}
        />
      </Card>
    </Space>
  )
}
