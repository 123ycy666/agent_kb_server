import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Input,
  Row,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DownloadOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { injectApi, presetApi } from '../api'
import type { PresetQuestion } from '../api'
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

type Msg = { role: 'user' | 'assistant'; text: string; result?: any }

function stepLabel(s: Step) {
  const bits = [`#${s.seq ?? '—'}`, s.tool || s.kind || 'step']
  if (s.round) bits.push(`第 ${s.round} 轮`)
  if (s.ms != null) bits.push(`${s.ms}ms`)
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
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [fileIds, setFileIds] = useState<string[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [result, setResult] = useState<any>(null)
  const [sending, setSending] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [taskId, setTaskId] = useState('')
  const [latestReport, setLatestReport] = useState<any>(null)
  const [presets, setPresets] = useState<PresetQuestion[]>([])
  const [downloading, setDownloading] = useState('')
  const [rightTab, setRightTab] = useState('four')
  const stoppedRef = useRef(false)

  useEffect(() => {
    injectApi
      .report({ limit: 1 })
      .then((r: any) => setLatestReport((r.items ?? [])[0] ?? null))
      .catch(() => undefined)
    presetApi
      .list()
      .then((r) => setPresets((r.items ?? []).slice(0, 6)))
      .catch(() => undefined)
    return () => {
      stoppedRef.current = true
    }
  }, [])

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
          setResult(r)
          setSending(false)
          setStopping(false)
          setTaskId('')
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', text: r.answer || '（本轮没有回答）', result: r },
          ])
          if (r.qa_id) {
            injectApi
              .report({ qa_id: r.qa_id })
              .then((x: any) => setLatestReport(x))
              .catch(() => undefined)
          }
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
    setResult(null)
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
  }

  const artifacts: Artifact[] = result?.artifacts ?? []
  const selfCheckVerdicts = steps
    .filter((s) => s.tool === 'self_check')
    .flatMap((s) => s.result?.verdicts ?? [])
  const fourData = result
    ? {
        ...result,
        self_check: { ...(result.self_check ?? {}), verdicts: selfCheckVerdicts },
      }
    : latestReport
  const lastIsUser = messages.length > 0 && messages[messages.length - 1].role === 'user'

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Row justify="space-between" align="middle">
        <Col>
          <Space align="baseline" wrap>
            <Title level={4} style={{ margin: 0 }}>
              智能问答
            </Title>
            <Text type="secondary">边跑边看 · 可停止 · 运行步骤可展开 · 四件套可见</Text>
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
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() =>
                injectApi
                  .report({ limit: 1 })
                  .then((r: any) => setLatestReport((r.items ?? [])[0] ?? null))
                  .catch(() => undefined)
              }
            >
              刷新最近记录
            </Button>
          </Space>
        </Col>
      </Row>

      <Row gutter={14} align="stretch">
        <Col span={14}>
          <Card
            size="small"
            title="对话"
            extra={
              sending ? (
                <Button size="small" danger icon={<StopOutlined />} loading={stopping} onClick={() => void stop()}>
                  停止
                </Button>
              ) : null
            }
            styles={{ body: { padding: 0 } }}
          >
            <div style={{ height: 394, overflowY: 'auto', padding: 14 }}>
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
                      写通知 / 做台账 / 读文件 / 查数 / 改一版都可以。每轮结束，右侧四件套会留下
                      「用了哪篇说明书、自检过没过、数字从哪来、答案是什么」。
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
                      {m.role === 'user' ? <UserOutlined style={{ marginTop: 4 }} /> : null}
                    </Space>
                  </div>
                ))
              )}
              {sending ? (
                <div style={{ padding: '8px 4px' }}>
                  <Space>
                    <Spin size="small" />
                    <Text type="secondary">模型在跑，右侧步骤实时更新…</Text>
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

        <Col span={10}>
          <Card
            size="small"
            styles={{ body: { paddingTop: 4, maxHeight: 548, overflowY: 'auto' } }}
            title={
              result ? (
                <Space>
                  <span>本轮结果</span>
                  <Tag color="green">已落库</Tag>
                </Space>
              ) : (
                <Space>
                  <span>最近一轮</span>
                  {latestReport?.raw_question ? <Tag>历史记录</Tag> : null}
                </Space>
              )
            }
          >
            <Tabs
              activeKey={rightTab}
              onChange={setRightTab}
              items={[
                {
                  key: 'four',
                  label: '四件套',
                  children: fourData ? (
                    <FourPack data={fourData} compact />
                  ) : (
                    <Empty description="提一个问题后，这里显示用了哪篇说明书 / 自检 / 数字来源 / 答案" />
                  ),
                },
                {
                  key: 'steps',
                  label: `运行步骤${steps.length ? `（${steps.length}）` : ''}`,
                  children:
                    steps.length === 0 ? (
                      <Empty description="提问后，每一步会实时出现在这里" />
                    ) : (
                      <Collapse
                        size="small"
                        items={steps.map((s, i) => ({
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
                                maxHeight: 260,
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
                  key: 'artifacts',
                  label: `产物${artifacts.length ? `（${artifacts.length}）` : ''}`,
                  children:
                    artifacts.length === 0 ? (
                      <Empty description="本轮没有产物；要文件就说「出一份 docx / xlsx / pptx」" />
                    ) : (
                      <Space direction="vertical" size={8} style={{ width: '100%' }}>
                        {artifacts.map((a, i) => (
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
          </Card>
        </Col>
      </Row>

      {lastIsUser && !sending ? (
        <Alert type="warning" showIcon message="上一条提问没有拿到回答，可以重发一次或到「记录」里看运行步骤。" />
      ) : null}
    </Space>
  )
}
