import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { EditOutlined, EyeOutlined, SaveOutlined } from '@ant-design/icons'
import { injectApi, promptsApi } from '../api'

const { Title, Text, Paragraph } = Typography

type Lane = 'ask' | 'office'

type Item = {
  key: string
  seg: string
  name: string
  where: string
  size: string
  editable: boolean
  takes_effect: string
  note: string
  editor: string
}

/* ───────────────────────── 每一小项的原文 ───────────────────────── */

function BodyView({ data }: { data: any }) {
  if (!data) return <Empty description="读取中…" />
  const rows: any[] = data.rows ?? []
  if (data.kind === 'rows' && rows.length) {
    const cols: string[] = data.columns ?? []
    return (
      <Table
        size="small"
        rowKey={(_, i?: number) => String(i)}
        dataSource={rows}
        scroll={{ x: 'max-content', y: 460 }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        columns={cols.map((c) => ({ title: c, dataIndex: c }))}
      />
    )
  }
  return (
    <pre
      style={{
        width: '100%',
        boxSizing: 'border-box',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        fontSize: 12,
        margin: 0,
        maxHeight: 620,
        overflow: 'auto',
      }}
    >
      {String(data.text ?? '')}
    </pre>
  )
}

/* ───────────────────────── 就地改：材料分节 ───────────────────────── */

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
        <div key={s.id} style={{ width: '100%' }}>
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

/* ───────────────────────── 就地改：文本键 ───────────────────────── */

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

/* ───────────────────────── 就地改：注入文本（cfg.inject_text）────────── */

function InjectTextEditor({ keyName }: { keyName: string }) {
  const [body, setBody] = useState('')
  const [scope, setScope] = useState<'global' | 'user'>('global')
  const [saving, setSaving] = useState(false)

  const load = () =>
    injectApi
      .text(keyName, scope)
      .then((r: any) => setBody(r?.body ?? ''))
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
  }, [keyName, scope])

  const save = async () => {
    setSaving(true)
    try {
      await injectApi.saveText(keyName, body, scope)
      message.success(scope === 'user' ? '已存成你自己的那份' : '已存成全局')
      await load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Space>
        <Tag.CheckableTag checked={scope === 'global'} onChange={() => setScope('global')}>
          改全局
        </Tag.CheckableTag>
        <Tag.CheckableTag checked={scope === 'user'} onChange={() => setScope('user')}>
          改我自己的
        </Tag.CheckableTag>
        <Text type="secondary">{keyName}</Text>
      </Space>
      <Input.TextArea
        autoSize={{ minRows: 10, maxRows: 26 }}
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <Space>
        <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
          保存到{scope === 'user' ? '我的' : '全局'}
        </Button>
        <Text type="secondary">{body.length} 字 · 60 秒内生效</Text>
      </Space>
    </Space>
  )
}

/* ───────────────────────── 就地改：补问法 ───────────────────────── */

function AskfillEditor() {
  const [on, setOn] = useState(true)
  const [dflt, setDflt] = useState('')
  const [lists, setLists] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const load = () =>
    promptsApi
      .texts()
      .then((r: any) => {
        const list: any[] = Array.isArray(r.items) ? r.items : Object.values(r.items ?? {})
        const v = list.find((x: any) => x.key === 'askfill')
        let cfg: any = {}
        try {
          cfg = JSON.parse(v?.body || '{}')
        } catch {
          cfg = {}
        }
        setOn(cfg.on !== false)
        setDflt(String(cfg.default || ''))
        const next: Record<string, string> = {}
        for (const k of ['asked', 'topic', 'how']) {
          const arr = Array.isArray(cfg[k]) ? cfg[k] : []
          next[k] = arr.join('\n')
        }
        setLists(next)
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      const payload: any = {
        on,
        default: dflt,
        asked: (lists.asked || '').split('\n').map((s) => s.trim()).filter(Boolean),
        topic: (lists.topic || '').split('\n').map((s) => s.trim()).filter(Boolean),
        how: (lists.how || '').split('\n').map((s) => s.trim()).filter(Boolean),
      }
      await promptsApi.saveTextKey('askfill', JSON.stringify(payload, null, 2))
      message.success('已保存，下一轮生效')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space>
        <Text strong>补问开关</Text>
        <Tag.CheckableTag checked={on} onChange={setOn}>
          {on ? '开：缺条件会补问一句' : '关：不补问，直接答'}
        </Tag.CheckableTag>
      </Space>
      <div>
        <Text strong>默认补问话术</Text>
        <Input value={dflt} onChange={(e) => setDflt(e.target.value)} />
      </div>
      {[
        ['asked', '会问哪些（一行一个）'],
        ['topic', '按主题问（一行一个）'],
        ['how', '怎么问（一行一个）'],
      ].map(([k, label]) => (
        <div key={k}>
          <Text strong>{label}</Text>
          <Input.TextArea
            autoSize={{ minRows: 4, maxRows: 12 }}
            value={lists[k] ?? ''}
            onChange={(e) => setLists((b) => ({ ...b, [k]: e.target.value }))}
          />
        </div>
      ))}
      <Space>
        <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
          保存补问法
        </Button>
        <Text type="secondary">存完下一轮生效</Text>
      </Space>
    </Space>
  )
}

/* ───────────────────────── 一条线的全链路 ───────────────────────── */

function ChainLane({ lane }: { lane: Lane }) {
  const [rows, setRows] = useState<Item[]>([])
  const [spans, setSpans] = useState<number[]>([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Item | null>(null)
  const [bd, setBd] = useState<any>(null)

  const loadSegs = lane === 'ask' ? promptsApi.impact : promptsApi.impactOffice
  const loadBody = lane === 'ask' ? promptsApi.impactBody : promptsApi.impactOfficeBody

  useEffect(() => {
    setLoading(true)
    loadSegs()
      .then((r: any) => {
        const flat: Item[] = []
        const sp: number[] = []
        for (const s of r.segments ?? []) {
          const list = s.items ?? []
          list.forEach((it: any, i: number) => {
            flat.push({ ...it, seg: s.name })
            sp.push(i === 0 ? list.length : 0)
          })
        }
        setRows(flat)
        setSpans(sp)
      })
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false))
  }, [lane])

  useEffect(() => {
    if (!open) {
      setBd(null)
      return
    }
    setBd(null)
    loadBody(open.key)
      .then((r: any) => setBd(r))
      .catch((e) => message.error((e as Error).message))
  }, [open])

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        按处理顺序全量列出：一次回答被 {rows.length} 项输入影响。点开任何一项都能看到它现在的原文 ——
        可改的就地改，只读的标清在哪、改完多久生效。
      </Paragraph>

      <Table
        size="small"
        rowKey="key"
        loading={loading}
        pagination={false}
        dataSource={rows}
        columns={[
          {
            title: '环节',
            dataIndex: 'seg',
            width: 175,
            onCell: (_: Item, i?: number) => ({ rowSpan: spans[i ?? 0] ?? 1 }),
            render: (v: string) => <Text strong>{v}</Text>,
          },
          {
            title: '#',
            width: 44,
            render: (_: any, __: Item, i: number) => <Text type="secondary">{i + 1}</Text>,
          },
          { title: '输入', render: (_: any, it: Item) => <Text strong>{it.name}</Text> },
          { title: '存在哪', width: 235, render: (_: any, it: Item) => <Text code>{it.where}</Text> },
          { title: '大小', dataIndex: 'size', width: 130 },
          {
            title: '状态',
            width: 86,
            render: (_: any, it: Item) => (it.editable ? <Tag color="green">可改</Tag> : <Tag>只读</Tag>),
          },
          { title: '生效方式', dataIndex: 'takes_effect', width: 118 },
          {
            title: '操作',
            width: 96,
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
        width={900}
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
            ) : open.editor === 'askfill' ? (
              <AskfillEditor />
            ) : open.editor.startsWith('text:') ? (
              <TextKeyEditor keyName={open.editor.split(':')[1]} />
            ) : open.editor.startsWith('inject:') ? (
              <InjectTextEditor keyName={open.editor.split(':')[1]} />
            ) : null}

            <div style={{ width: '100%', minWidth: 0 }}>
              <Space size={8} style={{ marginBottom: 6 }}>
                <Text strong>{open.editor ? '这一项现在注入的原文' : '原文'}</Text>
                {bd?.hint ? <Text type="secondary">{bd.hint}</Text> : null}
              </Space>
              <BodyView data={bd} />
            </div>
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
        每一项都显示在哪、多大、能不能改、生效方式；点开抽屉看原文并就地改。
      </Paragraph>
      <Card size="small">
        <Tabs
          items={[
            { key: 'ask', label: '问数线', children: <ChainLane lane="ask" /> },
            { key: 'work', label: '干活线', children: <ChainLane lane="office" /> },
          ]}
        />
      </Card>
    </Space>
  )
}
