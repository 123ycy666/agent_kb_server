import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Avatar, Button, Empty, Flex, Popconfirm, Space, Tag, Tooltip, Typography, Upload, message,
} from 'antd'
import { Bubble, Conversations, Sender, ThoughtChain, Welcome } from '@ant-design/x'
import {
  DeleteOutlined, DownloadOutlined, PaperClipOutlined, PlusOutlined, RobotOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { agentHttp, getToken } from '../api/client'

type Session = { id: string; title: string; updated_at?: string }
type BubbleItem = { key: string; role: 'user' | 'ai'; content: string; loading?: boolean }
type Step = { key: string; name: string; args: string; brief: string; status: 'pending' | 'success' | 'error'; ms?: number }
type Artifact = { name: string; url: string; file_id?: string; kind?: string }
type FileRef = { file_id: string; name: string }

const TOOL_LABEL: Record<string, string> = {
  read_file: '读文件', make_file: '出文件', read_skill: '读技能', list_skills: '技能目录',
  search_kb: '查资料', query_data: '查数', db_schema: '看库结构', remember: '记一条', recall: '找记忆',
}

function textOf(p: any): string {
  if (!p) return ''
  if (typeof p === 'string') return p
  if (typeof p.text === 'string') return p.text
  const c = p.content
  if (typeof c === 'string') return c
  if (Array.isArray(c)) return c.map((x: any) => x?.text || '').join('')
  return ''
}

function itemsToView(items: any[]) {
  const bubbles: BubbleItem[] = []
  const steps: Step[] = []
  const arts: Artifact[] = []
  for (const it of items) {
    const p = it.payload || {}
    const t = it.type
    const role = p.role
    if (t === 'user' || (t === 'item' && role === 'user')) {
      bubbles.push({ key: 'i' + it.id, role: 'user', content: textOf(p) })
    } else if (t === 'assistant' || (t === 'item' && role === 'assistant')) {
      bubbles.push({ key: 'i' + it.id, role: 'ai', content: textOf(p) })
    } else if (t === 'tool_call') {
      steps.push({
        key: 't' + it.id, name: TOOL_LABEL[p.name] || p.name || '工具',
        args: String(p.arguments || ''), brief: '', status: 'success',
      })
    } else if (t === 'tool_result') {
      try {
        const raw = typeof p.output === 'string' ? p.output : ''
        const d = raw ? JSON.parse(raw) : null
        if (d && d.url) arts.push({ name: d.name, url: d.url, file_id: d.file_id, kind: d.kind })
      } catch { /* 不是文件结果 */ }
    }
  }
  return { bubbles, steps, arts }
}

export default function AgentChat() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [active, setActive] = useState<string>('')
  const [bubbles, setBubbles] = useState<BubbleItem[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [files, setFiles] = useState<FileRef[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [model, setModel] = useState('')
  const runRef = useRef<string>('')
  const sessionRef = useRef<string>('')

  const headers = () => ({ Authorization: `Bearer ${getToken() || ''}` })

  const loadSessions = async () => {
    try {
      const r: any = await agentHttp.get('/api/agent/sessions')
      setSessions(r.items || [])
    } catch (e: any) {
      message.error(e?.response?.data?.detail?.message || '会话列表拉取失败')
    }
  }

  useEffect(() => { loadSessions() }, [])

  const loadSession = async (sid: string) => {
    if (!sid || sid === active) return
    try {
      const r: any = await agentHttp.get(`/api/agent/sessions/${sid}/items`)
      const v = itemsToView(r.items || [])
      setBubbles(v.bubbles)
      setSteps(v.steps)
      setArtifacts(v.arts)
      setActive(sid)
      sessionRef.current = sid
    } catch {
      message.error('这个会话读不出来')
    }
  }

  const newSession = () => {
    setActive('')
    sessionRef.current = ''
    setBubbles([])
    setSteps([])
    setArtifacts([])
    setFiles([])
  }

  const stop = async () => {
    if (!runRef.current) return
    try { await agentHttp.post('/api/agent/stop', { run_id: runRef.current }) } catch { /* 已结束 */ }
  }

  const patchBubble = (key: string, fn: (b: BubbleItem) => BubbleItem) => {
    setBubbles((bs) => bs.map((b) => (b.key === key ? fn(b) : b)))
  }

  const runSSE = async (text: string) => {
    const akey = 'a' + Date.now()
    const ukey = 'u' + Date.now()
    setBubbles((bs) => [...bs, { key: ukey, role: 'user', content: text },
      { key: akey, role: 'ai', content: '', loading: true }])
    setSteps([])
    setArtifacts([])
    setLoading(true)
    try {
      const res = await fetch('/api/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers() },
        body: JSON.stringify({ text, session_id: sessionRef.current || undefined,
          file_ids: files.map((f) => f.file_id) }),
      })
      if (!res.ok || !res.body) throw new Error('服务没有返回流（' + res.status + '）')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const chunks = buf.split('\n\n')
        buf = chunks.pop() || ''
        for (const chunk of chunks) {
          const line = chunk.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          let ev: any
          try { ev = JSON.parse(line.slice(6)) } catch { continue }
          if (ev.type === 'run') {
            runRef.current = ev.run_id
            setModel(ev.model || '')
            if (ev.session_id) { sessionRef.current = ev.session_id; setActive(ev.session_id) }
          } else if (ev.type === 'delta') {
            patchBubble(akey, (b) => ({ ...b, content: b.content + (ev.text || ''), loading: false }))
          } else if (ev.type === 'tool_call') {
            setSteps((s) => [...s, {
              key: `s${Date.now()}${Math.random()}`, name: TOOL_LABEL[ev.name] || ev.name,
              args: ev.args || '', brief: '', status: 'pending',
            }])
          } else if (ev.type === 'tool_result') {
            setSteps((s) => {
              const idx = [...s].reverse().findIndex((x) => x.status === 'pending')
              if (idx < 0) return s
              const real = s.length - 1 - idx
              const next = [...s]
              next[real] = { ...next[real], brief: ev.brief || '', ms: ev.ms, status: 'success' }
              return next
            })
          } else if (ev.type === 'artifact') {
            setArtifacts((a) => [...a, ev])
          } else if (ev.type === 'done') {
            if (ev.answer) patchBubble(akey, (b) => ({ ...b, content: ev.answer, loading: false }))
            else patchBubble(akey, (b) => ({ ...b, loading: false }))
          } else if (ev.type === 'error') {
            patchBubble(akey, (b) => ({ ...b, content: '出错了：' + ev.message, loading: false }))
          }
        }
      }
      setFiles([])
      loadSessions()
    } catch (e: any) {
      patchBubble(akey, (b) => ({ ...b, content: '没跑通：' + (e?.message || e), loading: false }))
    } finally {
      setLoading(false)
      runRef.current = ''
    }
  }

  const send = (text: string) => {
    const t = (text || '').trim()
    if (!t || loading) return
    setInput('')
    runSSE(t)
  }

  const uploadFile = async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    form.append('purpose', 'chat')
    try {
      const r: any = await agentHttp.post('/api/files', form)
      const d = r || {}
      setFiles((f) => [...f, { file_id: d.file_id, name: d.name || file.name }])
      message.success('已上传：' + (d.name || file.name))
    } catch (e: any) {
      message.error(e?.response?.data?.detail?.message || '上传失败')
    }
  }

  const convItems = useMemo(
    () => sessions.map((s) => ({ key: s.id, label: s.title || '未命名会话' })),
    [sessions],
  )

  const bubbleItems = bubbles.map((b) => ({
    key: b.key, role: b.role, content: b.content, loading: b.loading,
    avatar: b.role === 'user'
      ? <Avatar icon={<UserOutlined />} style={{ background: '#1677ff' }} />
      : <Avatar icon={<RobotOutlined />} style={{ background: '#0f766e' }} />,
  }))

  const chainItems = steps.map((s) => ({
    key: s.key, title: s.name, status: s.status,
    description: s.status === 'pending'
      ? '执行中…'
      : (s.brief || '').replace(/\s+/g, ' ').slice(0, 120) + (s.ms ? ` · ${s.ms}ms` : ''),
  }))

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 88px)', gap: 12 }}>
      <div style={{ width: 240, flex: '0 0 240px', background: '#fff', borderRadius: 12,
        padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={newSession} block>新建会话</Button>
        <div style={{ flex: 1, overflow: 'auto' }}>
          <Conversations items={convItems} activeKey={active || undefined}
            onActiveChange={loadSession} />
        </div>
        {active && (
          <Popconfirm title="删除这个会话？" onConfirm={async () => {
            await agentHttp.delete(`/api/agent/sessions/${active}`); newSession(); loadSessions()
          }}>
            <Button danger size="small" icon={<DeleteOutlined />} block>删除当前会话</Button>
          </Popconfirm>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0, background: '#fff', borderRadius: 12, display: 'flex',
        flexDirection: 'column' }}>
        <Flex align="center" justify="space-between" style={{ padding: '10px 16px', borderBottom: '1px solid #f0f0f0' }}>
          <Space>
            <Typography.Text strong style={{ fontSize: 16 }}>智能体</Typography.Text>
            {model && <Tag color="blue">{model}</Tag>}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            一句话交代，它自己决定读什么、查什么、出什么
          </Typography.Text>
        </Flex>

        <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
          {bubbles.length === 0 ? (
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 48 }}>
              <Welcome
                variant="borderless"
                icon={<RobotOutlined style={{ fontSize: 44, color: '#0f766e' }} />}
                title="今天要办什么？"
                description="可以写通知、做汇报、查数、读文件、校对、做 PPT；缺的信息它会先问。"
                style={{ maxWidth: 560 }}
              />
            </div>
          ) : (
            <Bubble.List
              items={bubbleItems}
              roles={{ user: { placement: 'end' }, ai: { placement: 'start' } }}
            />
          )}
        </div>

        <div style={{ padding: 12, borderTop: '1px solid #f0f0f0' }}>
          {files.length > 0 && (
            <Space wrap style={{ marginBottom: 8 }}>
              {files.map((f) => (
                <Tag key={f.file_id} closable onClose={() => setFiles((x) => x.filter((y) => y.file_id !== f.file_id))}>
                  {f.name}
                </Tag>
              ))}
            </Space>
          )}
          <Sender
            value={input}
            onChange={setInput}
            onSubmit={send}
            loading={loading}
            onCancel={stop}
            placeholder="说一件事，或者贴一段话…（Enter 发送，Shift+Enter 换行）"
            autoSize={{ minRows: 1, maxRows: 6 }}
            prefix={
              <Upload showUploadList={false} accept=".docx,.xlsx,.pptx,.pdf,.txt,.md,.csv"
                customRequest={(opt: any) => { void uploadFile(opt.file as File) }}>
                <Tooltip title="上传文件"><Button type="text" icon={<PaperClipOutlined />} /></Tooltip>
              </Upload>
            }
          />
        </div>
      </div>

      <div style={{ width: 320, flex: '0 0 320px', background: '#fff', borderRadius: 12,
        padding: 12, overflow: 'auto' }}>
        <Typography.Text strong>运行步骤</Typography.Text>
        <div style={{ marginTop: 12 }}>
          {steps.length === 0
            ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有步骤" />
            : <ThoughtChain items={chainItems} />}
        </div>
        {artifacts.length > 0 && (
          <>
            <Typography.Text strong style={{ display: 'block', marginTop: 16 }}>本轮产物</Typography.Text>
            <Space direction="vertical" style={{ marginTop: 8, width: '100%' }}>
              {artifacts.map((a, i) => (
                <a key={i} href={a.url} target="_blank" rel="noreferrer">
                  <Space><DownloadOutlined />{a.name}</Space>
                </a>
              ))}
            </Space>
          </>
        )}
      </div>
    </div>
  )
}
