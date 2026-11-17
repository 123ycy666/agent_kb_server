import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Drawer,
  Empty,
  Input,
  List,
  Popconfirm,
  Row,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  DownloadOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { chatHistoryApi, injectApi, presetApi } from '../api'
import type { ChatSession, PresetQuestion } from '../api'
import { agentHttp } from '../api/client'
import { FourPack } from '../components/SelfCheckPanel'

const { Title, Text, Paragraph } = Typography

type Step = {
  seq?: number
  kind?: string
  tool?: string
  round?: number
  ms?: number
  args?: any
  result?: any
}

type Artifact = {
  kind?: string
  name?: string
  bytes?: number
  file_id?: string
  url?: string
  self_check?: string
}

type Msg = {
  role: 'user' | 'assistant'
  text: string
  result?: any
  qaId?: number
  steps?: Step[]
  four?: any
}

function stepLabel(s: Step) {
  const bits = [`#${s.seq ?? '—'}`, s.tool || s.kind || 'step']
  if (s.round) bits.push(`第 ${s.round} 轮`)
  if (s.ms != null) bits.push(`${s.ms}ms`)
  return bits.join(' · ')
}

/** 答案下那一行：依据 · 自检 · 数字来源。数据不全时给「—」，不猜。 */
function summarize(m: Msg) {
  const f = m.four ?? m.result ?? {}
  const spec = f?.spec_used?.name || f?.spec_used?.title || ''
  const verdicts: any[] = f?.self_check?.verdicts ?? []
  const passed = verdicts.filter((v: any) => v?.pass !== false && v?.ok !== false).length
  const nums = Array.isArray(f?.number_sources) ? f.number_sources.length : 0
  const bits = [
    spec ? `依据《${spec}》` : '未用说明书',
    verdicts.length ? `自检 ${passed}/${verdicts.length}` : '未自检',
    nums ? `${nums} 个数字来源` : '无数字来源',
  ]
  return bits.join(' · ')
}

export default function AgentChat() {
  const [sessionId, setSessionId] = useState(() => {
    try {
      return localStorage.getItem('secretary_session') || ''
    } catch {
      return ''
    }
  })
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionLoading, setSessionLoading] = useState(false)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [fileIds, setFileIds] = useState<string[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [sending, setSending] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [taskId, setTaskId] = useState('')
  const [presets, setPresets] = useState<PresetQuestion[]>([])
  const [downloading, setDownloading] = useState('')
  const [selIdx, setSelIdx] = useState<number | null>(null)
  const [drawerTab, setDrawerTab] = useState('steps')
  const stoppedRef = useRef(false)

  const loadSessions = () =>
    chatHistoryApi
      .sessions({ limit: 30 })
      .then((r) => setSessions(r.sessions ?? []))
      .catch(() => undefined)

  useEffect(() => {
    presetApi
      .list()
      .then((r) => setPresets((r.items ?? []).slice(0, 6)))
      .catch(() => undefined)
    void loadSessions()
    return () => {
      stoppedRef.current = true
    }
  }, [])

  const attachFour = (qaId: number) =>
    injectApi
      .report({ qa_id: qaId })
      .then((x: any) =>
        setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? { ...m, four: x } : m))),
      )
      .catch(() => undefined)

  const openSession = async (sid: string) => {
    setSessionLoading(true)
    try {
      const r: any = await chatHistoryApi.history(sid, true)
      const mapped: Msg[] = (r.messages ?? []).map((m: any) => ({
        role: (m.direction === 1 ? 'user' : 'assistant') as 'user' | 'assistant',
        text: m.content || '',
        qaId: m.qa_id || undefined,
        steps: Array.isArray(m.steps)
          ? m.steps.map((s: any) => ({
              seq: s.step_no,
              kind: s.step_kind,
              tool: s.tool_name,
              round: s.round_no,
              ms: s.cost_ms,
              args: s.input_text,
              result: {
                sql: s.sql_text,
                row_count: s.result_rows,
                error: s.error_msg,
                output: s.output_text,
              },
            }))
          : [],
      }))
      setMessages(mapped)
      setSessionId(sid)
      try {
        localStorage.setItem('secretary_session', sid)
      } catch {
        /* 忽略 */
      }
    } catch (e) {
      message.error((e as Error).message || '读取会话失败')
    } finally {
      setSessionLoading(false)
    }
  }

  const removeSession = async (sid: string) => {
    try {
      await chatHistoryApi.remove(sid)
      message.success('已删除这场会话')
      if (sid === sessionId) {
        setMessages([])
        setSessionId('')
      }
      await loadSessions()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const poll = (tid: string) => {
    const tick = async () => {
      if (stoppedRef.current) return
      try {
        const p: any = await agentHttp.get('/api/ask/progress', { params: { task_id: tid } })
        setSteps(p.steps ?? [])
        if (p.session_id) {
          setSessionId(p.session_id)
          try {
            localStorage.setItem('secretary_session', p.session_id)
          } catch {
            /* 隐私模式下忽略 */
          }
        }
        if (p.done) {
          const r = p.result ?? {}
          const snapshot: Step[] = p.steps ?? []
          setSending(false)
          setStopping(false)
          setTaskId('')
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              text: r.answer || '（本轮没有回答）',
              result: r,
              qaId: r.qa_id,
              steps: snapshot,
            },
          ])
          if (r.qa_id) void attachFour(r.qa_id)
          void loadSessions()
          return
        }
        window.setTimeout(tick, 900)
      } catch (e) {
        stoppedRef.current = true
        setSending(false)
        setStopping(false)
        message.error((e as Error).message || '运行进度读取失败')
      }
    }
    void tick()
  }

  const send = async () => {
    const q = input.trim()
    if (!q || sending) return
    setInput('')
    setSteps([])
    setStopping(false)
    setSending(true)
    setMessages((prev) => [...prev, { role: 'user', text: q }])
    try {
      const res: any = await agentHttp.post('/api/ask', {
        question: q,
        session_id: sessionId || undefined,
        stream: true,
        channel: 'web',
        history_turns: 5,
        file_ids: fileIds.length ? fileIds : undefined,
      })
      setTaskId(res.task_id || '')
      setSessionId(res.session_id || sessionId)
      if (res.session_id) {
        try {
          localStorage.setItem('secretary_session', res.session_id)
        } catch {
          /* 忽略 */
        }
      }
      if (res.task_id) poll(res.task_id)
      else {
        setSending(false)
        message.warning('后端没有返回运行任务')
      }
    } catch (e) {
      stoppedRef.current = false
      setSending(false)
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: `提问失败：${(e as Error).message}` },
      ])
    }
  }

  const stop = async () => {
    if (!taskId) return
    setStopping(true)
    try {
      await agentHttp.post('/api/ask/stop', null, { params: { task_id: taskId } })
      message.info('已请求停止，模型下一轮会收手')
    } catch (e) {
      setStopping(false)
      message.error((e as Error).message)
    }
  }

  const download = async (a: Artifact) => {
    if (!a.url) return
    setDownloading(a.url)
    try {
      const blob = (await agentHttp.get(a.url, { responseType: 'blob' })) as unknown as Blob
      const url = URL.createObjectURL(blob)
      const el = document.createElement('a')
      el.href = url
      el.download = a.name || '产物'
      document.body.appendChild(el)
      el.click()
      el.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setDownloading('')
    }
  }

  const revise = (a: Artifact) => {
    if (a.file_id) setFileIds([a.file_id])
    setInput((prev) => prev || `再改一版：基于《${a.name || '这份产物'}》，保持原有结构与版式，调整内容后重新出文件。`)
    setSelIdx(null)
  }

  const fetchFour = (i: number) => {
    const m = messages[i]
    if (!m || m.four || !m.qaId) return
    injectApi
      .report({ qa_id: m.qaId })
      .then((x: any) => setMessages((prev) => prev.map((mm, j) => (j === i ? { ...mm, four: x } : mm))))
      .catch(() => undefined)
  }

  const selectTurn = (i: number) => {
    setSelIdx(i)
    setDrawerTab('steps')
    fetchFour(i)
  }

  const newSession = () => {
    setMessages([])
    setSteps([])
    setSessionId('')
    try {
      localStorage.removeItem('secretary_session')
    } catch {
      /* 忽略 */
    }
  }

  const lastIsUser = messages.length > 0 && messages[messages.length - 1].role === 'user'
  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant') return i
    }
    return null
  })()
  const activeIdx = selIdx != null && messages[selIdx]?.role === 'assistant' ? selIdx : lastAssistantIdx
  const opened = activeIdx != null ? messages[activeIdx] : null
  const openedArtifacts: Artifact[] = opened?.result?.artifacts ?? []
  useEffect(() => {
    if (activeIdx != null) fetchFour(activeIdx)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdx, messages.length])

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Row justify="space-between" align="middle">
        <Col>
          <Space align="baseline" wrap>
            <Title level={4} style={{ margin: 0 }}>
              智能问答
            </Title>
            <Text type="secondary">左边历史会话 · 中间对话 · 右边直接显示这一轮的每一步</Text>
          </Space>
        </Col>
        <Col>
          <Space wrap>
            {sending ? <Tag color="processing">运行中</Tag> : <Tag color="success">就绪</Tag>}
            {sessionId ? (
              <Text type="secondary" className="kb-mono" style={{ fontSize: 11 }}>
                会话 {sessionId.slice(-8)}
              </Text>
            ) : null}
            <Button size="small" icon={<PlusOutlined />} onClick={newSession}>
              新会话
            </Button>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => void loadSessions()}>
              刷新会话
            </Button>
          </Space>
        </Col>
      </Row>

      <Row gutter={12} align="stretch">
        <Col span={5}>
          <Card
            size="small"
            title={`历史会话（${sessions.length}）`}
            styles={{ body: { padding: 0, height: 640, overflowY: 'auto' } }}
          >
            {sessions.length === 0 ? (
              <Empty style={{ marginTop: 40 }} description="还没有会话" />
            ) : (
              <List
                size="small"
                dataSource={sessions}
                renderItem={(s) => (
                  <List.Item
                    style={{
                      cursor: 'pointer',
                      background: s.session_id === sessionId ? '#e6f4ff' : undefined,
                      padding: '8px 10px',
                    }}
                    onClick={() => void openSession(s.session_id)}
                    actions={[
                      <Popconfirm
                        key="del"
                        title="删除这场会话？"
                        onConfirm={(e) => {
                          e?.stopPropagation()
                          void removeSession(s.session_id)
                        }}
                        onCancel={(e) => e?.stopPropagation()}
                      >
                        <Button
                          size="small"
                          type="text"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Popconfirm>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Text style={{ fontSize: 13 }} ellipsis>
                          {s.title || s.session_id.slice(-8)}
                        </Text>
                      }
                      description={
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {s.last_msg_at ? String(s.last_msg_at).slice(5, 16) : '—'} · 问 {s.ask_count} 轮
                        </Text>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>

        <Col span={12}>
          <Card
            size="small"
            title="对话"
            loading={sessionLoading}
            extra={
              sending ? (
                <Button size="small" danger icon={<StopOutlined />} loading={stopping} onClick={() => void stop()}>
                  停止
                </Button>
              ) : null
            }
            styles={{ body: { padding: 0 } }}
          >
            <div style={{ height: 500, overflowY: 'auto', padding: 14 }}>
              {messages.length === 0 ? (
                <div style={{ padding: '18px 8px' }}>
                  <Space direction="vertical" size={10} style={{ width: '100%' }}>
                    <Space>
                      <RobotOutlined style={{ fontSize: 24, color: '#1677ff' }} />
                      <Text strong style={{ fontSize: 16 }}>
                        直接说人话就行
                      </Text>
                    </Space>
                    <Paragraph type="secondary" style={{ margin: 0, fontSize: 13 }}>
                      写通知 / 做台账 / 读文件 / 查数 / 改一版都可以。右边常驻「运行步骤」，不用点就能看到这一轮每一步到底做了什么。
                    </Paragraph>
                    <Space wrap size={[6, 6]}>
                      {presets.map((p) => (
                        <Tag
                          key={p.id}
                          color="blue"
                          style={{ cursor: 'pointer', whiteSpace: 'normal' }}
                          onClick={() => setInput(p.text)}
                        >
                          {p.text}
                        </Tag>
                      ))}
                    </Space>
                  </Space>
                </div>
              ) : (
                messages.map((m, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                      marginBottom: 12,
                    }}
                  >
                    <Space align="start" size={8} style={{ maxWidth: '88%' }}>
                      {m.role === 'assistant' ? (
                        <RobotOutlined style={{ color: '#1677ff', marginTop: 4 }} />
                      ) : null}
                      <div>
                        <div
                          style={{
                            background: m.role === 'user' ? '#1677ff' : '#f5f5f5',
                            color: m.role === 'user' ? '#fff' : 'rgba(0,0,0,.88)',
                            borderRadius: m.role === 'user' ? '10px 10px 2px 10px' : '10px 10px 10px 2px',
                            padding: '8px 12px',
                            whiteSpace: 'pre-wrap',
                            lineHeight: 1.7,
                            fontSize: 13,
                          }}
                        >
                          {m.text}
                        </div>
                        {m.role === 'assistant' ? (
                          <div
                            style={{ marginTop: 4, fontSize: 12, color: '#8c8c8c', cursor: 'pointer' }}
                            onClick={() => selectTurn(i)}
                          >
                            {summarize(m)}（右侧看这一步做了什么）
                          </div>
                        ) : null}
                      </div>
                      {m.role === 'user' ? <UserOutlined style={{ marginTop: 4 }} /> : null}
                    </Space>
                  </div>
                ))
              )}
              {sending ? (
                <div style={{ padding: '8px 4px' }}>
                  <Space>
                    <Spin size="small" />
                    <Text type="secondary">
                      模型在跑{steps.length ? `，已运行 ${steps.length} 步` : ''}…
                    </Text>
                  </Space>
                </div>
              ) : null}
            </div>

            <div style={{ borderTop: '1px solid #f0f0f0', padding: 12 }}>
              {fileIds.length > 0 ? (
                <Space wrap size={4} style={{ marginBottom: 6 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    随本轮带上：
                  </Text>
                  {fileIds.map((id) => (
                    <Tag key={id} closable onClose={() => setFileIds((xs) => xs.filter((x) => x !== id))}>
                      <FileTextOutlined /> {id}
                    </Tag>
                  ))}
                </Space>
              ) : null}
              <Input.TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="例：把这个文件改成给领导看的汇报材料，出一份 docx"
                autoSize={{ minRows: 2, maxRows: 4 }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void send()
                  }
                }}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Enter 发送，Shift+Enter 换行
                </Text>
                <Button type="primary" icon={<SendOutlined />} loading={sending} onClick={() => void send()}>
                  发送
                </Button>
              </div>
            </div>
          </Card>
        </Col>
        <Col span={7}>
          <Card
            size="small"
            title={opened ? `运行步骤${opened.steps?.length ? `（${opened.steps.length}）` : ''}` : '运行步骤'}
            styles={{ body: { padding: 8, height: 620, overflowY: 'auto' } }}
          >
            {!opened ? (
              <Empty style={{ marginTop: 80 }} description="问答一轮之后，这里直接显示每一步" />
            ) : (
              <Tabs
                size="small"
                activeKey={drawerTab}
                onChange={setDrawerTab}
                items={[
                  {
                    key: 'steps',
                    label: '步骤',
                    children:
                      !opened.steps || opened.steps.length === 0 ? (
                        <Empty description="这一轮没有拿到步骤明细（可能还没落库，稍后重开一次）" />
                      ) : (
                        <Collapse
                          size="small"
                          items={opened.steps.map((s, i) => ({
                            key: String(s.seq ?? i),
                            label: (
                              <Space wrap size={4}>
                                <Text style={{ fontSize: 12 }}>{stepLabel(s)}</Text>
                                {s.kind === 'tool' ? <Tag>工具</Tag> : <Tag color="blue">{s.kind || '步骤'}</Tag>}
                              </Space>
                            ),
                            children: (
                              <pre
                                style={{
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-all',
                                  fontSize: 11,
                                  margin: 0,
                                  maxHeight: 320,
                                  overflow: 'auto',
                                }}
                              >
                                {JSON.stringify(s.result ?? s.args ?? s, null, 2).slice(0, 6000)}
                              </pre>
                            ),
                          }))}
                        />
                      ),
                  },
                  {
                    key: 'four',
                    label: '四件套',
                    children:
                      opened.four || opened.result ? (
                        <FourPack
                          data={{
                            ...(opened.result ?? {}),
                            ...(opened.four ?? {}),
                          }}
                          compact
                        />
                      ) : (
                        <Empty description="四件套还没落库（qa_id 为空或写入失败）" />
                      ),
                  },
                  {
                    key: 'artifacts',
                    label: `产物${openedArtifacts.length ? `（${openedArtifacts.length}）` : ''}`,
                    children:
                      openedArtifacts.length === 0 ? (
                        <Empty description="本轮没有产物" />
                      ) : (
                        <Space direction="vertical" size={8} style={{ width: '100%' }}>
                          {openedArtifacts.map((a, i) => (
                            <Card key={`${a.url}-${i}`} size="small">
                              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                                <Space wrap size={6}>
                                  <FileTextOutlined />
                                  <Text strong>{a.name}</Text>
                                  <Tag>{a.kind || '文件'}</Tag>
                                  {a.self_check ? <Tag color="green">{a.self_check}</Tag> : null}
                                </Space>
                                <Space>
                                  <Button
                                    size="small"
                                    type="primary"
                                    icon={<DownloadOutlined />}
                                    loading={downloading === a.url}
                                    onClick={() => void download(a)}
                                  >
                                    直接下载
                                  </Button>
                                  <Button size="small" onClick={() => revise(a)}>
                                    再改一版
                                  </Button>
                                </Space>
                              </Space>
                            </Card>
                          ))}
                        </Space>
                      ),
                  },
                ]}
              />
            )}
          </Card>
        </Col>
      </Row>


      {lastIsUser && !sending ? (
        <Alert type="warning" showIcon message="上一条提问没有拿到回答，可以重发一次或到「记录」里看运行步骤。" />
      ) : null}
    </Space>
  )
}
