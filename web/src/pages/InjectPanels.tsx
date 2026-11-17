import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
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
import { EditOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { injectApi, promptsApi } from '../api'
import { http } from '../api/client'

const { Text, Paragraph } = Typography

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

      <Card size="small" type="inner" title="这页怎么读">
        <Paragraph type="secondary" style={{ margin: 0, fontSize: 13 }}>
          顺序就是代码里的装配顺序。可改的是「库 / 全局 / 个人」那几段；「代码兜底」是库里空着才用的默认值，
          「自动生成」是每轮现算的。改完保存，字数与占比马上重算。
        </Paragraph>
      </Card>

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

function AskLane() {
  const [segs, setSegs] = useState<any[]>([])
  const [counts, setCounts] = useState<any>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    http
      .get('/prompts/impact')
      .then((r: any) => {
        setSegs(r.data.segments ?? [])
        setCounts(r.data.counts ?? {})
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
        message={`一次问数回答被 ${counts.segments ?? 0} 段、${counts.items ?? 0} 类输入影响。答错时按下面顺序往上找。`}
      />

      {segs.map((s: any) => (
        <Card key={s.key} size="small" type="inner" title={s.name}>
          <Table
            size="small"
            rowKey={(it: any) => it.name}
            pagination={false}
            dataSource={s.items}
            columns={[
              { title: '输入', render: (_: any, it: any) => <Text strong>{it.name}</Text> },
              {
                title: '存在哪',
                width: 220,
                render: (_: any, it: any) => <Text code>{it.where}</Text>,
              },
              { title: '大小', dataIndex: 'size', width: 130 },
              {
                title: '能改吗',
                width: 110,
                render: (_: any, it: any) =>
                  it.editable ? <Tag color="green">页面上可改</Tag> : <Tag>只读</Tag>,
              },
              { title: '改完多久生效', dataIndex: 'takes_effect', width: 120 },
              {
                title: '它在决定什么',
                render: (_: any, it: any) => <Text type="secondary">{it.note}</Text>,
              },
            ]}
          />
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
