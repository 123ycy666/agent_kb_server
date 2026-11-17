import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Collapse,
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

const { Text, Paragraph } = Typography

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

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
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
        dataSource={parts}
        columns={[
          {
            title: '段',
            width: 240,
            render: (_, p: Part, i) => (
              <Space size={4}>
                <Text type="secondary">{`①②③④⑤⑥⑦⑧`[i] ?? i + 1}</Text>
                <Text strong>{p.label}</Text>
              </Space>
            ),
          },
          { title: '来源', width: 110, render: (_, p: Part) => <SourceTag source={p.source} /> },
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
            render: (_, p: Part) => (
              <Space>
                <Button size="small" icon={<EyeOutlined />} onClick={() => openEdit(p)}>
                  原文
                </Button>
                {EDITABLE[p.name] ? (
                  <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(p)}>
                    改
                  </Button>
                ) : null}
              </Space>
            ),
          },
        ]}
      />

      <Drawer
        open={openKey != null}
        onClose={() => setOpenKey(null)}
        width={720}
        title={parts.find((p) => p.name === openKey)?.label || ''}
        extra={
          EDITABLE[openKey ?? ''] ? (
            <Button type="primary" loading={saving} onClick={() => void save()}>
              保存到{scope === 'user' ? '我的' : '全局'}
            </Button>
          ) : null
        }
      >
        {EDITABLE[openKey ?? ''] ? (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            {EDITABLE[openKey ?? ''].map((k) => (
              <div key={k}>
                <Text strong>{k}</Text>
                <Input.TextArea
                  autoSize={{ minRows: 6, maxRows: 20 }}
                  value={bodies[k] ?? ''}
                  onChange={(e) => setBodies((b) => ({ ...b, [k]: e.target.value }))}
                />
              </div>
            ))}
          </Space>
        ) : (
          <Space direction="vertical" size={8}>
            <Text>{CODE_LOCATION[openKey ?? ''] || '这一段由代码或上一轮会话生成，不在库里。'}</Text>
            <Text type="secondary">要改它只能改代码；改代码不在这页做。</Text>
          </Space>
        )}
      </Drawer>
    </Space>
  )
}

/* ───────────────────────── 问数线（工作台） ───────────────────────── */

function MaterialEditor({ slot }: { slot: string }) {
  const [secs, setSecs] = useState<any[]>([])
  const [bodies, setBodies] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState<number | null>(null)

  const load = () =>
    promptsApi
      .material(slot)
      .then((r: any) => {
        const list = r.sections ?? r.items ?? []
        setSecs(list)
        const next: Record<number, string> = {}
        list.forEach((s: any) => (next[s.id] = s.body ?? ''))
        setBodies(next)
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
  }, [slot])

  if (!secs.length) return <Empty description="这一槽没有小节（内容为空）" />

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
    <Collapse
      size="small"
      items={secs.map((s: any) => ({
        key: String(s.id),
        label: (
          <Space>
            <Text strong>{s.section}</Text>
            <Text type="secondary">{String(s.body ?? '').length} 字</Text>
            {s.pinned ? <Tag color="blue">必带</Tag> : <Tag>按需</Tag>}
          </Space>
        ),
        children: (
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            <Input.TextArea
              autoSize={{ minRows: 8, maxRows: 30 }}
              value={bodies[s.id] ?? ''}
              onChange={(e) => setBodies((b) => ({ ...b, [s.id]: e.target.value }))}
            />
            <Space>
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
          </Space>
        ),
      }))}
    />
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
        setBody(v?.body ?? v?.text ?? '')
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
        autoSize={{ minRows: 8, maxRows: 30 }}
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

function ReadOnlyPeek({ endpoint, name }: { endpoint: string; name: string }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<any>(null)

  const show = () => {
    setOpen(true)
    if (data) return
    http
      .get(endpoint)
      .then((r: any) => setData(r))
      .catch((e) => message.error((e as Error).message))
  }

  return (
    <>
      <Button size="small" icon={<EyeOutlined />} onClick={show}>
        看内容
      </Button>
      <Drawer open={open} onClose={() => setOpen(false)} width={760} title={name}>
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>
          {data ? JSON.stringify(data, null, 2).slice(0, 200000) : '读取中…'}
        </pre>
      </Drawer>
    </>
  )
}

function AskLane() {
  const [segs, setSegs] = useState<any[]>([])
  const [counts, setCounts] = useState<any>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    promptsApi
      .impact()
      .then((r: any) => {
        setSegs(r.segments ?? [])
        setCounts(r.counts ?? {})
      })
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Empty description="读取中…" />

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message={`一次问数回答被 ${counts.segments ?? 0} 段、${counts.items ?? 0} 项输入影响。能就地改的直接改，改完下一轮生效。`}
      />

      {segs.map((s: any) => (
        <Card key={s.key} size="small" type="inner" title={s.name}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {(s.items ?? []).map((it: any) => (
              <div key={it.name} style={{ borderTop: '1px dashed #eee', paddingTop: 8 }}>
                <Space wrap size={6} style={{ marginBottom: 6 }}>
                  <Text strong>{it.name}</Text>
                  <Text code>{it.where}</Text>
                  <Text type="secondary">{it.size}</Text>
                  {it.editable ? <Tag color="green">可改</Tag> : <Tag>只读</Tag>}
                  <Text type="secondary">改完：{it.takes_effect}</Text>
                  <Text type="secondary">{it.note}</Text>
                </Space>
                {String(it.editor || '').startsWith('material:') ? (
                  <MaterialEditor slot={String(it.editor).split(':')[1]} />
                ) : String(it.editor || '').startsWith('text:') ? (
                  <TextKeyEditor keyName={String(it.editor).split(':')[1]} />
                ) : it.endpoint ? (
                  <ReadOnlyPeek endpoint={it.endpoint} name={it.name} />
                ) : (
                  <Text type="secondary">这个只能在代码里看</Text>
                )}
              </div>
            ))}
          </Space>
        </Card>
      ))}
    </Space>
  )
}

export default function InjectPanels() {
  return (
    <Card title="提示词（这一轮实际发出去的是什么）" size="small">
      <Tabs
        items={[
          { key: 'ask', label: '问数线', children: <AskLane /> },
          { key: 'work', label: '干活线', children: <WorkLane /> },
        ]}
      />
    </Card>
  )
}
