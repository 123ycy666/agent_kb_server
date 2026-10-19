import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Row,
  Space,
  Statistic,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  SearchOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { auditApi, conflictApi, kbApi, systemApi } from '../api'
import type { KnowledgeBase } from '../api'

const { Title, Text, Paragraph } = Typography

export default function Overview() {
  const [health, setHealth] = useState<any>(null)
  const [auditStats, setAuditStats] = useState<any>(null)
  const [conflictStats, setConflictStats] = useState<any>(null)
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [h, a, c, k] = await Promise.allSettled([
        systemApi.health(),
        auditApi.stats(),
        conflictApi.stats(),
        kbApi.list(1, 100),
      ])
      if (h.status === 'fulfilled') setHealth(h.value)
      if (a.status === 'fulfilled') setAuditStats(a.value)
      if (c.status === 'fulfilled') setConflictStats(c.value)
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

  const dbOk = health?.database?.ok
  const docTotal = kbs.reduce((s, k) => s + k.document_count, 0)
  const chunkTotal = kbs.reduce((s, k) => s + k.chunk_count, 0)

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Title level={4} style={{ margin: 0 }}>
          系统概览
        </Title>
        <Button onClick={load} loading={loading}>
          刷新
        </Button>
      </Space>

      {health && !dbOk && (
        <Alert
          type="error"
          showIcon
          message="数据库连接异常"
          description={
            <div>
              <Paragraph style={{ marginBottom: 4 }}>
                {health.database?.detail}
              </Paragraph>
              <Text code>docker compose up -d</Text> 启动 PostgreSQL + pgvector 后重试。
            </div>
          }
        />
      )}

      {health && dbOk && (
        <Alert
          type="success"
          showIcon
          icon={<CheckCircleOutlined />}
          message="服务运行正常"
          description={<Text className="kb-mono">{health.database?.detail}</Text>}
        />
      )}

      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic
              title="知识库"
              value={kbs.length}
              prefix={<DatabaseOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="文档总数" value={docTotal} prefix={<FileTextOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="切片总数" value={chunkTotal} prefix={<SearchOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="待处理冲突"
              value={conflictStats?.pending ?? 0}
              prefix={<WarningOutlined />}
              valueStyle={{ color: (conflictStats?.pending ?? 0) > 0 ? '#cf1322' : undefined }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Card title="运行环境" size="small">
            {health ? (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="应用">{health.app}</Descriptions.Item>
                <Descriptions.Item label="环境">
                  <Tag color={health.env === 'production' ? 'red' : 'blue'}>
                    {health.env}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="数据库">
                  {dbOk ? (
                    <Tag icon={<CheckCircleOutlined />} color="success">
                      已连接
                    </Tag>
                  ) : (
                    <Tag icon={<CloseCircleOutlined />} color="error">
                      未连接
                    </Tag>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="模型档位">
                  <Tag color={health.provider?.mode === 'mock' ? 'orange' : 'green'}>
                    {health.provider?.mode}
                  </Tag>
                  {health.provider?.mode === 'mock' && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      伪向量，仅验证链路
                    </Text>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="Embedding">
                  <Text className="kb-mono">{health.provider?.embedding}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="Rerank">
                  <Text className="kb-mono">{health.provider?.rerank}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="LLM">
                  <Text className="kb-mono">{health.provider?.llm}</Text>
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Text type="secondary">加载中…</Text>
            )}
          </Card>
        </Col>

        <Col span={12}>
          <Card title="操作审计" size="small">
            {auditStats ? (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="累计操作">{auditStats.total}</Descriptions.Item>
                <Descriptions.Item label="今日操作">{auditStats.today}</Descriptions.Item>
                <Descriptions.Item label="按主体">
                  {Object.entries(auditStats.by_actor_type ?? {}).map(([k, v]) => (
                    <Tag key={k}>
                      {k}: {String(v)}
                    </Tag>
                  ))}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Text type="secondary">无权限查看或加载中…</Text>
            )}
            <Paragraph style={{ marginTop: 12, marginBottom: 0 }} className="kb-hint">
              检索类操作会记录命中的文档 ID，用于事后追溯数据流向。
            </Paragraph>
          </Card>
        </Col>
      </Row>

      <Card title="快速开始" size="small">
        <Space wrap>
          <Link to="/kb">
            <Button type="primary">创建知识库</Button>
          </Link>
          <Link to="/tags">
            <Button>定义标签维度</Button>
          </Link>
          <Link to="/kb">
            <Button>上传文档</Button>
          </Link>
          <Link to="/search">
            <Button>检索测试</Button>
          </Link>
          <Link to="/models">
            <Button>配置模型</Button>
          </Link>
        </Space>
      </Card>
    </Space>
  )
}
