import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Input,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { injectApi } from '../api'
import type { InjectPreviewData, InjectionPart } from '../api'

const { Text } = Typography

export default function InjectPreview({
  question = '',
  specName,
  sessionId,
  auto = true,
  compact = false,
}: {
  question?: string
  specName?: string
  sessionId?: string
  auto?: boolean
  compact?: boolean
}) {
  const [q, setQ] = useState(question)
  const [data, setData] = useState<InjectPreviewData | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(
    async (next = q) => {
      setLoading(true)
      try {
        const res = await injectApi.preview({
          question: next.trim(),
          spec_name: specName,
          session_id: sessionId,
        })
        setData(res)
      } catch (e) {
        message.error((e as Error).message)
      } finally {
        setLoading(false)
      }
    },
    [q, specName, sessionId]
  )

  useEffect(() => {
    if (auto) void load(question)
    // 只在挂载时自动取一次，后续由「预览」按钮触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const columns: ColumnsType<InjectionPart> = [
    {
      title: '组成',
      dataIndex: 'label',
      width: 190,
      render: (label: string, row) => (
        <Space size={6}>
          <Text strong={row.injected}>{label}</Text>
          {!row.injected ? <Tag>未注入</Tag> : null}
        </Space>
      ),
    },
    {
      title: '字数',
      dataIndex: 'chars',
      width: 80,
      align: 'right',
      render: (n: number) => n.toLocaleString(),
    },
    {
      title: '占比',
      dataIndex: 'share',
      render: (share: number) => (
        <Space size={8} style={{ width: '100%' }}>
          <Progress
            percent={Math.round((share || 0) * 1000) / 10}
            showInfo={false}
            size="small"
            strokeColor={share > 0.5 ? '#fa8c16' : '#1677ff'}
            style={{ width: 130, margin: 0 }}
          />
          <Text style={{ fontSize: 12 }}>{((share || 0) * 100).toFixed(1)}%</Text>
        </Space>
      ),
    },
  ]

  return (
    <Card
      size="small"
      title="注入预览"
      extra={
        <Space>
          <Input
            size="small"
            value={q}
            placeholder="输入一个问题看本轮注入"
            prefix={<SearchOutlined />}
            style={{ width: 240 }}
            onChange={(e) => setQ(e.target.value)}
            onPressEnter={() => void load()}
          />
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            预览
          </Button>
        </Space>
      }
    >
      {!data ? (
        <Empty description="点「预览」看本轮注入构成" />
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Row gutter={12}>
            <Col span={8}>
              <Statistic title="本轮注入" value={data.total} suffix="字" />
            </Col>
            <Col span={8}>
              <Statistic title="常驻预算" value={data.budget} suffix="字" />
            </Col>
            <Col span={8}>
              <Statistic
                title="预算状态"
                value={data.over_budget ? '超限' : '未超限'}
                valueStyle={{ color: data.over_budget ? '#cf1322' : '#389e0d' }}
              />
            </Col>
          </Row>
          {data.over_budget ? (
            <Alert type="error" showIcon message="本轮注入超过常驻预算，已按历史→目录→摘要顺序压缩" />
          ) : (
            <Alert type="success" showIcon message="本轮注入在常驻预算内" />
          )}
          {data.spec ? <Text type="secondary">命中的说明书：《{data.spec}》</Text> : null}
          <Table
            rowKey="name"
            size="small"
            pagination={false}
            columns={columns}
            dataSource={data.parts}
          />
          <div>
            <Text strong>压了什么</Text>
            {data.compressed.length === 0 ? (
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  没有压缩，全部按原样注入。
                </Text>
              </div>
            ) : (
              <div style={{ marginTop: 6 }}>
                {data.compressed.map((x, i) => (
                  <div key={`${x.name}-${i}`} style={{ fontSize: 12, marginBottom: 4 }}>
                    <Tag color="orange">{x.name}</Tag>
                    {x.why}（{x.from} → {x.to} 字）
                  </div>
                ))}
              </div>
            )}
          </div>
          {!compact && data.head ? (
            <Collapse
              size="small"
              items={[
                {
                  key: 'head',
                  label: '看本轮注入开头（1200 字）',
                  children: (
                    <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>
                      {data.head}
                    </pre>
                  ),
                },
              ]}
            />
          ) : null}
        </Space>
      )}
    </Card>
  )
}
