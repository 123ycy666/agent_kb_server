import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import CaliberMetricDrawer, { mustText } from '../components/CaliberMetricDrawer'
import type { ParsedMetric } from '../components/CaliberMetricDrawer'
import { http } from '../api/client'
import Prompts from './Prompts'

const { Title, Text, Paragraph } = Typography

function CaliberPanel() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [edit, setEdit] = useState<ParsedMetric | null>(null)
  const [tryData, setTryData] = useState<any>(null)
  const [tryBusy, setTryBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r: any = await http.get('/caliber/metrics')
      setRows(r.metrics ?? [])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const openEdit = (m: any) => {
    setEdit({
      code: m.code,
      label: m.label,
      kind: m.expr ? 'derived' : 'base',
      expr: m.expr || '',
      unit: m.unit || '',
      digits: m.digits ?? 0,
      must: (m.bases ?? []).flatMap((b: any) => b.must ?? []),
      bases: m.bases ?? [],
    })
  }

  const tryRun = async (code: string) => {
    setTryBusy(true)
    setTryData(null)
    try {
      const r: any = await http.get('/caliber/try', {
        params: { code, year: String(new Date().getFullYear()) },
      })
      setTryData(r)
    } catch (e) {
      setTryData({ error: (e as Error).message })
    } finally {
      setTryBusy(false)
    }
  }

  const columns: ColumnsType<any> = [
    {
      title: '指标',
      width: 210,
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <Text strong>{r.label}</Text>
          <Text type="secondary" className="kb-mono" style={{ fontSize: 11 }}>
            {r.code}
          </Text>
        </Space>
      ),
    },
    { title: '单位', dataIndex: 'unit', width: 70, render: (v) => v || '—' },
    {
      title: '算式',
      dataIndex: 'expr',
      render: (v: string) =>
        v ? (
          <Text code style={{ fontSize: 12 }}>
            {v}
          </Text>
        ) : (
          <Tag>取数</Tag>
        ),
    },
    {
      title: '必需过滤',
      width: 300,
      render: (_, r) => {
        const chips = Array.from(
          new Set(((r.bases ?? []) as any[]).flatMap((b) => (b.must ?? []).map(mustText)))
        )
        return chips.length ? (
          <Space size={4} wrap>
            {chips.map((c) => (
              <Tag key={c}>{c}</Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">—</Text>
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 80,
      render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '停用'}</Tag>,
    },
    {
      title: '版本',
      dataIndex: 'version_no',
      width: 70,
      render: (v: number) => `v${v ?? 1}`,
    },
    {
      title: '操作',
      width: 150,
      render: (_, r) => (
        <Space size={4}>
          <Button size="small" onClick={() => openEdit(r)}>
            改口径
          </Button>
          <Button size="small" type="link" loading={tryBusy} onClick={() => void tryRun(r.code)}>
            试跑
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="口径是一行数据，不是代码"
        description="这里的指标口径、必需过滤、派生算式只影响问数线。改完下一轮取数生效，不用重启。"
      />
      <Card size="small" title={`指标口径（${rows.length}）`} extra={<Button size="small" icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>刷新</Button>}>
        <Table
          rowKey="code"
          size="small"
          loading={loading}
          dataSource={rows}
          columns={columns}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 1100 }}
        />
      </Card>
      {edit ? (
        <CaliberMetricDrawer
          seed={edit}
          onClose={() => setEdit(null)}
          onSaved={() => void load()}
        />
      ) : null}
      <Modal
        open={!!tryData}
        title="按契约试跑一次"
        onCancel={() => setTryData(null)}
        footer={<Button onClick={() => setTryData(null)}>关闭</Button>}
      >
        {tryData?.error ? (
          <Alert type="error" showIcon message={tryData.error} />
        ) : tryData ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text strong>
              {tryData.label}：{tryData.value}
              {tryData.unit}
            </Text>
            {(tryData.detail ?? []).map((d: any) => (
              <Card key={d.code} size="small" title={d.code}>
                <Text>{String(d.value)}</Text>
                <pre style={{ whiteSpace: 'pre-wrap', fontSize: 11, marginBottom: 0 }}>{d.sql}</pre>
              </Card>
            ))}
          </Space>
        ) : null}
      </Modal>
    </Space>
  )
}

function CompleteRules() {
  const [rows, setRows] = useState<any[]>([])
  const [deleted, setDeleted] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r: any = await http.get('/prompts/complete')
      setRows(
        (r.rows ?? []).map((x: any) => ({
          id: x.id,
          kind: x.kind || 'norm',
          match_text: x.match_text || '',
          fill_text: x.fill_text || '',
          priority: x.priority ?? 0,
        }))
      )
      setDeleted(r.deleted ?? [])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const setCell = (i: number, k: string, v: any) =>
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, [k]: v } : r)))

  const save = async () => {
    setSaving(true)
    try {
      const r: any = await http.put('/prompts/complete', { rows })
      if (r?.ok) {
        message.success(`已保存 ${r.rows} 条，下一轮问数生效`)
        await load()
      } else message.error(r?.error || '保存失败')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="补全表只影响问数线"
        description="匹配到一条，就把 fill_text 作为参考资料交给问数模型，不替用户改问题。整表保存，改完下一轮生效。"
      />
      <Card
        size="small"
        title={`补全条目（${rows.length}）`}
        extra={
          <Space>
            <Button size="small" onClick={() => void load()} loading={loading}>
              刷新
            </Button>
            <Button
              size="small"
              onClick={() =>
                setRows((v) => [
                  ...v,
                  { kind: 'norm', match_text: '', fill_text: '', priority: (v.length + 1) * 10 },
                ])
              }
            >
              加一行
            </Button>
            <Button size="small" type="primary" loading={saving} onClick={() => void save()}>
              保存
            </Button>
          </Space>
        }
      >
        <Table
          rowKey={(r: any, i?: number) => String(r.id || `new-${i}`)}
          size="small"
          loading={loading}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 900 }}
          columns={[
            {
              title: '类型',
              dataIndex: 'kind',
              width: 100,
              render: (v: string, _r, i) => (
                <Select
                  size="small"
                  value={v}
                  style={{ width: 90 }}
                  options={[
                    { value: 'norm', label: '归一' },
                    { value: 'fill', label: '补缺' },
                  ]}
                  onChange={(x) => setCell(i, 'kind', x)}
                />
              ),
            },
            {
              title: '匹配',
              dataIndex: 'match_text',
              render: (v: string, _r, i) => (
                <Input size="small" value={v} onChange={(e) => setCell(i, 'match_text', e.target.value)} />
              ),
            },
            {
              title: '补成什么',
              dataIndex: 'fill_text',
              render: (v: string, _r, i) => (
                <Input size="small" value={v} onChange={(e) => setCell(i, 'fill_text', e.target.value)} />
              ),
            },
            {
              title: '优先级',
              dataIndex: 'priority',
              width: 100,
              render: (v: number, _r, i) => (
                <InputNumber
                  size="small"
                  value={v}
                  style={{ width: 80 }}
                  onChange={(x) => setCell(i, 'priority', x ?? 0)}
                />
              ),
            },
            {
              title: '操作',
              width: 80,
              render: (_v, _r, i) => (
                <Button
                  size="small"
                  danger
                  type="link"
                  onClick={() => setRows((prev) => prev.filter((_, j) => j !== i))}
                >
                  删
                </Button>
              ),
            },
          ]}
        />
        {deleted.length > 0 ? (
          <Card size="small" title={`已删除（${deleted.length}，可恢复）`} style={{ marginTop: 12 }}>
            {deleted.map((r) => (
              <Space key={r.id} style={{ display: 'flex', marginBottom: 4 }}>
                <Text delete type="secondary">
                  {r.match_text} → {r.fill_text}
                </Text>
                <Button
                  size="small"
                  onClick={async () => {
                    await http.post(`/prompts/complete/${r.id}/restore`)
                    await load()
                  }}
                >
                  恢复
                </Button>
              </Space>
            ))}
          </Card>
        ) : null}
      </Card>
    </Space>
  )
}

export default function SearchTest() {
  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>
          问数
        </Title>
        <Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 12 }}>
          预处理规则、指标口径、补全表都在这页维护。
        </Paragraph>
      </div>
      <Alert
        type="warning"
        showIcon
        message="本页只影响问数线"
        description="回答口径、指标取数、补全表只作用于问数；办公线的身份、红线、注入预算请到「提示词与注入」，说明书请到「知识库」。"
      />
      <Tabs
        defaultActiveKey="caliber"
        items={[
          { key: 'caliber', label: '指标口径', children: <CaliberPanel /> },
          { key: 'rules', label: '问数规则', children: <Prompts /> },
          { key: 'complete', label: '补全表', children: <CompleteRules /> },
        ]}
      />
    </Space>
  )
}
