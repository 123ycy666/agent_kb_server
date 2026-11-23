import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Drawer, Empty, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { flowApi } from '../api/flows'
import type { FlowRun } from '../api/flows'

const { Text } = Typography

const dt = (v?: string | null) => (v ? String(v).slice(0, 19).replace('T', ' ') : '—')

export default function FlowRunsTab({ flowId }: { flowId?: number }) {
  const [rows, setRows] = useState<FlowRun[]>([])
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<FlowRun | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRows((await flowApi.runs(flowId ? { flow_id: flowId } : {})).items)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }, [flowId])

  useEffect(() => { void load() }, [load])

  const columns: ColumnsType<FlowRun> = [
    { title: '时间', dataIndex: 'started_at', width: 165, render: dt },
    { title: '工作流', dataIndex: 'flow_name', width: 240 },
    {
      title: '触发', dataIndex: 'trigger', width: 90,
      render: (v: string, r) => <Tag color={r.dry_run ? 'purple' : v === 'cron' ? 'blue' : 'default'}>
        {r.dry_run ? '试跑' : v === 'cron' ? '定时' : v === 'manual' ? '手动' : v}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (v: string) => <Tag color={v === 'ok' ? 'green' : v === 'failed' ? 'red' : 'orange'}>{v}</Tag>,
    },
    { title: '取到', dataIndex: 'selected_n', width: 80 },
    { title: '命中', dataIndex: 'hit_n', width: 80 },
    { title: '写入', dataIndex: 'written_n', width: 80 },
    { title: '耗时', dataIndex: 'elapsed_ms', width: 90, render: (v: number) => `${v} 毫秒` },
    {
      title: '错误', dataIndex: 'error',
      render: (v?: string | null) => (v ? <Text type="danger">{v.slice(0, 60)}</Text> : '—'),
    },
  ]

  return (
    <Card size="small" styles={{ body: { padding: 12 } }} extra={<Button onClick={() => void load()}>刷新</Button>}
      title="运行记录">
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns}
        onRow={(r) => ({ onClick: () => setDetail(r), style: { cursor: 'pointer' } })}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没跑过" /> }} />

      <Drawer open={!!detail} width={900} onClose={() => setDetail(null)} destroyOnClose
        title={detail ? `${detail.flow_name} · 第 ${detail.flow_version} 版 · ${detail.status}` : ''}>
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text>
              触发 {detail.trigger}　取到 {detail.selected_n} 行　命中 {detail.hit_n}　写入 {detail.written_n}
              　耗时 {detail.elapsed_ms} 毫秒
            </Text>
            {detail.error && <Text type="danger">{detail.error}</Text>}
            <Card size="small" title="这一轮编译出的 SQL">
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>{detail.sql_text || '—'}</pre>
            </Card>
          </Space>
        )}
      </Drawer>
    </Card>
  )
}
