import { useCallback, useEffect, useState } from 'react'
import {
  Button, Card, Empty, Input, Popconfirm, Space, Table, Tabs, Tag, Typography, message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import Page from '../components/ui/Page'
import { flowApi } from '../api/flows'
import type { FlowItem } from '../api/flows'
import FlowEditor from './FlowEditor'
import { TriggersTab } from './Triggers'
import FlowRunsTab from './FlowRuns'

const { Text } = Typography

const dt = (v?: string | null) => (v ? String(v).slice(0, 19).replace('T', ' ') : '—')

/** 工作流页：先建工作流，再在同一页里配定时触发，最后一页看运行记录。 */
export default function Flows() {
  const [tab, setTab] = useState('flows')
  return (
    <Page title="工作流">
      <Tabs activeKey={tab} onChange={setTab} destroyInactiveTabPane
        items={[
          { key: 'flows', label: '工作流', children: <FlowsTab /> },
          { key: 'triggers', label: '定时任务', children: <TriggersTab /> },
          { key: 'runs', label: '运行记录', children: <FlowRunsTab /> },
        ]} />
    </Page>
  )
}

function FlowsTab() {
  const [rows, setRows] = useState<FlowItem[]>([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [busy, setBusy] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRows((await flowApi.list()).items)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  const create = async () => {
    try {
      const d = await flowApi.create({ name: `新工作流 ${new Date().toLocaleString('zh-CN', { hour12: false })}` })
      await load()
      setEditing(d.id)
    } catch (e) { message.error((e as Error).message) }
  }

  const runNow = async (r: FlowItem) => {
    setBusy(r.id)
    try {
      const out = await flowApi.run(r.id)
      message.success(`跑完：取到 ${out.selected_n} 行，命中 ${out.hit_n}，回填 ${out.written_n} 行`)
      await load()
    } catch (e) { message.error((e as Error).message) } finally { setBusy(0) }
  }

  const columns: ColumnsType<FlowItem> = [
    {
      title: '名称', dataIndex: 'name', width: 260,
      render: (v, r) => (
        <Space direction="vertical" size={0}>
          <a onClick={() => setEditing(r.id)}>{v}</a>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.summary.source_tables.length ? `来源：${r.summary.source_tables.join('、')}` : '还没选来源表'}
          </Text>
        </Space>
      ),
    },
    {
      title: '产出', dataIndex: 'summary', width: 300,
      render: (s: FlowItem['summary']) => (s.target_table
        ? (
          <Space direction="vertical" size={0}>
            <Text code>{s.target}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {s.target_columns.length ? s.target_columns.join('、') : '还没映射列'}
            </Text>
          </Space>
        )
        : <Text type="secondary">还没选目标表</Text>),
    },
    {
      title: '状态', width: 150,
      render: (_, r) => (r.complete
        ? <Tag color={r.enabled ? 'green' : 'default'}>{r.enabled ? '在跑' : '停用'}</Tag>
        : <Tag color="orange">还没填完</Tag>),
    },
    {
      title: '最近一轮', width: 250,
      render: (_, r) => (r.last_run ? (
        <Space direction="vertical" size={0}>
          <Space size={6}>
            <Tag color={r.last_run.status === 'ok' ? 'green' : 'red'}>
              {r.last_run.dry_run ? '试跑' : r.last_run.status === 'ok' ? '成功' : '失败'}
            </Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>{dt(r.last_run.at)}</Text>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            取到 {r.last_run.selected_n} · 命中 {r.last_run.hit_n} · 写入 {r.last_run.written_n}
          </Text>
        </Space>
      ) : <Text type="secondary">还没跑过</Text>),
    },
    {
      title: '操作', width: 250,
      render: (_, r) => (
        <Space size="middle">
          <a onClick={() => setEditing(r.id)}>编辑</a>
          <a onClick={async () => {
            setBusy(r.id)
            try {
              const out = await flowApi.dryRun(r.id)
              message.success(`试跑：取到 ${out.selected_n} 行，命中 ${out.hit_n}（没写库）`)
              await load()
            } catch (e) { message.error((e as Error).message) } finally { setBusy(0) }
          }}>试跑</a>
          <Popconfirm title="真跑一轮？会写目标表" onConfirm={() => void runNow(r)} disabled={!r.complete}>
            <a style={{ opacity: r.complete ? 1 : 0.4 }}>立即跑</a>
          </Popconfirm>
          <a onClick={async () => {
            try { await flowApi.duplicate(r.id); await load(); message.success('已复制') } catch (e) { message.error((e as Error).message) }
          }}>复制</a>
          <Popconfirm title={`删掉「${r.name}」？`} onConfirm={async () => {
            try { await flowApi.remove(r.id); await load(); message.success('已删除') } catch (e) { message.error((e as Error).message) }
          }}><a>删除</a></Popconfirm>
        </Space>
      ),
    },
  ]

  const shown = rows.filter((r) => !keyword || r.name.includes(keyword))

  return (
    <Card size="small" styles={{ body: { padding: 12 } }} extra={
      <Space>
        <Input.Search allowClear placeholder="按名字找" style={{ width: 200 }}
          value={keyword} onChange={(e) => setKeyword(e.target.value)} />
        <Button onClick={() => void load()}>刷新</Button>
        <Button type="primary" onClick={() => void create()}>新建工作流</Button>
      </Space>
    }>
      <Table rowKey="id" size="small" loading={loading} dataSource={shown} columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有工作流。新建一份，填完五段就能跑。" /> }} />
      {busy ? <Text type="secondary">正在跑…</Text> : null}

      {editing !== null && (
        <FlowEditor flowId={editing} onClose={() => { setEditing(null); void load() }}
          onSaved={() => void load()} />
      )}
    </Card>
  )
}
