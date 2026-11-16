import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Input,
  InputNumber,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { EditOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { injectApi } from '../api'
import type { InjectItem } from '../api'
import { http } from '../api/client'
import InjectPreview from '../components/InjectPreview'
import StateBadge from '../components/StateBadge'

const { Title, Text, Paragraph } = Typography

const TEXT_KEY: Record<string, string> = {
  identity: 'identity',
  redlines: 'redlines',
  priority: 'priority',
  tools_guide: 'tools',
}

export default function DocumentList() {
  const [items, setItems] = useState<InjectItem[]>([])
  const [budget, setBudget] = useState(6000)
  const [affects, setAffects] = useState('')
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [savingBudget, setSavingBudget] = useState(false)
  const [edit, setEdit] = useState<InjectItem | null>(null)
  const [body, setBody] = useState('')
  const [bodyLoading, setBodyLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r = await injectApi.items()
      setItems(r.items ?? [])
      setBudget(r.budget ?? 6000)
      setAffects(r.affects || '')
      setNote(r.note || '')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const totalChars = useMemo(() => items.reduce((sum, x) => sum + x.chars, 0), [items])

  const saveBudget = async () => {
    setSavingBudget(true)
    try {
      const r: any = await injectApi.setBudget(budget)
      if (r?.error) message.error(r.error)
      else {
        message.success(`常驻预算已改为 ${r.resident_chars} 字，下一轮生效`)
        await load()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSavingBudget(false)
    }
  }

  const openEdit = async (row: InjectItem) => {
    setEdit(row)
    setBody('')
    const key = TEXT_KEY[row.k]
    if (!key) return
    setBodyLoading(true)
    try {
      const r: any = await http.get('/prompts/texts')
      const hit = (r.items ?? []).find((x: any) => x.key === key)
      setBody(hit?.body ?? '')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBodyLoading(false)
    }
  }

  const save = async () => {
    if (!edit) return
    setSaving(true)
    try {
      const r: any = await injectApi.saveText(edit.k, body)
      if (r?.error) message.error(r.error)
      else {
        message.success(`${edit.label}已保存，${r.chars} 字；下一轮生效`)
        setEdit(null)
        await load()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const columns: ColumnsType<InjectItem> = [
    {
      title: '注入项',
      width: 168,
      render: (_, r) => (
        <Space direction="vertical" size={2}>
          <Space size={6}>
            <Text strong>{r.label}</Text>
            <StateBadge
              state={r.state}
              source={r.source}
              updatedBy={r.updated_by}
              updatedAt={r.updated_at}
              extra={<div>生效：保存后下一轮（≤60 秒）</div>}
            />
          </Space>
          <Text type="secondary" className="kb-mono" style={{ fontSize: 11 }}>
            {r.k}
          </Text>
        </Space>
      ),
    },
    {
      title: '字数',
      dataIndex: 'chars',
      width: 60,
      align: 'right',
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 124,
      ellipsis: true,
      render: (v: string) => <Text style={{ fontSize: 11 }}>{v}</Text>,
    },
    {
      title: '最近修改',
      width: 112,
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <Text style={{ fontSize: 12 }}>{r.updated_by || '—'}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {r.updated_at ? dayjs(r.updated_at).format('YYYY-MM-DD HH:mm') : '—'}
          </Text>
        </Space>
      ),
    },
    {
      title: '生效方式',
      width: 108,
      render: () => (
        <Tag color="blue" style={{ whiteSpace: 'normal', lineHeight: 1.4 }}>
          下一轮 · 60 秒
        </Tag>
      ),
    },
    {
      title: '操作',
      width: 66,
      render: (_, r) => (
        <Button size="small" icon={<EditOutlined />} onClick={() => void openEdit(r)}>
          编辑
        </Button>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
        <div>
          <Space align="baseline">
            <Title level={4} style={{ margin: 0 }}>
              提示词与注入
            </Title>
            <Tag color="blue">{affects || '干活线'}</Tag>
          </Space>
          <Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 12 }}>
            每一项都显示字数、来源、最近修改、生效方式；右侧抽屉看全文与版本。
          </Paragraph>
        </div>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
          刷新
        </Button>
      </Space>

      <Alert
        type="warning"
        showIcon
        message="本页只影响干活线（办公 / 读件 / 闲聊）"
        description={note || '问数链路的口径材料在「问数」页，不在这里。'}
      />

      <Row gutter={14} align="stretch">
        <Col span={14}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <Card size="small" title="常驻预算">
              <Space wrap align="center">
                <Text>常驻上限</Text>
                <InputNumber
                  min={1000}
                  max={40000}
                  step={500}
                  value={budget}
                  onChange={(v) => setBudget(Number(v || 1000))}
                  addonAfter="字"
                  style={{ width: 160 }}
                />
                <Button type="primary" loading={savingBudget} onClick={() => void saveBudget()}>
                  保存预算
                </Button>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  默认 6000 字；超限先压历史、再压目录、最后压摘要。
                </Text>
              </Space>
              <Row gutter={12} style={{ marginTop: 12 }}>
                <Col span={8}>
                  <Statistic title="当前注入项" value={items.length} suffix="项" />
                </Col>
                <Col span={8}>
                  <Statistic title="注入项合计" value={totalChars} suffix="字" />
                </Col>
                <Col span={8}>
                  <Statistic title="预算余量" value={Math.max(budget - totalChars, 0)} suffix="字" />
                </Col>
              </Row>
            </Card>

            <Card size="small" title="注入项">
              <Table
                rowKey="k"
                size="small"
                loading={loading}
                dataSource={items}
                columns={columns}
                pagination={false}
              />
            </Card>
          </Space>
        </Col>

        <Col span={10}>
          <InjectPreview question="预算执行情况" compact />
        </Col>
      </Row>

      <Drawer
        open={!!edit}
        width={760}
        title={edit ? `${edit.label}　${edit.chars.toLocaleString()} 字` : '注入项'}
        onClose={() => setEdit(null)}
        extra={
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
            保存
          </Button>
        }
      >
        {edit ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="键">{edit.k}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <StateBadge
                  state={edit.state}
                  source={edit.source}
                  updatedBy={edit.updated_by}
                  updatedAt={edit.updated_at}
                />
              </Descriptions.Item>
              <Descriptions.Item label="来源">{edit.source}</Descriptions.Item>
              <Descriptions.Item label="版本">v{edit.version_no}</Descriptions.Item>
              <Descriptions.Item label="最近修改" span={2}>
                {edit.updated_by || '—'} ·{' '}
                {edit.updated_at ? dayjs(edit.updated_at).format('YYYY-MM-DD HH:mm:ss') : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="生效方式" span={2}>
                保存后下一轮生效（缓存最多 60 秒）
              </Descriptions.Item>
            </Descriptions>
            <Alert
              type="info"
              showIcon
              message="这里是注入文本的唯一真身；保存后按注入顺序参与下一轮装配。"
            />
            <div>
              <Text strong>全文</Text>
              <Input.TextArea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                autoSize={{ minRows: 18, maxRows: 32 }}
                style={{ marginTop: 6, fontSize: 12 }}
                placeholder={bodyLoading ? '读取中…' : '这段还没有正文，可以直接写'}
              />
            </div>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  )
}
