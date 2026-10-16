import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  Row,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ApiOutlined, CloudOutlined, ExperimentOutlined, ReloadOutlined } from '@ant-design/icons'
import { kbApi, modelApi } from '../api'
import type { KnowledgeBase, ProviderConfig, ProviderTestResult } from '../api'

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
