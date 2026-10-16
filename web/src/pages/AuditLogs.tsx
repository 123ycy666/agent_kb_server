import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Input,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { auditApi } from '../api'
import type { AuditLog } from '../api'

const { Title, Text, Paragraph } = Typography

const ACTOR_COLOR: Record<string, string> = {
  user: 'blue',
  app: 'purple',
  system: 'default',
}

export default function AuditLogs() {
  const [data, setData] = useState<AuditLog[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [stats, setStats] = useState<any>(null)
  const [actions, setActions] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<AuditLog | null>(null)

  const [filters, setFilters] = useState<{
    action?: string
    actor_type?: string
    keyword?: string
    range?: [dayjs.Dayjs, dayjs.Dayjs] | null
  }>({})

  const load = async (p = page) => {
    setLoading(true)
    try {
      const [list, st, ac] = await Promise.allSettled([
        auditApi.list({
          page: p,
          page_size: 20,
          action: filters.action,
          actor_type: filters.actor_type,
          keyword: filters.keyword,
          start: filters.range?.[0]?.toISOString(),
          end: filters.range?.[1]?.toISOString(),
        }),
        auditApi.stats(),
        auditApi.actions(),
      ])
      if (list.status === 'fulfilled') {
        setData(list.value.items)
        setTotal(list.value.total)
      }
      if (st.status === 'fulfilled') setStats(st.value)
      if (ac.status === 'fulfilled') setActions(ac.value)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(1)
    setPage(1)
  }, [filters])

  const columns: ColumnsType<AuditLog> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 160,
      render: (t: string) => (
        <Text style={{ fontSize: 12 }}>{dayjs(t).format('YYYY-MM-DD HH:mm:ss')}</Text>
      ),
    },
    {
      title: '主体',
      width: 170,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Space size={4}>
            <Tag color={ACTOR_COLOR[row.actor_type]}>{row.actor_type}</Tag>
            <Text style={{ fontSize: 12 }}>
              {row.actor_name ?? row.actor_app_id ?? '—'}
            </Text>
          </Space>
          {row.ip && (
            <Text type="secondary" className="kb-mono" style={{ fontSize: 10 }}>
              {row.ip}
            </Text>
          )}
        </Space>
      ),
    },
    {
      title: '操作',
      dataIndex: 'action',
      width: 170,
      render: (a: string) => {
        const meta = actions.find((x) => x.action === a)
        return (
          <Space direction="vertical" size={0}>
            <Text style={{ fontSize: 12 }}>{meta?.label ?? a}</Text>
            <Text type="secondary" className="kb-mono" style={{ fontSize: 10 }}>
              {a}
            </Text>
          </Space>
        )
      },
    },
    {
      title: '资源',
      render: (_, row) =>
        row.resource_name ? (
          <Space direction="vertical" size={0}>
            <Text style={{ fontSize: 12 }}>{row.resource_name}</Text>
            <Text type="secondary" className="kb-mono" style={{ fontSize: 10 }}>
              {row.resource_type}
              {row.resource_id ? `#${row.resource_id}` : ''}
            </Text>
          </Space>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: '命中',
      width: 90,
      render: (_, row) => {
        const hits = (row.detail as any)?.hit_doc_ids
        if (!Array.isArray(hits)) return <Text type="secondary">—</Text>
        return hits.length ? (
          <Tag color="processing">{hits.length} 份文档</Tag>
        ) : (
          <Tag>0</Tag>
        )
      },
    },
    {
      title: '结果',
      dataIndex: 'result',
      width: 80,
      render: (r: string) => (
        <Tag color={r === 'success' ? 'success' : 'error'}>{r === 'success' ? '成功' : '失败'}</Tag>
      ),
    },
    {
      title: '',
      width: 60,
      render: (_, row) => (
        <Button size="small" type="link" onClick={() => setDetail(row)}>
          详情
        </Button>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Title level={4} style={{ margin: 0 }}>
          审计日志
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>
            刷新
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="检索类操作会记录命中的文档 ID"
        description={
          <Paragraph style={{ marginBottom: 0, fontSize: 12 }}>
            这是企业级审计的核心留痕点：当机密文档外泄时，可以追溯「谁在什么时候、
            通过哪次检索看到了它」。仅记录查询词是不够的。
          </Paragraph>
        }
      />

      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title="累计操作" value={stats?.total ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="今日操作" value={stats?.today ?? 0} />
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="按主体分布">
            <Space wrap>
              {Object.entries(stats?.by_actor_type ?? {}).map(([k, v]) => (
                <Tag key={k} color={ACTOR_COLOR[k]}>
                  {k}: {String(v)}
                </Tag>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>

      <Card size="small">
        <Space wrap>
          <Select
            allowClear
            placeholder="操作类型"
            style={{ width: 200 }}
            showSearch
            optionFilterProp="label"
            value={filters.action}
            onChange={(v) => setFilters((s) => ({ ...s, action: v }))}
            options={actions.map((a) => ({ label: a.label, value: a.action }))}
          />
          <Select
            allowClear
            placeholder="主体类型"
            style={{ width: 140 }}
            value={filters.actor_type}
            onChange={(v) => setFilters((s) => ({ ...s, actor_type: v }))}
            options={[
              { label: '用户', value: 'user' },
              { label: '应用', value: 'app' },
              { label: '系统', value: 'system' },
            ]}
          />
          <DatePicker.RangePicker
            showTime
            value={filters.range as any}
            onChange={(v) => setFilters((s) => ({ ...s, range: v as any }))}
          />
          <Input.Search
            placeholder="资源名 / 动作关键词"
            style={{ width: 220 }}
            allowClear
            onSearch={(v) => setFilters((s) => ({ ...s, keyword: v || undefined }))}
          />
        </Space>
      </Card>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={data}
        loading={loading}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          showSizeChanger: false,
          onChange: (p) => {
            setPage(p)
            load(p)
          },
        }}
        scroll={{ x: 1100 }}
      />

      <Drawer
        title="审计详情"
        open={!!detail}
        onClose={() => setDetail(null)}
        width={620}
      >
        {detail && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="时间">
                {dayjs(detail.created_at).format('YYYY-MM-DD HH:mm:ss')}
              </Descriptions.Item>
              <Descriptions.Item label="主体">
                {detail.actor_type} / {detail.actor_name ?? detail.actor_app_id ?? '—'}
                {detail.actor_app_id && ` (${detail.actor_app_id})`}
              </Descriptions.Item>
              <Descriptions.Item label="动作">{detail.action}</Descriptions.Item>
              <Descriptions.Item label="资源">
                {detail.resource_type}
                {detail.resource_id ? `#${detail.resource_id}` : ''} {detail.resource_name}
              </Descriptions.Item>
              <Descriptions.Item label="IP">{detail.ip ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="结果">{detail.result}</Descriptions.Item>
            </Descriptions>

            <div>
              <Text strong>明细</Text>
              <pre
                className="kb-chunk kb-mono"
                style={{ maxHeight: 420, overflow: 'auto', marginTop: 6 }}
              >
                {JSON.stringify(detail.detail, null, 2)}
              </pre>
            </div>
          </Space>
        )}
      </Drawer>
    </Space>
  )
}
