import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Drawer,
  Form,
  Input,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { ApiOutlined, CloudOutlined, ExperimentOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { injectApi, kbApi, modelApi } from '../api'
import type { KnowledgeBase, ProviderConfig, ProviderTestResult, RegistryNode } from '../api'
import StateBadge from '../components/StateBadge'
import PermissionManagement from './PermissionManagement'

const { Title, Text, Paragraph, Link: AntLink } = Typography

const MODE_META: Record<string, { label: string; color: string; icon: any; desc: string }> = {
  mock: {
    label: 'Mock 档位',
    color: 'orange',
    icon: <ExperimentOutlined />,
    desc: '零依赖的确定性伪向量，用于功能开发与链路验证。检索结果不具备真实语义。',
  },
  local: {
    label: '本地模型档位',
    color: 'blue',
    icon: <ApiOutlined />,
    desc: '公司内网部署的 bge-m3 / bge-rerank-m3 / DeepSeek / 千问3.6（OpenAI 兼容端点）。',
  },
  online: {
    label: '线上 API 档位',
    color: 'purple',
    icon: <CloudOutlined />,
    desc: '线上大模型 API（阿里云百炼 compatible-mode）：text-embedding-v4 / gte-rerank-v2 / qwen3.8-max。会产生真实调用费用。',
  },
}

export default function ModelConfig() {
  const [cfg, setCfg] = useState<ProviderConfig | null>(null)
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)
  const [results, setResults] = useState<ProviderTestResult[]>([])
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [form] = Form.useForm()

  const load = async () => {
    setLoading(true)
    try {
      const [c, k] = await Promise.allSettled([modelApi.config(), kbApi.list(1, 100)])
      if (c.status === 'fulfilled') {
        setCfg(c.value)
        // 按当前通道回填对应表单（local / online 两套字段互不干扰）
        const opts = (c.value.options ?? {}) as Record<string, Record<string, unknown>>
        const fill = c.value.mode === 'online' ? opts.online : opts.local
        if (fill) form.setFieldsValue(fill)
      }
      if (k.status === 'fulfilled') setKbs(k.value.items)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const switchMode = async (mode: string) => {
    try {
      const res = await modelApi.switchMode(mode)
      setCfg(res)
      message.success(`已切换到 ${MODE_META[mode]?.label ?? mode}`)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const runTest = async () => {
    const values = form.getFieldsValue()
    const mode = cfg?.mode ?? 'local'
    setTesting(true)
    setResults([])
    try {
      // 按当前通道把对应字段发给后端做临时覆盖测试
      const prefix = mode === 'online' ? 'online' : 'local'
      const payload: Record<string, unknown> = { target: 'all', mode }
      for (const [k, v] of Object.entries(values)) {
        if (typeof k === 'string' && k.startsWith(prefix + '_') && v) {
          payload[k] = v
        }
      }
      const res = await modelApi.test(payload)
      setResults(res.results)
      if (res.all_ok) message.success('全部连接正常')
      else message.warning('部分连接失败，请查看下方明细')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setTesting(false)
    }
  }

  const current = cfg ? MODE_META[cfg.mode] : null

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Title level={4} style={{ margin: 0 }}>
          模型配置
        </Title>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
          刷新
        </Button>
      </Space>

      {cfg && (
        <Alert
          type={cfg.mode === 'mock' ? 'warning' : 'success'}
          showIcon
          icon={current?.icon}
          message={`当前档位：${cfg.mode_label}`}
          description={
            <Space direction="vertical" size={2}>
              <Text>{current?.desc}</Text>
              {cfg.notice && <Text type="warning">{cfg.notice}</Text>}
            </Space>
          }
        />
      )}

      <Row gutter={16}>
        {cfg &&
          (['mock', 'local', 'online'] as const).map((mode) => {
            const meta = MODE_META[mode]
            const active = cfg.mode === mode
            return (
              <Col span={8} key={mode}>
                <Card
                  size="small"
                  title={
                    <Space>
                      {meta.icon}
                      <span>{meta.label}</span>
                      {active && <Tag color="green">当前</Tag>}
                    </Space>
                  }
                  extra={
                    !active && (
                      <Button size="small" onClick={() => switchMode(mode)}>
                        切换
                      </Button>
                    )
                  }
                >
                  <Paragraph style={{ fontSize: 12, marginBottom: 8 }}>{meta.desc}</Paragraph>
                  {mode === 'mock' && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      数据库连接正常即可使用，无需任何外部依赖。
                    </Text>
                  )}
                  {mode === 'local' && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      对接公司内网地址，需在 .env 中配置 LOCAL_* 各项。
                    </Text>
                  )}
                  {mode === 'online' && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      对接线上 API，需在 .env 中配置 ONLINE_* 各项（Base URL / API Key / 模型名）。
                    </Text>
                  )}
                </Card>
              </Col>
            )
          })}
      </Row>

      {cfg && (
        <Card size="small" title="当前生效的模型">
          <Row gutter={16}>
            <Col span={8}>
              <Text type="secondary">Embedding</Text>
              <div>
                <Text className="kb-mono">{cfg.embedding.detail || cfg.embedding.model_id}</Text>
              </div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {cfg.embedding_dim} 维
              </Text>
            </Col>
            <Col span={8}>
              <Text type="secondary">Rerank</Text>
              <div>
                <Text className="kb-mono">{cfg.rerank.detail || cfg.rerank.model_id}</Text>
              </div>
            </Col>
            <Col span={8}>
              <Text type="secondary">LLM</Text>
              <div>
                <Text className="kb-mono">{cfg.llm.detail || cfg.llm.model_id}</Text>
              </div>
            </Col>
          </Row>
        </Card>
      )}

      <Card
        title={cfg?.mode === 'online' ? '线上 API 连接测试' : '本地模型连接测试'}
        size="small"
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            凭据仅用于本次测试，不会写入配置文件
          </Text>
        }
      >
        {cfg?.mode === 'local' && (
        <Form form={form} layout="vertical">
          <Divider orientation="left" plain>
            Embedding（bge-m3，必填）
          </Divider>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="local_embedding_url" label="Embedding 地址">
                <Input placeholder="http://10.228.224.16:18001/v1/embeddings" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="local_embedding_model" label="Embedding 模型">
                <Input placeholder="bge-m3" />
              </Form.Item>
            </Col>
          </Row>

          <Divider orientation="left" plain>
            Rerank（bge-rerank-m3，可选）
          </Divider>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="local_rerank_url" label="Rerank 地址">
                <Input placeholder="http://10.228.224.16:18001/v1/rerank" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="local_rerank_model" label="Rerank 模型">
                <Input placeholder="bge-rerank-m3" />
              </Form.Item>
            </Col>
          </Row>

          <Divider orientation="left" plain>
            LLM（冲突检测用，可选）
          </Divider>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="local_llm_base_url" label="Base URL">
                <Input placeholder="http://10.228.224.16:18001/v1" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="local_llm_model" label="模型">
                <Input placeholder="deepseek-chat" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="local_llm_api_key" label="API Key（可选）">
                <Input.Password placeholder="不鉴权则留空" allowClear />
              </Form.Item>
            </Col>
          </Row>

          <Space>
            <Button type="primary" onClick={runTest} loading={testing}>
              测试全部连接
            </Button>
            <Text type="secondary" style={{ fontSize: 12 }}>
              测试会真实调用内网模型服务（纯内网，无外网费用）
            </Text>
          </Space>
        </Form>
        )}

        {cfg?.mode === 'online' && (
          <Form form={form} layout="vertical">
            <Divider orientation="left" plain>
              Embedding（text-embedding-v4）
            </Divider>
            <Row gutter={12}>
              <Col span={12}>
                <Form.Item name="online_embedding_base_url" label="Embedding Base URL">
                  <Input placeholder="https://llm-xxxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="online_embedding_model" label="Embedding 模型">
                  <Input placeholder="text-embedding-v4" />
                </Form.Item>
              </Col>
            </Row>

            <Divider orientation="left" plain>
              Rerank（gte-rerank-v2）
            </Divider>
            <Row gutter={12}>
              <Col span={12}>
                <Form.Item name="online_rerank_base_url" label="Rerank Base URL">
                  <Input placeholder="https://llm-xxxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="online_rerank_model" label="Rerank 模型">
                  <Input placeholder="gte-rerank-v2" />
                </Form.Item>
              </Col>
            </Row>

            <Divider orientation="left" plain>
              LLM（冲突检测 / 问答用）
            </Divider>
            <Row gutter={12}>
              <Col span={8}>
                <Form.Item name="online_llm_base_url" label="LLM Base URL">
                  <Input placeholder="https://llm-xxxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="online_llm_model" label="模型">
                  <Input placeholder="qwen3.8-max" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="online_llm_api_key" label="API Key">
                  <Input.Password placeholder="sk-..." allowClear />
                </Form.Item>
              </Col>
            </Row>

            <Space>
              <Button type="primary" onClick={runTest} loading={testing}>
                测试全部连接
              </Button>
              <Text type="secondary" style={{ fontSize: 12 }}>
                测试会真实调用线上 API（<Text strong>会产生费用</Text>，请谨慎点击）
              </Text>
            </Space>
          </Form>
        )}

        {cfg?.mode === 'mock' && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            Mock 档位无需任何连接配置：使用本地确定性伪向量，数据库正常即可运行。
          </Text>
        )}

        {results.length > 0 && (
          <Table
            style={{ marginTop: 12 }}
            size="small"
            rowKey="target"
            pagination={false}
            dataSource={results}
            columns={[
              {
                title: '目标',
                dataIndex: 'target',
                render: (t: string) =>
                  ({ embedding: 'Embedding', rerank: 'Rerank', llm: 'LLM' })[t] ?? t,
              },
              {
                title: '模型',
                dataIndex: 'model',
                render: (m: string, r) => (
                  <Text className="kb-mono" style={{ fontSize: 11 }}>
                    {r.provider}/{m}
                  </Text>
                ),
              },
              {
                title: '结果',
                dataIndex: 'ok',
                render: (ok: boolean, r) => (
                  <Space direction="vertical" size={0}>
                    <Tag color={ok ? 'success' : 'error'}>{ok ? '连接成功' : '连接失败'}</Tag>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {r.message}
                    </Text>
                  </Space>
                ),
              },
              {
                title: '耗时',
                dataIndex: 'latency_ms',
                width: 90,
                render: (t: number) => `${t} ms`,
              },
            ]}
          />
        )}
      </Card>

      {kbs.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="⚠️ 切换向量模型前必读"
          description={
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Text style={{ fontSize: 12 }}>
                不同模型生成的向量<b>即使维度相同也不可比较</b>。
                用 mock 档建的索引，切到内网 bge-m3 后必须<b>全量重建</b>，
                否则检索结果会静默出错（不报错，就是查不到）。
              </Text>
              <Table
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={kbs}
                columns={[
                  { title: '知识库', dataIndex: 'name' },
                  {
                    title: '绑定模型',
                    dataIndex: 'embedding_model',
                    render: (m: string) => (
                      <Text className="kb-mono" style={{ fontSize: 11 }}>
                        {m}
                      </Text>
                    ),
                  },
                  {
                    title: '切片数',
                    dataIndex: 'chunk_count',
                    width: 100,
                    render: (n: number) => (n > 0 ? <Tag color="orange">{n} 条待重建</Tag> : '—'),
                  },
                ]}
              />
            </Space>
          }
        />
      )}
    </Space>
  )
}

function RegistryPanel() {
  const [items, setItems] = useState<RegistryNode[]>([])
  const [summary, setSummary] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(false)
  const [stateFilter, setStateFilter] = useState<string | undefined>()
  const [node, setNode] = useState<RegistryNode | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const r = await injectApi.nodes()
      setItems(r.items ?? [])
      setSummary(r.summary ?? {})
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const filtered = stateFilter ? items.filter((x) => x.state === stateFilter) : items

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="三态注册表：每一份配置都要看见、说得清来源"
        description="可改 = 页面上能改；只读·库 = 存在库里但本期由别的系统维护；只读·代码 = 代码常量。点任意一行看存储与接口。"
      />
      <Row gutter={12}>
        {['可改', '只读·库', '只读·代码'].map((k, i) => (
          <Col span={6} key={k}>
            <Card size="small">
              <Statistic
                title={k}
                value={summary[k] ?? 0}
                valueStyle={{ color: ['#389e0d', '#1677ff', '#8c8c8c'][i] }}
              />
            </Card>
          </Col>
        ))}
        <Col span={6}>
          <Card size="small">
            <Statistic title="合计" value={summary.total ?? items.length} />
          </Card>
        </Col>
      </Row>
      <Card
        size="small"
        title="注册表"
        extra={
          <Space>
            <Select
              allowClear
              size="small"
              placeholder="全部三态"
              style={{ width: 130 }}
              value={stateFilter}
              onChange={setStateFilter}
              options={['可改', '只读·库', '只读·代码'].map((x) => ({ value: x, label: x }))}
            />
            <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
              刷新
            </Button>
          </Space>
        }
      >
        <Table
          rowKey={(r) => `${r.group}-${r.key}`}
          size="small"
          loading={loading}
          dataSource={filtered}
          pagination={{ pageSize: 12, showSizeChanger: false }}
          onRow={(r) => ({ onClick: () => setNode(r), style: { cursor: 'pointer' } })}
          columns={[
            { title: '分组', dataIndex: 'group', width: 92, render: (v) => <Tag>{v}</Tag> },
            { title: '配置项', dataIndex: 'label', width: 200 },
            {
              title: '三态',
              dataIndex: 'state',
              width: 100,
              render: (v, r) => (
                <StateBadge
                  state={v}
                  source={r.storage}
                  updatedBy={r.updated_by}
                  updatedAt={r.updated_at}
                  extra={<div>生效：下一轮（≤60 秒）</div>}
                />
              ),
            },
            {
              title: '字数',
              width: 68,
              align: 'right',
              render: (_, r) => (r.chars != null ? r.chars.toLocaleString() : '—'),
            },
            {
              title: '来源',
              dataIndex: 'storage',
              width: 200,
              render: (v) => (
                <Text className="kb-mono" style={{ fontSize: 11 }}>
                  {v}
                </Text>
              ),
            },
            {
              title: '最近修改',
              width: 130,
              render: (_, r) =>
                r.updated_by || r.updated_at ? (
                  <Space direction="vertical" size={0}>
                    <Text style={{ fontSize: 11 }}>{r.updated_by || '—'}</Text>
                    <Text type="secondary" style={{ fontSize: 10 }}>
                      {r.updated_at || ''}
                    </Text>
                  </Space>
                ) : (
                  <Text type="secondary">—</Text>
                ),
            },
            {
              title: '生效方式',
              width: 138,
              render: () => <Tag color="blue">下一轮 · ≤60 秒</Tag>,
            },
            { title: '页面', dataIndex: 'page', width: 112 },
          ]}
        />
      </Card>
      <Drawer open={!!node} width={640} title={node ? `${node.group} / ${node.label}` : ''} onClose={() => setNode(null)}>
        {node ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="键">
                <Text code>{node.key}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="三态">
                <StateBadge state={node.state} source={node.storage} updatedBy={node.updated_by} />
              </Descriptions.Item>
              <Descriptions.Item label="来源">{node.storage}</Descriptions.Item>
              <Descriptions.Item label="页面">{node.page}</Descriptions.Item>
              <Descriptions.Item label="接口">{node.api || '—（代码常量）'}</Descriptions.Item>
              <Descriptions.Item label="影响">{node.affects}</Descriptions.Item>
              <Descriptions.Item label="生效">下一轮（缓存最多 60 秒）</Descriptions.Item>
            </Descriptions>
            {node.values ? (
              <Card size="small" title="当前值">
                <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>
                  {JSON.stringify(node.values, null, 2)}
                </pre>
              </Card>
            ) : null}
          </Space>
        ) : null}
      </Drawer>
    </Space>
  )
}

const RULE_LABEL: Record<string, string> = {
  product: '产物关键词（一句话想做哪种文件）',
  make_verb: '动手词（要生成/写/做）',
  deliver_tail: '交付尾词（发我/给我/导出）',
  ask_hint: '问数提示词（多少/排名/占比）',
  file_ref: '文件指代（这个文件/附件/文中）',
  priority: '选取优先级（谁说了算）',
}

const RULE_HINT: Record<string, string> = {
  product: '每个产物一组词；命中任一，就按那种文件出。',
  make_verb: '出现这些词，说明用户想动手做事，不是只想聊天。',
  deliver_tail: '出现这些词，说明用户要交付物。',
  ask_hint: '出现这些词，优先走问数。',
  file_ref: '这些说法指代会话里已有的文件，要读文件。',
  priority: '说明书选取发生冲突时，按这个顺序判。',
}

function RouteRulePanel() {
  const [meta, setMeta] = useState<any>(null)
  const [values, setValues] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r: any = await injectApi.routeRule()
      setMeta(r)
      setValues(JSON.parse(JSON.stringify(r.values ?? {})))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      const r: any = await injectApi.saveRouteRule(values)
      if (r?.error) message.error(r.error)
      else {
        message.success(`已保存整份入口判据（${r.terms} 个词），下一轮生效`)
        await load()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const setList = (key: string, list: string[]) =>
    setValues((prev) => ({ ...prev, [key]: list }))

  const setProduct = (kind: string, list: string[]) =>
    setValues((prev) => ({
      ...prev,
      product: { ...(prev.product ?? {}), [kind]: list },
    }))

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Card size="small" title="入口分流判据">
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap>
            {meta ? (
              <StateBadge
                state={meta.state}
                source={meta.source}
                updatedBy={meta.updated_by}
                updatedAt={meta.updated_at}
              />
            ) : null}
            <Tag>v{meta?.version_no ?? 1}</Tag>
            <Tag>{meta?.chars ?? 0} 字</Tag>
            <Text type="secondary">保存整份 values，改完下一轮生效（≤60 秒）</Text>
          </Space>
          <Alert
            type="info"
            showIcon
            message="这些词决定一句话走干活线还是问数线"
            description="改词不改代码：每个 Tag 按回车追加，点 × 删除。保存时整份 values 提交，后端只认这些键。"
          />
          <Space>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
              保存整份
            </Button>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
              重新读取
            </Button>
          </Space>
        </Space>
      </Card>

      {Object.keys(values).map((key) =>
        key === 'product' ? (
          <Card key={key} size="small" title={RULE_LABEL[key] || key}>
            <Paragraph type="secondary" style={{ fontSize: 12 }}>
              {RULE_HINT[key]}
            </Paragraph>
            <Row gutter={12}>
              {Object.entries(values.product ?? {}).map(([kind, list]) => (
                <Col span={8} key={kind}>
                  <Card size="small" type="inner" title={kind}>
                    <Select
                      mode="tags"
                      style={{ width: '100%' }}
                      value={list as string[]}
                      tokenSeparators={[',', '，', '、']}
                      onChange={(v: string[]) => setProduct(kind, v)}
                    />
                  </Card>
                </Col>
              ))}
            </Row>
          </Card>
        ) : (
          <Card key={key} size="small" title={RULE_LABEL[key] || key}>
            <Paragraph type="secondary" style={{ fontSize: 12 }}>
              {RULE_HINT[key]}
            </Paragraph>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={Array.isArray(values[key]) ? values[key] : []}
              tokenSeparators={[',', '，', '、']}
              onChange={(v: string[]) => setList(key, v)}
            />
          </Card>
        )
      )}
    </Space>
  )
}

export function SystemPage() {
  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>
          系统
        </Title>
        <Text type="secondary">
          用户与角色、模型、三态注册表、入口分流判据；可改项都落在接口上。
        </Text>
      </div>
      <Tabs
        defaultActiveKey="registry"
        items={[
          { key: 'registry', label: '三态注册表', children: <RegistryPanel /> },
          { key: 'route', label: '入口分流判据', children: <RouteRulePanel /> },
          { key: 'users', label: '用户与角色', children: <PermissionManagement /> },
          { key: 'models', label: '模型', children: <ModelConfig /> },
        ]}
      />
    </Space>
  )
}
