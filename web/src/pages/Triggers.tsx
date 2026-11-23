import { useCallback, useEffect, useState } from 'react'
import {
  Button, Card, Empty, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, TimePicker,
  Typography, message,
} from 'antd'
import dayjs from 'dayjs'
import type { ColumnsType } from 'antd/es/table'
import { flowApi } from '../api/flows'
import type { FlowItem, TriggerItem } from '../api/flows'

const { Text } = Typography
const dt = (v?: string | null) => (v ? String(v).slice(0, 19).replace('T', ' ') : '—')

const WEEK = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const MONTH_DAYS = Array.from({ length: 28 }, (_v, i) => ({ value: i + 1, label: `${i + 1} 号` }))

type Unit = 'day' | 'week' | 'month'

export function TriggersTab() {
  const [rows, setRows] = useState<TriggerItem[]>([])
  const [flows, setFlows] = useState<FlowItem[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<TriggerItem | null>(null)
  const [name, setName] = useState('')
  const [unit, setUnit] = useState<Unit>('day')
  const [dow, setDow] = useState<number[]>([1])
  const [Dom, setDom] = useState<number[]>([1])
  const [times, setTimes] = useState<string[]>(['08:00'])
  const [flowIds, setFlowIds] = useState<number[]>([])
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [t, f] = await Promise.all([flowApi.triggers(), flowApi.list()])
      setRows(t.items)
      setFlows(f.items)
    } catch (e) { message.error((e as Error).message) } finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  const openNew = () => {
    setEditing(null); setName(''); setUnit('day'); setDow([1]); setDom([1])
    setTimes(['08:00']); setFlowIds([]); setEnabled(true); setOpen(true)
  }
  const openEdit = (t: TriggerItem) => {
    setEditing(t); setName(t.name)
    const r = t.rule || { unit: 'day', dow: [], dom: [], times: t.times }
    setUnit((r.unit || 'day') as Unit)
    setDow(r.dow && r.dow.length ? r.dow : [1])
    setDom(r.dom && r.dom.length ? r.dom : [1])
    setTimes(r.times && r.times.length ? r.times : ['08:00'])
    setFlowIds(t.flows.map((f) => f.id)); setEnabled(t.enabled); setOpen(true)
  }

  const save = async () => {
    const clean = times.map((x) => x.trim()).filter(Boolean)
    if (!name.trim()) { message.error('给定时任务起个名字'); return }
    if (!clean.length) { message.error('至少一个时间点'); return }
    if (unit === 'week' && !dow.length) { message.error('选星期几'); return }
    if (unit === 'month' && !Dom.length) { message.error('选每月几号'); return }
    setSaving(true)
    try {
      const rule = { unit, dow: unit === 'week' ? dow : [], dom: unit === 'month' ? Dom : [], times: clean }
      const body = { name, rule, times: clean, flow_ids: flowIds, enabled }
      if (editing) await flowApi.saveTrigger(editing.id, body)
      else await flowApi.createTrigger(body)
      setOpen(false)
      await load()
      message.success('已保存')
    } catch (e) { message.error((e as Error).message) } finally { setSaving(false) }
  }

  const columns: ColumnsType<TriggerItem> = [
    { title: '名称', dataIndex: 'name', width: 200 },
    {
      title: '周期', width: 230,
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <Tag>{r.rule_text || (r.times || []).join('、')}</Tag>
          {r.next?.[0] && <Text type="secondary" style={{ fontSize: 12 }}>下次 {r.next[0]}</Text>}
        </Space>
      ),
    },
    {
      title: '挂的工作流', dataIndex: 'flows',
      render: (v: TriggerItem['flows']) => (v.length
        ? <Space wrap size={4}>{v.map((f) => <Tag key={f.id} color={f.enabled ? 'blue' : 'default'}>{f.name}</Tag>)}</Space>
        : <Text type="secondary">没挂工作流</Text>),
    },
    {
      title: '上次', dataIndex: 'last', width: 200,
      render: (v: TriggerItem['last']) => (v ? <Text type="secondary">
        {dt(v.at)} · {v.status === 'ok' ? '成功' : v.status} · 写入 {v.written_n}
      </Text> : '—'),
    },
    {
      title: '启用', dataIndex: 'enabled', width: 80,
      render: (v: boolean, r) => (
        <Switch size="small" checked={v} onChange={async (on) => {
          try { await flowApi.saveTrigger(r.id, { enabled: on }); await load() } catch (e) { message.error((e as Error).message) }
        }} />
      ),
    },
    {
      title: '操作', width: 210,
      render: (_, r) => (
        <Space size="middle">
          <a onClick={() => openEdit(r)}>编辑</a>
          <Popconfirm title="立刻按这个时间表跑一遍？" onConfirm={async () => {
            try {
              const out = await flowApi.runTrigger(r.id) as { results?: { flow: string; status: string; written_n?: number }[] }
              const list = (out?.results || []).map((x) => `${x.flow}:${x.status}${x.written_n !== undefined ? '(' + x.written_n + ')' : ''}`).join('；')
              message.success(list || '没有可跑的工作流')
              await load()
            } catch (e) { message.error((e as Error).message) }
          }}><a>立即跑</a></Popconfirm>
          <Popconfirm title={`删掉「${r.name}」？`} onConfirm={async () => {
            try { await flowApi.deleteTrigger(r.id); await load(); message.success('已删除') }
            catch (e) { message.error((e as Error).message) }
          }}><a>删除</a></Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card size="small" styles={{ body: { padding: 12 } }} extra={
      <Space>
        <Button onClick={() => void load()}>刷新</Button>
        <Button type="primary" onClick={openNew}>新建定时任务</Button>
      </Space>
    }>
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有定时任务。建一条，选好周期和几点，再挂上工作流。" /> }} />

      <Modal open={open} width={720} destroyOnClose confirmLoading={saving}
        title={editing ? '改定时任务' : '新建定时任务'} okText="保存"
        onCancel={() => setOpen(false)} onOk={() => void save()}>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <div style={{ marginBottom: 4 }}>名称</div>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例：每天八点跑结算" />
          </div>

          <div>
            <div style={{ marginBottom: 4 }}>多久跑一次</div>
            <Space wrap>
              <Select value={unit} style={{ width: 120 }} onChange={(v) => setUnit(v as Unit)}
                options={[{ value: 'day', label: '每天' }, { value: 'week', label: '每周' },
                  { value: 'month', label: '每月' }]} />
              {unit === 'week' && (
                <Select mode="multiple" allowClear style={{ minWidth: 300 }} value={dow} onChange={setDow}
                  placeholder="星期几（可多选）"
                  options={WEEK.map((w, i) => ({ value: i + 1, label: w }))} />
              )}
              {unit === 'month' && (
                <Select mode="multiple" allowClear style={{ minWidth: 300 }} value={Dom} onChange={setDom}
                  placeholder="每月几号（可多选）" options={MONTH_DAYS} />
              )}
            </Space>
          </div>

          <div>
            <div style={{ marginBottom: 4 }}>几点跑（可以加多个时间点）</div>
            <Space wrap>
              {times.map((t, i) => (
                <Space key={i} size={4}>
                  <TimePicker format="HH:mm" minuteStep={5} allowClear={false}
                    value={/^\d{2}:\d{2}$/.test(t) ? dayjs('2000-01-01 ' + t) : null}
                    onChange={(v) => setTimes((arr) => arr.map((x, j) => (j === i ? (v ? v.format('HH:mm') : x) : x)))} />
                  {times.length > 1 && (
                    <Button size="small" type="text" danger
                      onClick={() => setTimes((arr) => arr.filter((_, j) => j !== i))}>×</Button>
                  )}
                </Space>
              ))}
              <Button size="small" onClick={() => setTimes((arr) => [...arr, '09:00'])}>加一个时间点</Button>
            </Space>
          </div>

          <div>
            <div style={{ marginBottom: 4 }}>挂哪些工作流（按选择顺序跑）</div>
            <Select mode="multiple" allowClear style={{ width: '100%' }} value={flowIds} onChange={setFlowIds}
              optionFilterProp="label" placeholder="选工作流"
              options={flows.map((f) => ({
                value: f.id,
                label: `${f.name}${f.complete ? '' : '（还没填完）'}`,
                disabled: !f.complete,
              }))} />
            <div style={{ marginTop: 4 }}>
              <Text type="secondary">没填完的工作流不能挂。停用的工作流会被跳过。</Text>
            </div>
          </div>

          <div><span style={{ marginRight: 8 }}>启用</span><Switch checked={enabled} onChange={setEnabled} /></div>
        </Space>
      </Modal>
    </Card>
  )
}
