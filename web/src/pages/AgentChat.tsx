import { useEffect, useRef, useState } from 'react'
import { Button, Empty, Popconfirm, Tooltip, Typography, message } from 'antd'
import { DeleteOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons'
import { agentHttp } from '../api/client'
import { useAgent } from '../components/agent/useAgent'
import MessageView, { JumpToBottom } from '../components/agent/MessageView'
import Composer from '../components/agent/Composer'
import TraceRail from '../components/agent/TraceRail'
import type { FilePart } from '../components/agent/types'

type Session = { id: string; title: string; updated_at?: string }

export default function AgentChat() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [active, setActive] = useState('')
  const { msgs, running, runId, send, stop, hydrate, setRunId } = useAgent()
  const scrollRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  const [showJump, setShowJump] = useState(false)

  const loadSessions = async () => {
    try {
      const r: any = await agentHttp.get('/api/agent/sessions')
      setSessions(r.items || [])
    } catch (e: any) {
      message.error(e?.response?.data?.detail?.message || '会话列表拉取失败')
    }
  }

  useEffect(() => { void loadSessions() }, [])

  useEffect(() => {
    const el = scrollRef.current
    if (el && followRef.current) el.scrollTop = el.scrollHeight
  }, [msgs])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    followRef.current = atBottom
    setShowJump(!atBottom)
  }

  const toBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
    followRef.current = true
    setShowJump(false)
  }

  const openSession = async (sid: string) => {
    if (sid === active) return
    setActive(sid)
    setRunId('')
    await hydrate(sid)
    try {
      const r: any = await agentHttp.get(`/api/agent/runs?session_id=${sid}`)
      const first = (r.items || [])[0]
      if (first?.id) setRunId(String(first.id))
    } catch { /* 没有运行记录 */ }
    toBottom()
  }

  const newSession = () => {
    setActive('')
    setRunId('')
    void hydrate('')
  }

  const submit = (text: string, files: FilePart[]) => {
    followRef.current = true
    setShowJump(false)
    void send({
      text,
      files,
      fileIds: files.map((f) => f.fileId),
      sessionId: active || undefined,
      onSession: (sid) => { setActive(sid); void loadSessions() },
    })
  }

  const del = async () => {
    if (!active) return
    try {
      await agentHttp.delete(`/api/agent/sessions/${active}`)
      newSession()
      void loadSessions()
    } catch {
      message.error('删不掉，稍后再试')
    }
  }

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 88px)', gap: 12 }}>
      <div style={{ width: 240, flex: '0 0 240px', background: '#fff', borderRadius: 12, padding: 12,
        display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={newSession} block>新建会话</Button>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {sessions.length === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>还没有会话</Typography.Text>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              onClick={() => void openSession(s.id)}
              style={{
                padding: '7px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 13,
                background: s.id === active ? '#e6f4ff' : 'transparent',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}
            >
              {s.title || '未命名会话'}
            </div>
          ))}
        </div>
        {active && (
          <Popconfirm title="删除这个会话？" onConfirm={() => void del()}>
            <Button danger size="small" icon={<DeleteOutlined />} block>删除当前会话</Button>
          </Popconfirm>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0, background: '#fff', borderRadius: 12, display: 'flex',
        flexDirection: 'column', position: 'relative' }}>
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #f0f0f0', display: 'flex',
          alignItems: 'center', gap: 8 }}>
          <RobotOutlined style={{ color: '#0f766e' }} />
          <Typography.Text strong style={{ fontSize: 16 }}>智能体</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            一句话交代，它自己决定读什么、查什么、出什么
          </Typography.Text>
        </div>

        <div ref={scrollRef} onScroll={onScroll} style={{ flex: 1, overflow: 'auto', padding: '8px 16px' }}>
          {msgs.length === 0 ? (
            <div style={{ marginTop: 64, textAlign: 'center' }}>
              <Empty description="今天要办什么？" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              <Tooltip title="可以闲聊，也可以要一份文件">
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  写通知、做汇报、查数、读文件、校对、出表格；缺的信息它会先问
                </Typography.Text>
              </Tooltip>
            </div>
          ) : (
            msgs.map((m) => <MessageView key={m.key} m={m} />)
          )}
        </div>

        <JumpToBottom show={showJump} onClick={toBottom} />
        <Composer running={running} onSend={submit} onStop={() => void stop()} />
      </div>

      <TraceRail runId={runId} />
    </div>
  )
}
