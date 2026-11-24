import { useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Form, Input, InputNumber, Row, Select, Space, Tabs, Tag, Typography, message } from 'antd'
import { ApiOutlined, CloudServerOutlined, DatabaseOutlined, RobotOutlined, SaveOutlined, SettingOutlined } from '@ant-design/icons'
import { siteApi } from '../api'
import Settings from './Settings'
import { useSite } from '../store/site'

const { Title, Text, Paragraph } = Typography

// 用过的现场：{label, pg, admin_username}。只存产品库连接与管理员账号，
// **不存管理员口令**（保存时口令留空即可：账号已存在就跳过、不覆盖口令）。
const SITES_KEY = 'kb_sites'
type SavedSite = { label: string; pg: Record<string, unknown>; admin_username: string }
const loadSites = (): SavedSite[] => {
  try {
    const raw = localStorage.getItem(SITES_KEY)
    return raw ? (JSON.parse(raw) as SavedSite[]) : []
  } catch {
    return []
  }
}
const rememberSite = (pg: any, adminUsername: string) => {
  const list = loadSites().filter((x) => x.pg?.dbname !== pg?.dbname)
  list.unshift({ label: String(pg?.dbname || '未命名'), pg, admin_username: adminUsername })
  localStorage.setItem(SITES_KEY, JSON.stringify(list.slice(0, 8)))
}

type TestResult = { ok: boolean; detail: string; ms?: number } | null

function TestLine({ result }: { result: TestResult }) {
  if (!result) return null
  return (
    <Alert
      style={{ marginTop: 8 }}
      type={result.ok ? 'success' : 'error'}
      showIcon
      message={result.ok ? '连通正常' : '连不上'}
      description={<Text style={{ fontSize: 12 }}>{result.detail}{result.ms != null ? ` · ${result.ms}ms` : ''}</Text>}
    />
  )
}

/**
 * 现场配置页（UI-001）。
 *
 * 未配置 / 连不上时，这是**唯一可访问的页面**：登录页与业务页都不出现（UI-002）。
 * 三段各带一次连通性测试，保存后立即重连新库（FR-CFG-002）；
 * 初始管理员已存在时只跳过、不覆盖口令。
 */
export default function SiteConfig() {
  const { status, refresh } = useSite()
  const [pgForm] = Form.useForm()
  const [dbForm] = Form.useForm()
  const [modelForm] = Form.useForm()
  const [pgTest, setPgTest] = useState<TestResult>(null)
  const [dbTest, setDbTest] = useState<TestResult>(null)
  const [modelTest, setModelTest] = useState<TestResult>(null)
  const [saving, setSaving] = useState(false)
  const [dbSource, setDbSource] = useState('')
  const [sites, setSites] = useState<SavedSite[]>(() => loadSites())
  const [switching, setSwitching] = useState('')

  const quickSwitch = async (site: SavedSite) => {
    setSwitching(site.label)
    try {
      const r = await siteApi.save({
        pg: site.pg,
        admin: { username: site.admin_username, password: '' },
      })
      if (r.ok) {
        message.success(`已切到 ${site.label}`)
        await refresh()
        setSites(loadSites())
      } else {
        message.error(r.error || '切换失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSwitching('')
    }
  }

  // 现场已就绪时（管理员从「现场」菜单进来）预填当前值，方便只改一项
  useEffect(() => {
    if (!status?.ok) return
    siteApi
      .config()
      .then((c: any) => {
        pgForm.setFieldsValue({ ...c.pg, admin_username: c.admin?.username })
        dbForm.setFieldsValue(c.mysql ?? {})
        setDbSource(c.mysql_source || '')
        modelForm.setFieldsValue({
          model_channel: c.model?.channel || 'online',
          llm_base_url: c.model?.base_url ?? '',
          llm_model: c.model?.model ?? '',
        })
      })
      .catch(() => undefined)
    // 依赖里带 site_id：切完现场（保存并重连）要立刻按新库重读，否则三个页签停在上一个库的值
  }, [status?.ok, status?.site_id, pgForm, dbForm, modelForm])

  const testPg = async () => {
    const v = await pgForm.validateFields()
    setPgTest(null)
    try {
      const r = await siteApi.test({ target: 'pg', pg: v })
      setPgTest({ ok: !!r.ok, detail: r.detail, ms: r.ms })
    } catch (e) {
      setPgTest({ ok: false, detail: (e as Error).message })
    }
  }

  const testDb = async () => {
    const v = await dbForm.validateFields()
    setDbTest(null)
    try {
      const r = await siteApi.test({ target: 'mysql', mysql: v })
      setDbTest({ ok: !!r.ok, detail: r.detail, ms: r.ms })
    } catch (e) {
      setDbTest({ ok: false, detail: (e as Error).message })
    }
  }

  const testModel = async () => {
    const v = await modelForm.validateFields()
    setModelTest(null)
    try {
      const r = await siteApi.test({ target: 'model', model: v })
      setModelTest({ ok: !!r.ok, detail: r.detail, ms: r.ms })
    } catch (e) {
      setModelTest({ ok: false, detail: (e as Error).message })
    }
  }

  const save = async () => {
    const pg = await pgForm.validateFields()
    const db = await dbForm.getFieldsValue()
    const model = await modelForm.getFieldsValue()
    setSaving(true)
    try {
      const r = await siteApi.save({
        pg: {
          host: pg.host,
          port: pg.port,
          dbname: pg.dbname,
          user: pg.user,
          password: pg.password,
        },
        admin: { username: pg.admin_username, password: pg.admin_password },
        mysql: db,
        model,
      })
      if (r.ok) {
        rememberSite(pg, pg.admin_username)
        setSites(loadSites())
        message.success('已保存并重连，请用初始管理员登录')
        await refresh()
      } else {
        message.error(r.error || '保存失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const down = !status?.ok

  return (
    <div style={{ maxWidth: 980, margin: '40px auto', padding: '0 16px' }}>
      <Space direction="vertical" size={2} style={{ width: '100%', marginBottom: 16 }}>
        <Title level={4} style={{ marginBottom: 0 }}>
          <CloudServerOutlined /> 现场配置
        </Title>
        <Text type="secondary">
          一个现场 = 一个 PostgreSQL 库。填完三条连接保存后，本实例立即连到该库。
        </Text>
        {down && (
          <Alert
            style={{ marginTop: 10 }}
            type="warning"
            showIcon
            message="该实例当前不可用：还没有配置现场，或产品库连不上。"
            description={<Text style={{ fontSize: 12 }}>{status?.error || '（后端未返回原因）'}</Text>}
          />
        )}
        {!down && (
          <Alert
            style={{ marginTop: 10 }}
            type="info"
            showIcon
            message={
              <span>
                当前现场：<Tag>{status?.site_id}</Tag>
                {status?.business_empty ? <Tag color="orange">业务内容为空</Tag> : null}
              </span>
            }
          />
        )}
      </Space>

      {sites.length > 0 && (
        <Card size="small" style={{ marginBottom: 12 }} title="用过的现场（点一下直接切）">
          <Space wrap>
            {sites.map((s0) => (
              <Button
                key={s0.label}
                disabled={s0.label === status?.site?.dbname}
                loading={switching === s0.label}
                onClick={() => quickSwitch(s0)}
              >
                {s0.label}
                {s0.label === status?.site?.dbname ? '（当前）' : ''}
              </Button>
            ))}
            <Text type="secondary" style={{ fontSize: 12 }}>
              只存产品库连接与管理员账号；口令每次不用输，账号已存在就跳过。
            </Text>
          </Space>
        </Card>
      )}

      <Card>
        <Tabs
          items={[
            {
              key: 'pg',
              label: (
                <span>
                  <DatabaseOutlined /> 产品库 + 初始管理员
                </span>
              ),
              children: (
                <Form form={pgForm} layout="vertical" initialValues={{ host: '127.0.0.1', port: 5432 }}>
                  <Row gutter={12}>
                    <Col span={14}>
                      <Form.Item name="host" label="主机" rules={[{ required: true }]}>
                        <Input placeholder="127.0.0.1" />
                      </Form.Item>
                    </Col>
                    <Col span={10}>
                      <Form.Item name="port" label="端口" rules={[{ required: true }]}>
                        <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="dbname" label="库名" rules={[{ required: true }]}>
                        <Input placeholder="现场库名" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="user" label="用户" rules={[{ required: true }]}>
                        <Input placeholder="库账号" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="password" label="口令">
                        <Input.Password placeholder="库口令" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item
                        name="admin_username"
                        label="初始管理员账号"
                        rules={[{ required: true, message: '至少给一个管理员账号' }]}
                      >
                        <Input placeholder="例如 admin" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item
                        name="admin_password"
                        label="初始管理员口令"
                        rules={[{ required: true, message: '至少给一个管理员口令' }]}
                      >
                        <Input.Password placeholder="仅在该账号不存在时创建" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Space>
                    <Button icon={<ApiOutlined />} onClick={testPg}>
                      测试连接
                    </Button>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      管理员已存在且已有口令时只跳过，不会改掉现有口令。
                    </Text>
                  </Space>
                  <TestLine result={pgTest} />
                </Form>
              ),
            },
            {
              key: 'mysql',
              label: (
                <span>
                  <DatabaseOutlined /> 业务库（MySQL）
                </span>
              ),
              children: (
                <Form form={dbForm} layout="vertical" initialValues={{ db_port: 3306 }}>
                  <Row gutter={12}>
                    <Col span={14}>
                      <Form.Item name="db_host" label="主机">
                        <Input placeholder="业务库地址" />
                      </Form.Item>
                    </Col>
                    <Col span={10}>
                      <Form.Item name="db_port" label="端口">
                        <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="db_name" label="库名">
                        <Input placeholder="业务库名" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="db_user" label="账号">
                        <Input placeholder="只读账号即可" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="db_password" label="口令">
                        <Input.Password />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="alert_db_name" label="预警业务库（留空＝同上）">
                        <Input placeholder="例：dlj_data" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Space>
                    <Button icon={<ApiOutlined />} onClick={testDb}>
                      测试连接
                    </Button>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      问数读上面的库；预警工作流读「预警业务库」，留空就与问数同一个库。
                    </Text>
                  </Space>
                  <div style={{ marginTop: 6 }}>
                    <Text type={dbSource === 'page' ? 'success' : 'warning'} style={{ fontSize: 12 }}>
                      当前生效：{dbSource === 'page' ? '上面这份页面配置' : '环境变量默认值（还没在这里保存过）'}
                    </Text>
                  </div>
                  <TestLine result={dbTest} />
                </Form>
              ),
            },
            {
              key: 'sys',
              label: (
                <span>
                  <SettingOutlined /> 系统与外观
                </span>
              ),
              children: <Settings />,
            },
            {
              key: 'model',
              label: (
                <span>
                  <RobotOutlined /> 模型通道
                </span>
              ),
              children: (
                <Form form={modelForm} layout="vertical" initialValues={{ model_channel: 'online' }}>
                  <Row gutter={12}>
                    <Col span={8}>
                      <Form.Item name="model_channel" label="通道">
                        <Select
                          options={[
                            { value: 'online', label: 'online（公网网关）' },
                            { value: 'local', label: 'local（内网自建）' },
                          ]}
                        />
                      </Form.Item>
                    </Col>
                    <Col span={16}>
                      <Form.Item name="llm_base_url" label="网关地址">
                        <Input placeholder="https://…/compatible-mode/v1" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="llm_api_key" label="API Key">
                        <Input.Password placeholder="留空 = 不变" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="llm_model" label="模型名">
                        <Input placeholder="例如 deepseek-chat" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Space>
                    <Button icon={<ApiOutlined />} onClick={testModel}>
                      测试连接
                    </Button>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      先试网关的 /models，网关不支持就发一条 1 token 的对话。
                    </Text>
                  </Space>
                  <TestLine result={modelTest} />
                </Form>
              ),
            },
          ]}
        />

        <Space style={{ marginTop: 16 }}>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>
            保存并重连
          </Button>
          <Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
            保存时先试连产品库；连不上不会写配置、也不会切走当前现场。
          </Paragraph>
        </Space>
      </Card>
    </div>
  )
}
