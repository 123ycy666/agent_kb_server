import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined, ScanOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { conflictApi, kbApi } from '../api'
import type { ConflictItem, KnowledgeBase } from '../api'
import { useAuth } from '../store/auth'

const { Title, Text, Paragraph } = Typography

const RELATION_LABEL: Record<string, { text: string; color: string }> = {
  contradiction: { text: '真冲突', color: 'red' },
  entailment: { text: '蕴含/细化', color: 'blue' },
  duplication: { text: '重复', color: 'default' },
  unrelated: { text: '无关', color: 'default' },
}

const TYPE_LABEL: Record<string, string> = {
  fact: '事实型',
  rule: '规则型',
  temporal: '时效型',
  wording: '表述型',
}

const RESOLUTION_OPTIONS = [
  { label: '覆盖旧版', value: 'overwrite' },
  { label: '保留旧版', value: 'keep_old' },
  { label: '并存', value: 'coexist' },
  { label: '忽略', value: 'ignore' },
]

export default function ConflictList() {
  const { has } = useAuth()
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [kbId, setKbId] = useState<number | undefined>()
  const [status, setStatus] = useState<string>('pending')
  const [items, setItems] = useState<ConflictItem[]>([])
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [choices, setChoices] = useState<Record<number, string>>({})
  const [scanning, setScanning] = useState(false)

  const doFullScan = async () => {
    if (!kbId) {
      message.warning('请先选择要扫描的知识库')
      return
    }
    setScanning(true)
    try {
      const res = await conflictApi.fullScan(kbId, true)
      if (res.total_conflicts > 0) {
        message.warning(res.message)
      } else {
        message.success(res.message)
      }
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setScanning(false)
    }
  }

  const load = async () => {
    setLoading(true)
    try {
      const [list, st, k] = await Promise.allSettled([
        conflictApi.list({ kb_id: kbId, status: status || undefined, page_size: 100 }),
        conflictApi.stats(kbId),
        kbApi.list(1, 100),
      ])
      if (list.status === 'fulfilled') {
        setItems(list.value.items)
        const init: Record<number, string> = {}
        list.value.items.forEach((c) => {
          init[c.id] = 'overwrite'
        })
        setChoices(init)
      }
      if (st.status === 'fulfilled') setStats(st.value)
      if (k.status === 'fulfilled') setKbs(k.value.items)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [kbId, status])

  const resolve = async (c: ConflictItem) => {
    try {
      await conflictApi.resolve(c.id, choices[c.id] ?? 'overwrite')
      message.success('已处理')
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const columns: ColumnsType<ConflictItem> = [
    {
      title: '关系',
      dataIndex: 'relation',
      width: 100,
      render: (r: string) => {
        const cfg = RELATION_LABEL[r] ?? { text: r, color: 'default' }
        return <Tag color={cfg.color}>{cfg.text}</Tag>
      },
    },
    {
      title: '类型',
      dataIndex: 'conflict_type',
      width: 90,
      render: (t: string) => <Tag>{TYPE_LABEL[t] ?? t}</Tag>,
    },
    {
      title: '冲突文件',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Text style={{ fontSize: 12 }}>
            新：<Text strong>{row.new_doc_name ?? `#${row.new_doc_id}`}</Text>
          </Text>
          <Text style={{ fontSize: 12 }}>
            旧：<Text strong>{row.old_doc_name ?? `#${row.old_doc_id}`}</Text>
            {row.old_doc_version ? ` (v${row.old_doc_version})` : ''}
          </Text>
        </Space>
      ),
    },
    {
      title: '判定理由',
      dataIndex: 'reason',
      render: (r: string, row) => (
        <Space direction="vertical" size={4} style={{ maxWidth: 420 }}>
          <Text style={{ fontSize: 12 }}>{r}</Text>
          {row.new_snippet && (
            <div className="kb-snippet-new" style={{ fontSize: 11, padding: '4px 8px' }}>
              新：{row.new_snippet.slice(0, 120)}
            </div>
          )}
          {row.old_snippet && (
            <div className="kb-snippet-old" style={{ fontSize: 11, padding: '4px 8px' }}>
              旧：{row.old_snippet.slice(0, 120)}
            </div>
          )}
        </Space>
      ),
    },
    {
      title: '相似度',
      dataIndex: 'similarity_score',
      width: 90,
      render: (s: number) => (s != null ? Number(s).toFixed(3) : '—'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => (
        <Tag color={s === 'pending' ? 'warning' : s === 'resolved' ? 'success' : 'default'}>
          {s === 'pending' ? '待处理' : s === 'resolved' ? '已处理' : '已忽略'}
        </Tag>
      ),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 150,
      render: (t: string) => (
        <Text style={{ fontSize: 12 }}>{dayjs(t).format('YYYY-MM-DD HH:mm')}</Text>
      ),
    },
    {
      title: '处理',
      width: 240,
      render: (_, row) =>
        row.status !== 'pending' ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {row.resolution ?? '—'}
          </Text>
        ) : has('conflict:resolve') ? (
          <Space direction="vertical" size={4}>
            <Radio.Group
              size="small"
              value={choices[row.id]}
              onChange={(e) => setChoices((s) => ({ ...s, [row.id]: e.target.value }))}
            >
              {RESOLUTION_OPTIONS.map((o) => (
                <Radio.Button key={o.value} value={o.value} style={{ fontSize: 12 }}>
                  {o.label}
                </Radio.Button>
              ))}
            </Radio.Group>
            <Button type="primary" size="small" onClick={() => resolve(row)}>
              确认处理
            </Button>
          </Space>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            无处理权限
          </Text>
        ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Title level={4} style={{ margin: 0 }}>
          冲突管理
        </Title>
        <Space>
          <Select
            allowClear
            placeholder="全部知识库"
            style={{ width: 200 }}
            value={kbId}
            onChange={setKbId}
            options={kbs.map((k) => ({ label: k.name, value: k.id }))}
          />
          <Select
            style={{ width: 130 }}
            value={status}
            onChange={setStatus}
            options={[
              { label: '待处理', value: 'pending' },
              { label: '已处理', value: 'resolved' },
              { label: '已忽略', value: 'ignored' },
              { label: '全部', value: '' },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
          {has('system:scan') && (
            <Popconfirm
              title="执行全库一致性扫描？"
              description={
                <span>
                  将检查该知识库内<b>全部文档（含你不可见的文档）</b>，
                  用于补上「受限文档与公开文档冲突却无人发现」的盲区。
                  会消耗模型调用，操作将记入审计日志。
                </span>
              }
              okText="开始扫描"
              cancelText="取消"
              onConfirm={doFullScan}
            >
              <Button icon={<ScanOutlined />} loading={scanning} disabled={!kbId}>
                全库扫描
              </Button>
            </Popconfirm>
          )}
        </Space>
      </Space>

      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title="冲突总数" value={stats?.total ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="待处理"
              value={stats?.pending ?? 0}
              valueStyle={{ color: (stats?.pending ?? 0) > 0 ? '#cf1322' : undefined }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已处理" value={stats?.resolved ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已忽略" value={stats?.ignored ?? 0} />
          </Card>
        </Col>
      </Row>

      <Alert
        type="info"
        showIcon
        message="关于冲突检测的权限边界"
        description={
          <Paragraph style={{ marginBottom: 0, fontSize: 12 }}>
            检测范围仅包含<b>你有权访问的文档</b>。受限文档完全不参与比对与提示，
            避免通过上传文件探测到无权查看的内容。
            管理员可另行发起「全库一致性扫描」覆盖全部文档（操作会记入审计日志）。
          </Paragraph>
        }
      />

      <Table
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        locale={{ emptyText: <Empty description="暂无冲突记录" /> }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        scroll={{ x: 1200 }}
      />
    </Space>
  )
}
