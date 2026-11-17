import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Progress,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { EditOutlined, EyeOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { injectApi, promptsApi } from '../api'
import { http } from '../api/client'

const { Title, Text, Paragraph } = Typography

/* ───────────────────────── 干活线 ───────────────────────── */

type Part = {
  name: string
  label: string
  chars: number
  injected: boolean
  share: number
  source: string
}

const EDITABLE: Record<string, string[]> = {
  identity: ['identity'],
  discipline: ['redlines', 'priority', 'number_discipline'],
  tools: ['tools_guide'],
}

const CODE_LOCATION: Record<string, string> = {
  menu: 'kb.spec 目录行，由说明书自动生成',
  summary: '会话摘要，由历史轮次压缩生成',
  history: '前几轮对话，来自 qa.t_chat_message',
  files: '本会话带过的文件清单',
  spec: '本轮命中的说明书正文（kb.spec.body）',
}

function SourceTag({ source }: { source: string }) {
  const color =
    source === '个人' ? 'purple' : source === '全局' ? 'geekblue'
      : source === '库' ? 'blue' : source === '代码兜底' ? 'orange' : 'default'
  return <Tag color={color}>{source}</Tag>
}

function WorkLane() {
  const [question, setQuestion] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [bodies, setBodies] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [scope, setScope] = useState<'global' | 'user'>('global')

  const load = (q = question) => {
    setLoading(true)
    injectApi
      .preview({ question: q })
      .then((r) => setData(r))
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load('')
  }, [])

  const openEdit = async (part: Part) => {
    setOpenKey(part.name)
    const keys = EDITABLE[part.name] ?? []
    const next: Record<string, string> = {}
    for (const k of keys) {
      try {
        const r: any = await injectApi.text(k, scope)
        next[k] = r.body || ''
      } catch {
        next[k] = ''
      }
    }
    setBodies(next)
  }

  const save = async () => {
    setSaving(true)
    try {
      for (const [k, v] of Object.entries(bodies)) await injectApi.saveText(k, v, scope)
      message.success(scope === 'user' ? '已存成你自己的那份' : '已存成全局')
      await load()
      setOpenKey(null)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const parts: Part[] = data?.parts ?? []
  const total = data?.total ?? 0
  const budget = data?.budget ?? 6000
  const opened = parts.find((p) => p.name === openKey)

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap>
        <Input
          style={{ width: 380 }}
          placeholder="试跑：输入一句真实问题（留空只看常驻部分）"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onPressEnter={() => load()}
        />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>
          重新装配
        </Button>
        <Tag.CheckableTag checked={scope === 'global'} onChange={() => setScope('global')}>
          改全局
        </Tag.CheckableTag>
        <Tag.CheckableTag checked={scope === 'user'} onChange={() => setScope('user')}>
          改我自己的
        </Tag.CheckableTag>
        <Text type="secondary">
          合计 {total} 字 / 上限 {budget} 字
        </Text>
        {data?.over_budget ? <Tag color="red">超限</Tag> : null}
      </Space>

      <Progress percent={Math.min(100, Math.round((total / Math.max(budget, 1)) * 100))} size="small" />

      {(data?.compressed ?? []).length ? (
        <Alert
          type="warning"
          showIcon
          message={
            '超限压缩：' +
            (data.compressed as any[]).map((c) => `${c.name} ${c.from}→${c.to} 字`).join('；')
          }
        />
      ) : null}

      <Table
        size="small"
        rowKey="name"
        pagination={false}
        loading={loading}
        dataSource={parts}
        columns={[
          {
            title: '段',
            render: (_, p: Part, i) => (
              <Space size={4}>
                <Text type="secondary">{`①②③④⑤⑥⑦⑧`[i] ?? i + 1}</Text>
                <Text strong>{p.label}</Text>
              </Space>
            ),
          },
          { title: '来源', width: 120, render: (_, p: Part) => <SourceTag source={p.source} /> },
          { title: '字数', width: 90, render: (_, p: Part) => (p.injected ? p.chars : '—') },
          {
            title: '占比',
            width: 120,
            render: (_, p: Part) =>
              p.injected ? (
                <Progress percent={Math.round(p.share * 100)} size="small" showInfo={false} />
              ) : (
                '—'
              ),
          },
          {
            title: '操作',
            width: 150,
            render: (_, p: Part) => (
              <Button
                size="small"
                icon={EDITABLE[p.name] ? <EditOutlined /> : <EyeOutlined />}
                onClick={() => void openEdit(p)}
              >
                {EDITABLE[p.name] ? '看/改' : '看原文'}
              </Button>
            ),
          },
        ]}
      />

      <Drawer
        open={openKey != null}
        onClose={() => setOpenKey(null)}
        width={760}
        title={opened ? `${opened.label}　${opened.chars.toLocaleString()} 字` : ''}
        extra={
          EDITABLE[openKey ?? ''] ? (
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
              保存到{scope === 'user' ? '我的' : '全局'}
            </Button>
          ) : null
        }
      >
        {opened ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="段">{opened.label}</Descriptions.Item>
              <Descriptions.Item label="来源">
                <SourceTag source={opened.source} />
              </Descriptions.Item>
              <Descriptions.Item label="字数">{opened.chars.toLocaleString()}</Descriptions.Item>
              <Descriptions.Item label="是否注入">
                {opened.injected ? '本轮已注入' : '空，本轮没注入'}
              </Descriptions.Item>
              <Descriptions.Item label="生效方式" span={2}>
                保存后下一轮生效（缓存最多 60 秒）
              </Descriptions.Item>
            </Descriptions>
            {EDITABLE[openKey ?? ''] ? (
              EDITABLE[openKey ?? ''].map((k) => (
                <div key={k}>
                  <Text strong>{k}</Text>
                  <Input.TextArea
                    autoSize={{ minRows: 10, maxRows: 26 }}
                    value={bodies[k] ?? ''}
                    onChange={(e) => setBodies((b) => ({ ...b, [k]: e.target.value }))}
                  />
                </div>
              ))
            ) : (
              <Alert
                type="info"
                showIcon
                message={CODE_LOCATION[openKey ?? ''] || '这一段由代码或上一轮会话生成，不在库里'}
                description="要改它只能改代码；改代码不在这页做。"
              />
            )}
          </Space>
        ) : null}
      </Drawer>
    </Space>
  )
}

/* ───────────────────────── 问数线 ───────────────────────── */

type Item = {
  seg: string
  name: string
  where: string
  size: string
  editable: boolean
  takes_effect: string
  note: string
  endpoint: string
  editor: string
}

function SectionList({ slot }: { slot: string }) {
  const [secs, setSecs] = useState<any[]>([])
  const [bodies, setBodies] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState<number | null>(null)

  const load = () =>
    promptsApi
      .material(slot)
      .then((r: any) => {
        const list = r.sections ?? []
        setSecs(list)
        const next: Record<number, string> = {}
        list.forEach((s: any) => (next[s.id] = s.body ?? ''))
        setBodies(next)
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
  }, [slot])

  if (!secs.length) return <Empty description="这一槽没有内容" />

  const save = async (id: number) => {
    setSaving(id)
    try {
      await promptsApi.saveSection(id, bodies[id] ?? '')
      message.success('已保存，下一轮生效')
      await load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(null)
    }
  }

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      {secs.map((s: any) => (
        <div key={s.id}>
          <Space style={{ marginBottom: 4 }}>
            <Text strong>{s.section}</Text>
            <Text type="secondary">{String(s.body ?? '').length} 字</Text>
            {s.pinned ? <Tag color="blue">必带</Tag> : <Tag>按需</Tag>}
          </Space>
          <Input.TextArea
            autoSize={{ minRows: 12, maxRows: 30 }}
            value={bodies[s.id] ?? ''}
            onChange={(e) => setBodies((b) => ({ ...b, [s.id]: e.target.value }))}
          />
          <Space style={{ marginTop: 6 }}>
            <Button
              type="primary"
              size="small"
              icon={<SaveOutlined />}
              loading={saving === s.id}
              onClick={() => void save(s.id)}
            >
              保存这一节
            </Button>
            <Text type="secondary">存完下一轮问答就用新内容</Text>
          </Space>
        </div>
      ))}
    </Space>
  )
}

function TextKeyEditor({ keyName }: { keyName: string }) {
  const [body, setBody] = useState('')
  const [saving, setSaving] = useState(false)

  const load = () =>
    promptsApi
      .texts()
      .then((r: any) => {
        const list: any[] = Array.isArray(r.items) ? r.items : Object.values(r.items ?? {})
        const v = list.find((x: any) => x.key === keyName || x.name === keyName)
        setBody(v?.body ?? '')
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
  }, [keyName])

  const save = async () => {
    setSaving(true)
    try {
      await promptsApi.saveTextKey(keyName, body)
      message.success('已保存，下一轮生效')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Input.TextArea
        autoSize={{ minRows: 12, maxRows: 30 }}
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <Space>
        <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
          保存这一段
        </Button>
        <Text type="secondary">{body.length} 字 · 存完下一轮生效</Text>
      </Space>
    </Space>
  )
}

function ReadOnlyBody({ endpoint }: { endpoint: string }) {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    if (!endpoint) return
    http
      .get(endpoint)
      .then((r: any) => setData(r))
      .catch((e) => message.error((e as Error).message))
  }, [endpoint])

  if (!endpoint) return <Empty description="这一项只能在代码里看" />
  if (!data) return <Empty description="读取中…" />

  const rows: any[] = Array.isArray(data) ? data
    : Array.isArray(data.items) ? data.items
      : Array.isArray(data.rows) ? data.rows
        : []

  if (rows.length) {
    const keys = Object.keys(rows[0]).filter((k) => !['ok'].includes(k)).slice(0, 6)
    return (
      <Table
        size="small"
        rowKey={(r: any, i?: number) => String(r.id ?? r.key ?? r.table_name ?? i)}
        dataSource={rows}
        pagination={{ pageSize: 10 }}
        columns={keys.map((k) => ({
          title: k,
          dataIndex: k,
          render: (v: any) => (typeof v === 'object' ? JSON.stringify(v) : String(v ?? '')),
        }))}
      />
    )
  }

  const text = typeof data === 'string' ? data
    : Object.entries(data).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('\n')
  return (
    <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0, maxHeight: 500, overflow: 'auto' }}>
      {text.slice(0, 20000)}
    </pre>
  )
}

function AskLane() {
  const [rows, setRows] = useState<Item[]>([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Item | null>(null)

  useEffect(() => {
    promptsApi
      .impact()
      .then((r: any) => {
        const flat: Item[] = []
        for (const s of r.segments ?? []) {
          for (const it of s.items ?? []) flat.push({ ...it, seg: s.name })
        }
        setRows(flat)
      })
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        一次问数回答被 {rows.length} 项输入影响。点「查看」看这一项现在是什么、能改的就地改，保存后下一轮生效。
      </Paragraph>

      <Table
        size="small"
        rowKey="name"
        loading={loading}
        pagination={false}
        dataSource={rows}
        columns={[
          { title: '环节', dataIndex: 'seg', width: 190 },
          { title: '输入', render: (_: any, it: Item) => <Text strong>{it.name}</Text> },
          { title: '存在哪', width: 210, render: (_: any, it: Item) => <Text code>{it.where}</Text> },
          { title: '大小', dataIndex: 'size', width: 130 },
          {
            title: '状态',
            width: 100,
            render: (_: any, it: Item) => (it.editable ? <Tag color="green">可改</Tag> : <Tag>只读</Tag>),
          },
          { title: '生效方式', dataIndex: 'takes_effect', width: 110 },
          {
            title: '操作',
            width: 110,
            render: (_: any, it: Item) => (
              <Button
                size="small"
                icon={it.editable ? <EditOutlined /> : <EyeOutlined />}
                onClick={() => setOpen(it)}
              >
                查看
              </Button>
            ),
          },
        ]}
      />

      <Drawer
        open={open != null}
        onClose={() => setOpen(null)}
        width={820}
        title={open ? `${open.name}　${open.size}` : ''}
      >
        {open ? (
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="环节">{open.seg}</Descriptions.Item>
              <Descriptions.Item label="存在哪">
                <Text code>{open.where}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="大小">{open.size}</Descriptions.Item>
              <Descriptions.Item label="能不能改">
                {open.editable ? '页面上可改' : '只读'}
              </Descriptions.Item>
              <Descriptions.Item label="生效方式">{open.takes_effect}</Descriptions.Item>
              <Descriptions.Item label="它在决定什么">{open.note}</Descriptions.Item>
            </Descriptions>

            {open.editor.startsWith('material:') ? (
              <SectionList slot={open.editor.split(':')[1]} />
            ) : open.editor.startsWith('text:') ? (
              <TextKeyEditor keyName={open.editor.split(':')[1]} />
            ) : (
              <ReadOnlyBody endpoint={open.endpoint} />
            )}
          </Space>
        ) : null}
      </Drawer>
    </Space>
  )
}

export default function InjectPanels() {
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space align="baseline">
        <Title level={4} style={{ margin: 0 }}>
          提示词
        </Title>
        <Tag color="blue">两条线分开看</Tag>
      </Space>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        每一项都显示字数、来源、状态、生效方式；点开抽屉看全文并就地改。
      </Paragraph>
      <Card size="small">
        <Tabs
          items={[
            { key: 'ask', label: '问数线', children: <AskLane /> },
            { key: 'work', label: '干活线', children: <WorkLane /> },
          ]}
        />
      </Card>
    </Space>
  )
}
