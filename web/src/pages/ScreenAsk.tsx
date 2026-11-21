import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Collapse, Empty, Input, Popconfirm, Tag, Tooltip, Typography, message } from 'antd'
import { DeleteOutlined, PlusOutlined, SendOutlined, StopOutlined } from '@ant-design/icons'
import Page from '../components/ui/Page'
import { agentHttp } from '../api/client'

type Msg = { key: string; role: 'user' | 'assistant'; text: string }
type Step = {
  seq?: number
  kind?: string
  tool?: string
  content?: string
  args?: any
  result?: any
  ms?: number
  model_ms?: number
}

let seq = 0
const nk = (p: string) => p + ++seq
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

function stepBody(s: Step) {
  const out: string[] = []
  for (const [k, v] of Object.entries({ args: s.args, result: s.result, content: s.content })) {
    if (v == null || v === '') continue
    out.push(k + ':\n' + (typeof v === 'string' ? v : JSON.stringify(v, null, 2)))
  }
  return out.join('\n\n').slice(0, 3000)
}

// 历史回放的步骤来自 t_chat_trace_step（input_text/output_text 是入库时的 JSON 串）
const asJson = (v: any) => {
  if (typeof v !== 'string') return v
  try {
    return JSON.parse(v)
  } catch {
    return v
  }
}

const toStep = (s: any): Step => ({
  seq: s.step_no,
  kind: s.step_kind || '',
  tool: s.tool_name || '',
  args: asJson(s.input_text),
  result: asJson(s.output_text),
  ms: s.cost_ms ?? undefined,
  model_ms: s.model_ms ?? undefined,
})

export default function ScreenAsk() {
  const [sessions, setSessions] = useState<any[]>([])
  const [q, setQ] = useState('')
  const [active, setActive] = useState('')
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [text, setText] = useState('')
  const [running, setRunning] = useState(false)
  const taskRef = useRef('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const loadSessions = () =>
    agentHttp
      .get('/api/sessions')
      .then((r: any) => {
        const list = r.sessions || []
        setSessions(list)
        return list
      })
      .catch(() => undefined)

  useEffect(() => {
    // 刷新后没有选中态：直接打开最近一场，右栏才不会空着
    void loadSessions().then((list?: any[]) => {
      if (list?.length) void openSession(list[0].session_id)
    })
  }, [])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [msgs])

  const shown = useMemo(() => {
    const k = q.trim().toLowerCase()
    return k ? sessions.filter((s) => (s.title || '').toLowerCase().includes(k)) : sessions
  }, [sessions, q])

  const newSession = () => {
    setActive('')
    setMsgs([])
    setSteps([])
  }

  const openSession = async (sid: string) => {
    if (sid === active) return
    setActive(sid)
    setSteps([])
    try {
      // with_steps：刷新后 task_id 已随页面丢掉，右栏的步骤只能从历史里回放
      const h: any = await agentHttp.get('/api/history', {
        params: { session_id: sid, with_steps: true },
      })
      const rows = h.messages || []
      setMsgs(
        rows.map((m: any) => ({
          key: nk('h'),
          role: Number(m.sender_type) === 2 ? 'assistant' : 'user',
          text: String(m.content || ''),
        }))
      )
      const last = [...rows].reverse().find((m: any) => Array.isArray(m.steps) && m.steps.length)
      setSteps(last ? (last.steps as any[]).map(toStep) : [])
    } catch {
      message.error('历史拉取失败')
    }
  }

  const del = async () => {
    if (!active) return
    try {
      await agentHttp.post(`/api/sessions/${active}/delete`)
      setActive('')
      setMsgs([])
      setSteps([])
      void loadSessions()
    } catch {
      message.error('删不掉，稍后再试')
    }
  }

  const send = async () => {
    const question = text.trim()
    if (!question || running) return
    setText('')
    setSteps([])
    setMsgs((m) => [...m, { key: nk('u'), role: 'user', text: question }])
    setRunning(true)
    try {
      const r: any = await agentHttp.post('/api/ask/data', {
        question,
        session_id: active || undefined,
        stream: true,
      })
      taskRef.current = r.task_id
      if (r.session_id) setActive(r.session_id)
      for (;;) {
        await sleep(800)
        const p: any = await agentHttp.get('/api/ask/progress', { params: { task_id: r.task_id } })
        if (Array.isArray(p.steps) && p.steps.length) setSteps(p.steps)
        if (p.done) {
          const res: any = p.result || {}
          setMsgs((m) => [
            ...m,
            { key: nk('a'), role: 'assistant', text: String(res.answer || '（没有返回正文）') },
          ])
          if (Array.isArray(res.trace) && res.trace.length) setSteps(res.trace)
          void loadSessions()
          break
        }
      }
    } catch (e: any) {
      message.error(e?.message || '提问失败')
      setMsgs((m) => [...m, { key: nk('e'), role: 'assistant', text: '出错了：' + (e?.message || String(e)) }])
    } finally {
      taskRef.current = ''
      setRunning(false)
    }
  }

  const stop = () => {
    if (!taskRef.current) return
    void agentHttp
      .post('/api/ask/stop', null, { params: { task_id: taskRef.current } })
      .catch(() => undefined)
  }

  return (
    <Page title="大屏问数">
      <div style={{ display: 'flex', height: 'calc(100vh - 132px)', gap: 12 }}>
        <div
          style={{
            width: 240,
            flex: '0 0 240px',
            background: '#fff',
            borderRadius: 8,
            padding: 12,
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <Button type="primary" icon={<PlusOutlined />} onClick={newSession} block>
            新建会话
          </Button>
          <Input
            allowClear
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜会话"
            aria-label="搜会话"
            style={{ fontSize: 12 }}
          />
          <div style={{ flex: 1, overflow: 'auto' }}>
            {shown.length === 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {sessions.length === 0 ? '还没有会话' : '没有匹配的会话'}
              </Typography.Text>
            )}
            {shown.map((s) => (
              <Tooltip key={s.session_id} title={s.title || '未命名会话'} placement="right">
                <div
                  onClick={() => void openSession(s.session_id)}
                  style={{
                    padding: '7px 10px',
                    borderRadius: 6,
                    cursor: 'pointer',
                    fontSize: 13,
                    background: s.session_id === active ? '#e6f4ff' : 'transparent',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {s.title || '未命名会话'}
                </div>
              </Tooltip>
            ))}
          </div>
          {active && (
            <Popconfirm title="删除这个会话？" onConfirm={() => void del()} okText="删除" cancelText="不删">
              <Button danger size="small" icon={<DeleteOutlined />} block aria-label="删除当前会话">
                删除当前会话
              </Button>
            </Popconfirm>
          )}
        </div>

        <div
          style={{
            flex: 1,
            minWidth: 0,
            background: '#fff',
            borderRadius: 8,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <div
            style={{
              padding: '10px 16px',
              borderBottom: '1px solid #f0f0f0',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <Typography.Text strong style={{ fontSize: 15 }}>
              大屏问数
            </Typography.Text>
            <Tag style={{ marginInlineStart: 4, color: 'rgba(0,0,0,.45)' }}>只查数</Tag>
          </div>

          <div ref={scrollRef} style={{ flex: 1, overflow: 'auto', padding: '8px 16px' }}>
            {msgs.length === 0 ? (
              <div style={{ marginTop: 64, textAlign: 'center' }}>
                <Empty description="问一个能查数的问题" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              </div>
            ) : (
              msgs.map((m) => (
                <div
                  key={m.key}
                  style={{
                    display: 'flex',
                    justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                    marginBottom: 12,
                  }}
                >
                  <div
                    style={{
                      maxWidth: '82%',
                      padding: '9px 12px',
                      borderRadius: 8,
                      fontSize: 13,
                      lineHeight: 1.7,
                      whiteSpace: 'pre-wrap',
                      background: m.role === 'user' ? '#1677ff' : '#f5f7fa',
                      color: m.role === 'user' ? '#fff' : 'rgba(0,0,0,.88)',
                    }}
                  >
                    {m.text}
                  </div>
                </div>
              ))
            )}
            {running && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                查数中…
              </Typography.Text>
            )}
          </div>

          <div
            style={{
              borderTop: '1px solid #f0f0f0',
              padding: 12,
              display: 'flex',
              gap: 8,
              alignItems: 'flex-end',
            }}
          >
            <Input.TextArea
              value={text}
              onChange={(e) => setText(e.target.value)}
              autoSize={{ minRows: 1, maxRows: 4 }}
              placeholder="例：2026年10月全市有多少个供电所"
              aria-label="问题"
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
            />
            {running ? (
              <Button icon={<StopOutlined />} onClick={stop}>
                停止
              </Button>
            ) : (
              <Button type="primary" icon={<SendOutlined />} onClick={() => void send()}>
                发送
              </Button>
            )}
          </div>
        </div>

        <div
          style={{
            width: 340,
            flex: '0 0 340px',
            background: '#fff',
            borderRadius: 8,
            padding: 12,
            overflow: 'auto',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <Typography.Text strong>运行步骤</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {steps.length}
            </Typography.Text>
          </div>
          {steps.length === 0 ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              还没有运行记录
            </Typography.Text>
          ) : (
            <Collapse
              size="small"
              items={steps.map((s, i) => ({
                key: String(i),
                label: (
                  <span style={{ fontSize: 12 }}>
                    #{s.seq ?? i + 1} · {s.kind || 'step'}
                    {s.tool && s.tool !== s.kind ? ' · ' + s.tool : ''}
                    {s.model_ms ? <Tag style={{ marginInlineStart: 6 }}>{s.model_ms}ms</Tag> : null}
                    {s.ms ? <Tag style={{ marginInlineStart: 6 }}>{s.ms}ms</Tag> : null}
                  </span>
                ),
                children: (
                  <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                    {stepBody(s)}
                  </pre>
                ),
              }))}
            />
          )}
        </div>
      </div>
    </Page>
  )
}
