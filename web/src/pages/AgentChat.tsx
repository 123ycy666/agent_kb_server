import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button, Empty, Input, Popconfirm, Tabs, Tag, Tooltip, Typography, message } from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons'
import Page from '../components/ui/Page'
import { useTab } from '../components/ui/useTab'
import { agentHttp } from '../api/client'
import { useAgent } from '../components/agent/useAgent'
import MessageView, { JumpToBottom } from '../components/agent/MessageView'
import Composer from '../components/agent/Composer'
import TraceRail from '../components/agent/TraceRail'
import type { FilePart } from '../components/agent/types'
import ChatHistory from './ChatHistory'

type Session = { id: string; title: string; updated_at?: string }

function ChatPane() {
  const [sp, setSp] = useSearchParams()
  const [sessions, setSessions] = useState<Session[]>([])
  const [active, setActive] = useState('')
  const [q, setQ] = useState('')
  const [model, setModel] = useState('')
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

  useEffect(() => {
    void loadSessions()
    agentHttp
      .get('/api/agent/config')
      .then((r: any) => setModel(r?.model?.name || ''))
      .catch(() => setModel(''))
  }, [])

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
    } catch {
      /* 没有运行记录 */
    }
    toBottom()
  }

  /** 从概览页点一行进来：`/chat?session=<id>` */
  useEffect(() => {
    const sid = sp.get('session')
    if (!sid) return
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev)
        n.delete('session')
        return n
      },
      { replace: true }
    )
    void openSession(sid)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
      onSession: (sid) => {
        setActive(sid)
        void loadSessions()
      },
    })
  }

  const lastUser = [...msgs].reverse().find((m) => m.role === 'user')

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

  const shown = useMemo(() => {
    const k = q.trim().toLowerCase()
    return k ? sessions.filter((s) => (s.title || '').toLowerCase().includes(k)) : sessions
  }, [sessions, q])

  return (
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
            <Tooltip key={s.id} title={s.title || '未命名会话'} placement="right">
              <div
                onClick={() => void openSession(s.id)}
                style={{
                  padding: '7px 10px',
                  borderRadius: 6,
                  cursor: 'pointer',
                  fontSize: 13,
                  background: s.id === active ? '#e6f4ff' : 'transparent',
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
          position: 'relative',
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
          <RobotOutlined style={{ color: '#1677ff' }} />
          <Typography.Text strong style={{ fontSize: 15 }}>
            智能体
          </Typography.Text>
          {model ? <Tag style={{ marginInlineStart: 4, color: 'rgba(0,0,0,.45)' }}>{model}</Tag> : null}
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          style={{ flex: 1, overflow: 'auto', padding: '8px 16px' }}
        >
          {msgs.length === 0 ? (
            <div style={{ marginTop: 64, textAlign: 'center' }}>
              <Empty description="今天要办什么？" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            </div>
          ) : (
            msgs.map((m) => <MessageView key={m.key} m={m} />)
          )}
          {!running && msgs.length > 0 && lastUser && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              style={{ marginBottom: 12 }}
              onClick={() => submit(lastUser.text || '', lastUser.files || [])}
            >
              重新生成
            </Button>
          )}
        </div>

        <JumpToBottom show={showJump} onClick={toBottom} />
        <Composer running={running} onSend={submit} onStop={() => void stop()} />
      </div>

      <TraceRail runId={runId} />
    </div>
  )
}

export default function AgentChat() {
  const [tab, setTab] = useTab('chat', ['chat', 'records'])
  return (
    <Page title="对话">
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: 'chat', label: '对话', children: <ChatPane /> },
          { key: 'records', label: '记录', children: <ChatHistory /> },
        ]}
      />
    </Page>
  )
}
