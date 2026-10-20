import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Empty,
  Input,
  List,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ClockCircleOutlined,
  DeleteOutlined,
  HistoryOutlined,
  ReloadOutlined,
  SearchOutlined,
  SendOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  chatHistoryApi,
  type ChatHistoryResult,
  type ChatMessage,
  type ChatSession,
  type TraceStep,
} from '../api'

const { Text, Paragraph } = Typography

function fmt(s?: string | null, f = 'MM-DD HH:mm') {
  return s ? dayjs(s).format(f) : ''
}

/** 从消息里取出「工具轨迹数组」（服务端字段复用：可能是数字，也可能是数组） */
function stepsOf(m: ChatMessage): TraceStep[] {
  return Array.isArray(m.steps) ? (m.steps as TraceStep[]) : []
}

export default function ChatHistory() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [listLoading, setListLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [activeId, setActiveId] = useState('')
  const [detail, setDetail] = useState<ChatHistoryResult | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  /**
   * 管理员可切换「全部用户」（2026-10-20 加）。
   *
   * 起因：管理员反馈「看不到别人的对话历史」。那不是 bug —— 会话按 user_id 归属，
   * admin 查自己当然只有自己的。但运维/审计确实需要看全部，所以给个开关。
   *
   * ⚠️ 能不能看**由服务端判定**（响应里的 can_view_all），不在前端猜角色：
   *    实测 /auth/me 返回的 role_names、administrator_flag 都是 None，
   *    前端按角色名判断会永远判成「不是管理员」，开关压根不显示。
   * 服务端还会二次校验，普通用户即使传 all_users 也只拿到自己的。
   */
  const [allUsers, setAllUsers] = useState(false)
  const [canViewAll, setCanViewAll] = useState(false)

  const loadSessions = useCallback(async () => {
    setListLoading(true)
    try {
      const r = await chatHistoryApi.sessions({ allUsers })
      setSessions(r.sessions || [])
      setCanViewAll(Boolean(r.can_view_all))
      if (r.error) message.warning('会话列表加载异常：' + r.error)
    } catch (e) {
      message.error((e as Error).message || '加载会话列表失败')
    } finally {
      setListLoading(false)
    }
  }, [allUsers])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  /**
   * 回到本页时自动刷新会话列表。
   *
   * 为什么必须有（2026-10-20 修的「刚回答的问题没有历史」）：
   * 原来只在组件挂载时拉一次。用户在问答页问完，**切回已打开的历史页标签**时
   * 组件并没有重新挂载 —— 看到的一直是进页面那一刻的旧列表，
   * 于是判定「刚问的没落库」。数据其实在库里（查过 /api/sessions 每次都在）。
   *
   * 只监听 visibilitychange 就够：跨标签切换、从后台切回来都会触发；
   * 同标签内路由跳转本来就会重新挂载，不需要额外处理。
   * focus 事件太吵（点一下输入框就触发），不监听。
   */
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') loadSessions()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [loadSessions])

  // 选中某会话后加载详情（带竞态保护：快速点击时丢弃过期的响应）
  useEffect(() => {
    if (!activeId) {
      setDetail(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    chatHistoryApi
      .history(activeId, true)
      .then((r) => {
        if (!cancelled) setDetail(r)
      })
      .catch((e) => {
        if (!cancelled) message.error((e as Error).message || '加载会话详情失败')
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [activeId])

  const filtered = useMemo(() => {
    const k = keyword.trim().toLowerCase()
    if (!k) return sessions
    return sessions.filter((s) => (s.title || '').toLowerCase().includes(k))
  }, [sessions, keyword])

  /** 继续该会话：把 session_id 写进 localStorage（问答页读的就是这个 key），再跳到问答页 */
  const continueSession = (sid: string) => {
    try {
      localStorage.setItem('secretary_session', sid)
    } catch {
      /* 隐私模式下 localStorage 不可用，忽略 */
    }
    navigate('/agent')
  }

  /**
   * 删除一场会话。
   *
   * 服务端是**软删**（deleted_flag=1），删错的还能由管理员恢复，
   * 但界面上就是"没了"，所以必须二次确认（Popconfirm）——误触代价不对称：
   * 多点一次的代价是一秒，误删的代价是整场问答记录看不见了。
   */
  const removeSession = async (sid: string) => {
    try {
      await chatHistoryApi.remove(sid)
      message.success('已删除该会话')
      // 本地状态同步：只从列表里摘掉，不整表重拉（重拉会闪、还会丢滚动位置）
      setSessions((prev) => prev.filter((x) => x.session_id !== sid))
      if (activeId === sid) {
        setActiveId('')
        setDetail(null)
      }
      // 如果删的正是问答页正在用的那场，把 localStorage 清掉，
      // 否则切回问答页会继续往已删除的会话里追加消息
      try {
        if (localStorage.getItem('secretary_session') === sid) {
          localStorage.removeItem('secretary_session')
        }
      } catch {
        /* 忽略 */
      }
    } catch (e) {
      message.error((e as Error).message || '删除失败')
    }
  }

  const messages = detail?.messages || []

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="历史对话"
        description="这里是你自己的问答记录。左侧选一场会话，右侧看完整对话、查询口径与质量标记；也可以「继续此会话」接着聊。"
        action={
          <Button icon={<ReloadOutlined />} onClick={loadSessions} loading={listLoading}>
            刷新
          </Button>
        }
      />

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <Card
          size="small"
          style={{ width: 380, flex: '0 0 380px' }}
          styles={{ body: { padding: 12 } }}
          title={
            <Space>
              <HistoryOutlined />
              <span>会话记录（{filtered.length}）</span>
              {/* 开关显不显示由服务端的 can_view_all 决定；普通用户即使放开也拿不到别人的 */}
              {canViewAll && (
                <Checkbox
                  checked={allUsers}
                  onChange={(e) => setAllUsers(e.target.checked)}
                  style={{ fontWeight: 400, fontSize: 12 }}
                >
                  全部用户
                </Checkbox>
              )}
            </Space>
          }
        >
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="按标题搜索"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <div style={{ maxHeight: 'calc(100vh - 300px)', overflowY: 'auto' }}>
            <List
              loading={listLoading}
              dataSource={filtered}
              locale={{
                emptyText: (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无会话记录"
                  />
                ),
              }}
              renderItem={(s) => (
                <List.Item
                  onClick={() => setActiveId(s.session_id)}
                  style={{
                    cursor: 'pointer',
                    padding: '10px 10px',
                    borderRadius: 6,
                    background: s.session_id === activeId ? '#e6f4ff' : undefined,
                  }}
                >
                  <div style={{ width: '100%', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ marginBottom: 4 }}>
                        <Text strong ellipsis style={{ maxWidth: 260 }}>
                          {s.title || '（无标题会话）'}
                        </Text>
                      </div>
                      <Space size={4} wrap>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          <ClockCircleOutlined /> {fmt(s.last_msg_at || s.create_time)}
                        </Text>
                        <Tag color="blue">{s.ask_count} 轮</Tag>
                        {/* 「全部用户」模式下必须标出归属，否则分不清是谁问的 */}
                        {allUsers && (
                          <Tag color="purple">
                            {s.user_code || (s.user_id ? `用户#${s.user_id}` : '未归属')}
                          </Tag>
                        )}
                        {s.gap_count > 0 && <Tag color="orange">{s.gap_count} 缺口</Tag>}
                      </Space>
                    </div>
                    {/* 删除按钮：必须先 stopPropagation，否则点它会把这场会话选中 */}
                    <Popconfirm
                      title="删除这场会话？"
                      description="删除后列表不再显示（服务端为软删，可由管理员恢复）"
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={() => removeSession(s.session_id)}
                    >
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        title="删除该会话"
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Popconfirm>
                  </div>
                </List.Item>
              )}
            />
          </div>
        </Card>

        <div style={{ flex: 1, minWidth: 0 }}>
          {detailLoading ? (
            <Card>
              <div style={{ textAlign: 'center', padding: 60 }}>
                <Spin />
              </div>
            </Card>
          ) : !detail ? (
            <Card>
              <Empty
                description="请从左侧选择一场会话"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                style={{ padding: 60 }}
              />
            </Card>
          ) : (
            <Card
              title={
                <Space>
                  <Text strong>{detail.session?.title || '（无标题会话）'}</Text>
                  {detail.session?.ai_model && <Tag>{detail.session.ai_model}</Tag>}
                </Space>
              }
              extra={
                <Space>
                  <Popconfirm
                    title="删除这场会话？"
                    description="删除后列表不再显示（服务端为软删，可由管理员恢复）"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => removeSession(activeId)}
                  >
                    <Button danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    onClick={() => continueSession(activeId)}
                  >
                    继续此会话
                  </Button>
                </Space>
              }
            >
              <Space size={16} wrap style={{ marginBottom: 16 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  会话 ID：{activeId}
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  开始：{fmt(detail.session?.create_time, 'YYYY-MM-DD HH:mm')}
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  消息：{detail.total} 条
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  末次活动：{fmt(detail.session?.last_msg_at, 'YYYY-MM-DD HH:mm')}
                </Text>
              </Space>

              {messages.map((m) => (
                <MessageBubble key={m.id} m={m} />
              ))}
            </Card>
          )}
        </div>
      </div>
    </>
  )
}

/** 该轮问答的口径与工具轨迹（折叠内容） */
function QueryDetail({ m, steps }: { m: ChatMessage; steps: TraceStep[] }) {
  return (
    <div style={{ fontSize: 12 }}>
      {m.fixed_question && (
        <Paragraph style={{ marginBottom: 4 }}>
          <Text type="secondary">纠错后问题：</Text>
          {m.fixed_question}
        </Paragraph>
      )}
      {m.completed_question && (
        <Paragraph style={{ marginBottom: 4 }}>
          <Text type="secondary">补全后问题：</Text>
          {m.completed_question}
        </Paragraph>
      )}
      {(m.time_text || m.period_key) && (
        <Paragraph style={{ marginBottom: 4 }}>
          <Text type="secondary">时间口径：</Text>
          {m.time_text || m.period_key}
          {m.time_is_default === 1 && (
            <Tag color="gold" style={{ marginLeft: 4 }}>
              系统默认
            </Tag>
          )}
        </Paragraph>
      )}
      {m.place && (
        <Paragraph style={{ marginBottom: 4 }}>
          <Text type="secondary">地点口径：</Text>
          {m.place}
          {m.place_is_default === 1 && (
            <Tag color="gold" style={{ marginLeft: 4 }}>
              系统默认
            </Tag>
          )}
        </Paragraph>
      )}

      {steps.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <Text type="secondary">工具轨迹（{steps.length} 步）</Text>
          <div style={{ marginTop: 4 }}>
            {steps.map((s, i) => (
              <div
                key={s.step_no ?? i}
                style={{ padding: '3px 0', borderBottom: '1px dashed #f0f0f0' }}
              >
                <Tag>{s.tool_name || s.step_kind || 'step'}</Tag>
                {s.status != null && (
                  <Tag color={s.status === 0 ? 'green' : 'red'}>
                    {s.status === 0 ? '成功' : '失败'}
                  </Tag>
                )}
                {s.cost_ms != null && <span style={{ marginRight: 8 }}>耗时 {s.cost_ms}ms</span>}
                {s.result_rows != null && <span>· {s.result_rows} 行</span>}
                {s.error_msg && (
                  <div style={{ color: '#cf1322', marginTop: 2 }}>{s.error_msg}</div>
                )}
                {s.sql_text && (
                  <div style={{ color: '#888', marginTop: 2, wordBreak: 'break-all' }}>
                    {String(s.sql_text).slice(0, 300)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** 单条消息气泡（用户右侧 / AI 左侧），下方挂该轮的口径与质量标签、可展开的工具轨迹 */
function MessageBubble({ m }: { m: ChatMessage }) {
  const isUser = m.direction === 1
  const steps = stepsOf(m)
  const expandable =
    !isUser && Boolean(m.completed_question || m.fixed_question || m.time_text || m.place || steps.length)

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        marginBottom: 16,
      }}
    >
      <div style={{ maxWidth: '80%' }}>
        <div
          style={{
            background: isUser ? '#1677ff' : '#f5f5f5',
            color: isUser ? '#fff' : 'rgba(0,0,0,.88)',
            padding: '10px 14px',
            borderRadius: isUser ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            lineHeight: 1.7,
          }}
        >
          {m.content || '（空）'}
        </div>

        <div style={{ marginTop: 6, textAlign: isUser ? 'right' : 'left' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {fmt(m.create_time, 'MM-DD HH:mm:ss')}
          </Text>
          {!isUser && (
            <>
              {m.elapsed_ms != null && (
                <Tag style={{ marginLeft: 6 }}>{(m.elapsed_ms / 1000).toFixed(1)}s</Tag>
              )}
              {!!m.db_query_count && <Tag color="geekblue">查库 {m.db_query_count}</Tag>}
              {!!m.kb_query_count && <Tag color="purple">知识库 {m.kb_query_count}</Tag>}
              {m.name_fix_used === 1 && <Tag color="cyan">名称纠错</Tag>}
              {m.unverified === 1 && <Tag color="orange">证据不足 · 拒答</Tag>}
              {m.is_gap === 1 && <Tag color="red">知识缺口</Tag>}
              {m.feedback === 1 && <Tag color="green">已赞</Tag>}
              {m.feedback === 0 && <Tag color="red">已踩</Tag>}
            </>
          )}
        </div>

        {expandable && (
          <Collapse
            ghost
            size="small"
            style={{ marginTop: 2 }}
            items={[
              {
                key: 'detail',
                label: (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    查看口径与工具轨迹{steps.length > 0 ? `（${steps.length} 步）` : ''}
                  </Text>
                ),
                children: <QueryDetail m={m} steps={steps} />,
              },
            ]}
          />
        )}
      </div>
    </div>
  )
}
