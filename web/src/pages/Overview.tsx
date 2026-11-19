import { useCallback, useEffect, useState } from 'react'
import { Empty, Table, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import Page from '../components/ui/Page'
import { agentHttp } from '../api/client'
import { wfApi } from '../api/workflows'

const { Text } = Typography

type Session = { id: string; title: string; updated_at?: string }

const dt = (v?: string | null) => (v ? String(v).slice(0, 16).replace('T', ' ') : '—')

const NUM_STYLE: React.CSSProperties = {
  fontSize: 28,
  fontWeight: 600,
  lineHeight: 1.2,
  fontVariantNumeric: 'tabular-nums',
}

export default function Overview() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(false)
  const [agent, setAgent] = useState({ today_turns: 0, today_failed: 0 })
  const [wf, setWf] = useState({ unread: 0, todo: 0 })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [a, w, s] = await Promise.allSettled([
        agentHttp.get('/api/agent/overview'),
        wfApi.overview(),
        agentHttp.get('/api/agent/sessions'),
      ])
      if (a.status === 'fulfilled') setAgent(a.value as any)
      if (w.status === 'fulfilled') {
        const v: any = w.value
        setWf({
          unread: v.unread ?? 0,
          todo: (v.alert_types ?? []).reduce((n: number, x: any) => n + (x.total ?? 0), 0),
        })
      }
      if (s.status === 'fulfilled') setSessions(((s.value as any).items ?? []).slice(0, 10))
    } catch (e: any) {
      message.error(e?.message || '概览数据拉不出来')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const nums = [
    { label: '今日轮次', value: agent.today_turns },
    { label: '失败数', value: agent.today_failed },
    { label: '预警条数', value: wf.unread },
    { label: '待办事项', value: wf.todo },
  ]

  const columns: ColumnsType<Session> = [
    { title: '会话标题', dataIndex: 'title', ellipsis: true, render: (v: string) => v || '未命名会话' },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: (v?: string) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{dt(v)}</span>,
    },
  ]

  return (
    <Page title="概览">
      <div
        style={{
          background: '#fff',
          borderRadius: 8,
          padding: 20,
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 16,
          marginBottom: 12,
        }}
      >
        {nums.map((n) => (
          <div key={n.label}>
            <div style={{ fontSize: 12, color: 'rgba(0,0,0,.45)', marginBottom: 4 }}>{n.label}</div>
            <div style={NUM_STYLE as any}>{n.value}</div>
          </div>
        ))}
      </div>

      <div style={{ background: '#fff', borderRadius: 8, padding: 16 }}>
        <div style={{ marginBottom: 8 }}>
          <Text strong>最近会话</Text>
        </div>
        <Table<Session>
          size="small"
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={sessions}
          pagination={false}
          onRow={(r) => ({
            onClick: () => navigate(`/chat?session=${encodeURIComponent(r.id)}`),
            style: { cursor: 'pointer' },
          })}
          locale={{
            emptyText: <Empty description="还没有会话" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
          }}
        />
      </div>
    </Page>
  )
}
