import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { taskApi } from '../api'
import type { TaskItem } from '../api'

const { Title, Text, Paragraph } = Typography

const STATUS_META: Record<string, { text: string; color: string }> = {
  pending: { text: '排队中', color: 'default' },
  running: { text: '执行中', color: 'processing' },
  done: { text: '已完成', color: 'success' },
  failed: { text: '失败', color: 'error' },
  conflict_pending: { text: '冲突待处理', color: 'warning' },
}

const TYPE_LABEL: Record<string, string> = {
  doc_ingest: '文档入库',
  conflict_scan: '冲突扫描',
  reindex: '重建索引',
  kb_delete: '删除知识库',
}

export default function TaskCenter() {
  const [data, setData] = useState<TaskItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string | undefined>()
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<TaskItem | null>(null)

  const load = async (p = page) => {
    setLoading(true)
    try {
      const res = await taskApi.list({ page: p, page_size: 20, status })
      setData(res.items)
      setTotal(res.total)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(1)
    setPage(1)
  }, [status])

  const columns: ColumnsType<TaskItem> = [
    {
      title: '任务 ID',
      dataIndex: 'id',
      width: 150,
      render: (id: string, row) => (
        <Space direction="vertical" size={0}>
          <Text className="kb-mono" style={{ fontSize: 11 }}>
            {id.slice(0, 8)}…
          </Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {TYPE_LABEL[row.task_type] ?? row.task_type}
          </Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 130,
      render: (s: string) => {
        const m = STATUS_META[s] ?? { text: s, color: 'default' }
        return <Tag color={m.color}>{m.text}</Tag>
      },
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 160,
      render: (p: number, row) => (
        <Progress
          percent={p}
          size="small"
          status={row.status === 'failed' ? 'exception' : undefined}
        />
      ),
    },
    {
      title: '结果摘要',
      render: (_, row) => {
        if (row.error_msg) {
          return (
            <Text type="danger" style={{ fontSize: 12 }}>
              {row.error_msg.slice(0, 80)}
            </Text>
          )
        }
        if (row.result) {
          const r = row.result as any
          return (
            <Space size={4} wrap>
              {r.doc_id && <Tag>文档 #{r.doc_id}</Tag>}
              {r.chunk_count != null && <Tag>切片 {r.chunk_count}</Tag>}
              {r.conflicts?.length ? (
                <Tag color="error">冲突 {r.conflicts.length}</Tag>
              ) : null}
            </Space>
          )
        }
        return <Text type="secondary">—</Text>
      },
    },
    {
      title: '来源',
      dataIndex: 'created_by_app',
      width: 120,
      render: (a?: string) => (a ? <Tag color="purple">{a}</Tag> : <Tag color="blue">Web</Tag>),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 150,
      render: (t: string) => (
        <Text style={{ fontSize: 12 }}>{dayjs(t).format('MM-DD HH:mm:ss')}</Text>
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
          任务中心
        </Title>
        <Space>
          <Select
            allowClear
            placeholder="全部状态"
            style={{ width: 150 }}
            value={status}
            onChange={setStatus}
            options={Object.entries(STATUS_META).map(([v, m]) => ({ label: m.text, value: v }))}
          />
          <Button icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>
            刷新
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="文档上传、解析、向量化、冲突检测都是耗时操作，统一走异步任务"
        description={
          <Paragraph style={{ marginBottom: 0, fontSize: 12 }}>
            当前阶段任务在请求内同步执行完毕（接口形态已是异步契约）。
            接入 Celery 后，上传会立即返回 <Text code>task_id</Text>，
            调用方轮询本页面对应的接口获取进度，接口契约无需变更。
          </Paragraph>
        }
      />

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
        scroll={{ x: 1000 }}
      />

      <Drawer title="任务详情" open={!!detail} onClose={() => setDetail(null)} width={640}>
        {detail && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="任务 ID">
                <Text className="kb-mono">{detail.id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="类型">
                {TYPE_LABEL[detail.task_type] ?? detail.task_type}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {STATUS_META[detail.status]?.text ?? detail.status}
              </Descriptions.Item>
              <Descriptions.Item label="关联资源">
                {detail.ref_id ? `#${detail.ref_id}` : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                {detail.created_by_app ?? 'Web 界面'}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {dayjs(detail.created_at).format('YYYY-MM-DD HH:mm:ss')}
              </Descriptions.Item>
            </Descriptions>

            {detail.error_msg && (
              <Alert type="error" showIcon message="执行失败" description={detail.error_msg} />
            )}

            <div>
              <Text strong>结果数据</Text>
              <pre className="kb-chunk kb-mono" style={{ maxHeight: 400, overflow: 'auto', marginTop: 6 }}>
                {JSON.stringify(detail.result, null, 2)}
              </pre>
            </div>
          </Space>
        )}
      </Drawer>
    </Space>
  )
}
