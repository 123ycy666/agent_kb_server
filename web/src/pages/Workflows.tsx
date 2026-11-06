import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Collapse, Descriptions, Drawer, Empty, Form, Input, InputNumber,
  Modal, Popconfirm, Select, Space, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import { wfApi } from '../api/workflows'
import type { WfDoc, WfItem, WfRun, WfSchedule, WfSection } from '../api/workflows'

const { Title, Paragraph, Text } = Typography

const TIER: Record<string, { text: string; color: string }> = {
  high: { text: '高', color: 'red' },
  mid: { text: '中', color: 'orange' },
  low: { text: '低', color: 'blue' },
}
const WEEK = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

/** 把 cron 翻成人话；认不出就原样显示 */
function cronText(cron: string): string {
  const m = String(cron || '').trim().split(/\s+/)
  if (m.length !== 5) return cron || '—'
  const [mi, hh, dom, mon, dow] = m
  const t = `${String(hh).padStart(2, '0')}:${String(mi).padStart(2, '0')}`
  if (mi.startsWith('*/') && hh === '*') return `每 ${mi.slice(2)} 分钟`
  if (hh.startsWith('*/') && dom === '*') return `每 ${hh.slice(2)} 小时（第 ${mi} 分）`
  if (dom === '*' && mon === '*' && dow === '*') return `每天 ${t}`
  if (dom === '*' && mon === '*' && /^\d$/.test(dow)) return `每${WEEK[Number(dow) - 1]} ${t}`
  if (mon === '*' && /^\d+$/.test(dom)) return `每月 ${dom} 号 ${t}`
  return cron
}

const fmt = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : String(v))
const dt = (v?: string | null) => (v ? String(v).slice(0, 19).replace('T', ' ') : '—')
const err = (e: unknown) => {
  const any = e as { response?: { data?: { detail?: { message?: string } } }; message?: string }
  return any?.response?.data?.detail?.message || any?.message || '请求失败'
}

function LogsTable({ logs }: { logs: WfRun['logs'] }) {
  return (
    <Table size="small" rowKey="seq" dataSource={logs || []} pagination={{ pageSize: 30 }}
      columns={[
        { title: '#', dataIndex: 'seq', width: 45 },
        { title: '种类', dataIndex: 'kind', width: 80 },
        {
          title: '内容', dataIndex: 'payload',
          render: (p: Record<string, unknown>) => {
            const bad = (p as { ok?: boolean; error?: unknown })
            return (
              <Text code style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {bad?.ok === false || bad?.error ? '⚠ ' : ''}{JSON.stringify(p).slice(0, 500)}
              </Text>
            )
          },
        },
      ]} />
  )
}

// ══════════════════════════════════════════════ 1 规则文档
function DocsTab() {
  const [docs, setDocs] = useState<WfDoc[]>([])
  const [loading, setLoading] = useState(false)
  const [openNew, setOpenNew] = useState(false)
  const [editing, setEditing] = useState<WfDoc | null>(null)
  const [test, setTest] = useState<{ name: string; run: Record<string, unknown>; logs: WfRun['logs'] } | null>(null)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try { setDocs((await wfApi.docs()).items) } catch (e) { message.error(err(e)) } finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const create = async () => {
    const v = await form.validateFields()
    try {
      const d = await wfApi.createDoc(v)
      setOpenNew(false)
      form.resetFields()
      await load()
      setEditing(d)
    } catch (e) { message.error(err(e)) }
  }

  const toggle = async (d: WfDoc, on: boolean) => {
    try {
      const r = await wfApi.saveDoc(d.id, { enabled: on })
      setDocs((arr) => arr.map((x) => (x.id === d.id ? { ...x, enabled: on, schedule_id: on ? x.schedule_id : null } : x)))
      if (!on) message.success(r.detached_from ? `已停用，并从定时方案「${r.detached_from}」上解挂` : '已停用')
      else message.success('已启用')
    } catch (e) { message.error(err(e)) }
  }

  const exportDoc = async (d: WfDoc) => {
    try {
      const blob = await wfApi.exportDoc(d.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${d.name}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { message.error(err(e)) }
  }

  const doTest = async (d: WfDoc) => {
    const key = 'wf-test'
    message.loading({ content: `正在测试「${d.name}」…`, key, duration: 0 })
    try {
      const r = await wfApi.dryRun(d.id) as Record<string, unknown>
      message.destroy(key)
      const runId = Number(r.run_id || 0)
      const full = runId ? await wfApi.runDetail(runId) : null
      setTest({ name: d.name, run: r, logs: full?.logs || [] })
      await load()
    } catch (e) { message.destroy(key); message.error(err(e)) }
  }

  const columns: ColumnsType<WfDoc> = [
    { title: '文档名', dataIndex: 'name', render: (v, r) => <a onClick={() => setEditing(r)}>{v}</a> },
    { title: '工种', dataIndex: 'work_type_label', width: 120, render: (v) => <Tag>{v}</Tag> },
    {
      title: '启用', dataIndex: 'enabled', width: 90,
      render: (v, r) => <Switch size="small" checked={v} onChange={(on) => void toggle(r, on)} />,
    },
    { title: '更新时间', dataIndex: 'updated_at', width: 170, render: dt },
    {
      title: '操作', width: 210,
      render: (_, r) => (
        <Space size="middle">
          <a onClick={() => setEditing(r)}>编辑</a>
          <a onClick={() => void doTest(r)}>测试</a>
          <a onClick={() => void exportDoc(r)}>导出</a>
          <Popconfirm title={`删除「${r.name}」？`} onConfirm={async () => {
            try { await wfApi.deleteDoc(r.id); await load(); message.success('已删除') } catch (e) { message.error(err(e)) }
          }}><a>删除</a></Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card title="规则文档" extra={<Space>
      <Button onClick={() => void load()}>刷新</Button>
      <Button type="primary" onClick={() => setOpenNew(true)}>新建文档</Button>
    </Space>}>
      <Table rowKey="id" size="small" loading={loading} dataSource={docs} columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有规则文档，先新建一份" /> }} />

      <Modal title="新建规则文档" open={openNew} onCancel={() => setOpenNew(false)}
        onOk={() => void create()} okText="创建" destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{ work_type: 'detect' }}>
          <Form.Item name="name" label="文档名称" rules={[{ required: true, message: '填个名字' }]}>
            <Input placeholder="例：预算花不完" onPressEnter={() => void create()} />
          </Form.Item>
          <Form.Item name="work_type" label="工种" rules={[{ required: true }]}>
            <Select options={[
              { value: 'detect', label: '取数工作流（看数据、判、落一条）' },
              { value: 'send', label: '发送工作流（读事项、发给人）' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal open={!!test} width={820} footer={null} onCancel={() => setTest(null)} destroyOnClose
        title={test ? `测试结果：${test.name}` : ''}>
        {test && (
          <Space direction="vertical" style={{ width: '100%' }}>
            {String(test.run.status) === 'ok' ? (
              <Alert type="success" showIcon
                message={`能跑通：查了 ${fmt(test.run.checked_n)} 个对象，命中 ${fmt(test.run.hit_n)} 条`}
                description="测试只按文档实际跑一遍、不写结果表也不写事项表，所以不用还原；这一轮留在运行记录里，标为「测试」。" />
            ) : (
              <Alert type="error" showIcon message="没跑通"
                description={String(test.run.error || '看下面步骤里的报错')} />
            )}
            <LogsTable logs={test.logs} />
          </Space>
        )}
      </Modal>

      {editing && (
        <DocEditor doc={editing}
          onTest={(name, run, logs) => setTest({ name, run, logs })}
          onClose={() => { setEditing(null); void load() }} />
      )}
    </Card>
  )
}

function DocEditor({ doc, onTest, onClose }: {
  doc: WfDoc
  onTest: (name: string, run: Record<string, unknown>, logs: WfRun['logs']) => void
  onClose: () => void
}) {
  const [detail, setDetail] = useState<WfDoc | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [orig, setOrig] = useState<{ name: string; values: Record<string, string> }>({ name: '', values: {} })
  const [name, setName] = useState(doc.name)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    void (async () => {
      try {
        const d = await wfApi.doc(doc.id)
        setDetail(d)
        setValues({ ...(d.doc || {}) })
        setOrig({ name: d.name, values: { ...(d.doc || {}) } })
        setName(d.name)
      } catch (e) { message.error(err(e)) }
    })()
  }, [doc.id])

  const sections: WfSection[] = detail?.sections || []
  const empty = sections.filter((s) => !(values[s.key] || '').trim()).map((s) => s.title)
  const changed = name !== orig.name
    || sections.some((s) => (values[s.key] || '') !== (orig.values[s.key] || ''))

  const runTest = async (label: string) => {
    const key = 'wf-test'
    message.loading({ content: `正在按文档实际跑一遍（不写表）：${label}`, key, duration: 0 })
    try {
      const r = await wfApi.dryRun(doc.id) as Record<string, unknown>
      message.destroy(key)
      const runId = Number(r.run_id || 0)
      const full = runId ? await wfApi.runDetail(runId) : null
      onTest(name || label, r, full?.logs || [])
    } catch (e) {
      message.destroy(key)
      message.error(err(e))
    }
  }

  const save = async () => {
    setSaving(true)
    try {
      await wfApi.saveDoc(doc.id, {
        name,
        doc: sections.reduce<Record<string, string>>((a, s) => ({ ...a, [s.key]: values[s.key] || '' }), {}),
      })
      setSaving(false)
      if (changed) {
        await runTest(name)
        message.success('已保存，并按文档跑了一遍测试（未写表）')
        onClose()
      } else {
        message.success('已保存（内容没有变化，未测试）')
        onClose()
      }
    } catch (e) { message.error(err(e)); setSaving(false) }
  }

  const exportDoc = async () => {
    try {
      const blob = await wfApi.exportDoc(doc.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${name || 'doc'}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { message.error(err(e)) }
  }

  return (
    <Drawer open width={880} onClose={onClose} destroyOnClose
      title={<Space>
        <Input value={name} onChange={(e) => setName(e.target.value)} style={{ width: 280 }} />
        <Tag>{detail?.work_type_label}</Tag>
      </Space>}
      extra={<Space>
        <Button onClick={() => void exportDoc()}>导出</Button>
        <Button type="primary" loading={saving} onClick={() => void save()}>保存</Button>
      </Space>}>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {empty.length > 0 && (
          <Alert type="info" showIcon message={`还空着 ${empty.length} 节：${empty.join('、')}`}
            description="空着的节 = 这件事不做；要跑必须先填全。" />
        )}
        {sections.map((s) => (
          <Card key={s.key} size="small"
            title={<Space>{s.title}<Text type="secondary" style={{ fontWeight: 400 }}>{s.hint}</Text></Space>}>
            <Input.TextArea
              value={values[s.key] || ''}
              onChange={(e) => setValues((v) => ({ ...v, [s.key]: e.target.value }))}
              autoSize={{ minRows: 3, maxRows: 14 }}
              placeholder={'例：' + s.example}
            />
          </Card>
        ))}
      </Space>
    </Drawer>
  )
}

function SchedulesTab({ docs }: { docs: WfDoc[] }) {
  type T = { unit: string; n: number; hh: number; mm: number; dow: number; dom: number }
  const [list, setList] = useState<WfSchedule[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<WfSchedule | null>(null)
  const [name, setName] = useState('')
  const [t, setT] = useState<T>({ unit: 'day', n: 1, hh: 8, mm: 0, dow: 1, dom: 1 })
  const [docIds, setDocIds] = useState<number[]>([])
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try { setList((await wfApi.schedules()).items) } catch (e) { message.error(err(e)) }
  }, [])
  useEffect(() => { void load() }, [load])

  const toCron = (x: T) => {
    const hm = `${x.mm} ${x.hh}`
    if (x.unit === 'minute') return `*/${x.n} * * * *`
    if (x.unit === 'hour') return `${x.mm} */${x.n} * * *`
    if (x.unit === 'day') return `${hm} * * *`
    if (x.unit === 'week') return `${hm} * * ${x.dow}`
    return `${hm} ${x.dom} * *`
  }

  const parseCron = (c: string): T => {
    const [mi, hh, dom, , dow] = String(c || '').split(/\s+/)
    if (String(mi).startsWith('*/')) return { unit: 'minute', n: Number(String(mi).slice(2)) || 1, hh: 8, mm: 0, dow: 1, dom: 1 }
    if (String(hh).startsWith('*/')) return { unit: 'hour', n: Number(String(hh).slice(2)) || 1, hh: 8, mm: Number(mi) || 0, dow: 1, dom: 1 }
    const base = { n: 1, hh: Number(hh) || 8, mm: Number(mi) || 0, dow: Number(dow) || 1, dom: Number(dom) || 1 }
    if (dow !== '*') return { ...base, unit: 'week' }
    if (dom !== '*') return { ...base, unit: 'month' }
    return { ...base, unit: 'day' }
  }

  const openNew = () => {
    setEditing(null); setName(''); setDocIds([]); setEnabled(true)
    setT({ unit: 'day', n: 1, hh: 8, mm: 0, dow: 1, dom: 1 })
    setOpen(true)
  }
  const openEdit = (s: WfSchedule) => {
    setEditing(s); setName(s.name); setDocIds(s.doc_ids || []); setEnabled(s.enabled)
    setT(parseCron(s.cron || (s.crons || [])[0] || '0 8 * * *'))
    setOpen(true)
  }

  const patch = (p: Partial<T>) => setT((x) => ({ ...x, ...p }))

  const save = async () => {
    setSaving(true)
    try {
      const body = { name, crons: [toCron(t)], doc_ids: docIds, enabled }
      if (editing) await wfApi.saveSchedule(editing.id, body)
      else await wfApi.createSchedule(body)
      setOpen(false)
      await load()
      message.success('已保存')
    } catch (e) { message.error(err(e)) } finally { setSaving(false) }
  }

  const columns: ColumnsType<WfSchedule> = [
    { title: '名称', dataIndex: 'name' },
    { title: '运行时间', dataIndex: 'cron', width: 170, render: (v) => <Tag>{cronText(v)}</Tag> },
    { title: '下次触发', dataIndex: 'next', width: 170, render: (v: string[]) => (v?.length ? v[0] : '—') },
    {
      title: '挂载文档', dataIndex: 'doc_names',
      render: (v: string[]) => (v?.length
        ? <Space wrap size={4}>{v.map((x) => <Tag key={x} color="blue">{x}</Tag>)}</Space>
        : <Text type="secondary">未挂载</Text>),
    },
    {
      title: '启用', dataIndex: 'enabled', width: 80,
      render: (v, r) => (
        <Switch size="small" checked={v} onChange={async (on) => {
          try { await wfApi.saveSchedule(r.id, { enabled: on }); await load() } catch (e) { message.error(err(e)) }
        }} />
      ),
    },
    {
      title: '操作', width: 190,
      render: (_, r) => (
        <Space size="middle">
          <a onClick={() => openEdit(r)}>编辑</a>
          <Popconfirm title="立刻按这个方案跑一遍？" onConfirm={async () => {
            try {
              const out = await wfApi.runSchedule(r.id) as { docs?: { doc: string; status: string; hit_n?: number }[] }
              const list = (out?.docs || []).map((x) => `${x.doc}:${x.status}${x.hit_n !== undefined ? '(' + x.hit_n + ')' : ''}`).join('；')
              message.success(list || '该方案下没有启用的文档')
            } catch (e) { message.error(err(e)) }
          }}><a>立即运行</a></Popconfirm>
          <Popconfirm title={`删除「${r.name}」？`} description="还挂着文档时不能删" onConfirm={async () => {
            try { await wfApi.deleteSchedule(r.id); await load(); message.success('已删除') } catch (e) { message.error(err(e)) }
          }}><a>删除</a></Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card title="定时任务" extra={<Space>
      <Button onClick={() => void load()}>刷新</Button>
      <Button type="primary" onClick={openNew}>新建定时方案</Button>
    </Space>}>
      <Table rowKey="id" size="small" dataSource={list} columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有定时方案。不建方案就不会自动跑。" /> }} />

      <Modal open={open} width={620} destroyOnClose
        title={editing ? '修改定时方案' : '新建定时方案'}
        onCancel={() => setOpen(false)} onOk={() => void save()} okText="保存" confirmLoading={saving}>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <div style={{ marginBottom: 4 }}>名称</div>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例：每天八点跑预算预警" />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>什么时候跑（一个方案一个时间；要 8 点、9 点各跑一次就建两个方案，各选同一份文档）</div>
            <Space wrap>
              <Select value={t.unit} onChange={(v) => patch({ unit: v })} style={{ width: 120 }} options={[
                { value: 'day', label: '每天' }, { value: 'week', label: '每周' },
                { value: 'month', label: '每月' }, { value: 'minute', label: '每 N 分钟' },
                { value: 'hour', label: '每 N 小时' }]} />
              {(t.unit === 'minute' || t.unit === 'hour') && (
                <><span>每</span><InputNumber min={1} max={59} value={t.n} onChange={(v) => patch({ n: Number(v) || 1 })} /><span>个</span></>
              )}
              {t.unit !== 'minute' && (
                <><InputNumber min={0} max={23} value={t.hh} onChange={(v) => patch({ hh: Number(v) || 0 })} /><span>时</span>
                  <InputNumber min={0} max={59} value={t.mm} onChange={(v) => patch({ mm: Number(v) || 0 })} /><span>分</span></>
              )}
              {t.unit === 'week' && (
                <Select value={t.dow} onChange={(v) => patch({ dow: v })} style={{ width: 100 }}
                  options={WEEK.map((l, idx) => ({ value: idx + 1, label: l }))} />
              )}
              {t.unit === 'month' && (
                <><span>每月</span><InputNumber min={1} max={28} value={t.dom} onChange={(v) => patch({ dom: Number(v) || 1 })} /><span>号</span></>
              )}
              <Text type="secondary">{cronText(toCron(t))}</Text>
            </Space>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>挂载文档（可多选；同一份文档也可以挂在别的方案上）</div>
            <Select mode="multiple" allowClear style={{ width: '100%' }} value={docIds} onChange={setDocIds}
              optionFilterProp="label" placeholder="选文档"
              options={docs.map((d) => ({
                value: d.id,
                label: `${d.name}（${d.work_type_label}）${d.enabled ? '' : ' — 已停用'}`,
                disabled: !d.enabled,
              }))} />
            <div style={{ marginTop: 4 }}>
              <Text type="secondary">停用的文档不能挂载；把文档停用会自动从所有方案上解挂。</Text>
            </div>
          </div>
          <div><span style={{ marginRight: 8 }}>启用</span><Switch checked={enabled} onChange={setEnabled} /></div>
        </Space>
      </Modal>
    </Card>
  )
}
// ══════════════════════════════════════════════ 3 运行记录
function RunsTab() {
  const [rows, setRows] = useState<WfRun[]>([])
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<WfRun | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try { setRows((await wfApi.runs()).items) } catch (e) { message.error(err(e)) } finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const columns: ColumnsType<WfRun> = [
    { title: '时间', dataIndex: 'started_at', width: 165, render: dt },
    { title: '文档', dataIndex: 'doc_name', render: (v, r) => <a onClick={async () => setDetail(await wfApi.runDetail(r.id))}>{v}</a> },
    { title: '触发', dataIndex: 'trigger', width: 90, render: (v, r) => <Tag color={r.dry_run ? 'purple' : v === 'cron' ? 'blue' : 'default'}>{r.dry_run ? (v === 'test' ? '测试' : '试跑') : v}</Tag> },
    { title: '状态', dataIndex: 'status', width: 90, render: (v) => <Tag color={v === 'ok' ? 'green' : v === 'failed' ? 'red' : 'orange'}>{v}</Tag> },
    { title: '检查', dataIndex: 'checked_n', width: 70 },
    { title: '命中', dataIndex: 'hit_n', width: 70 },
    { title: '新增', dataIndex: 'new_n', width: 70 },
    { title: '关闭', dataIndex: 'closed_n', width: 70 },
    { title: '错误', dataIndex: 'error', render: (v) => (v ? <Text type="danger">{String(v).slice(0, 50)}</Text> : '—') },
  ]

  const snap = detail?.doc_snapshot || {}
  const secs = detail?.sections || []

  return (
    <Card title="运行记录" extra={<Button onClick={() => void load()}>刷新</Button>}>
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有跑过" /> }} />

      <Drawer open={!!detail} width={980} onClose={() => setDetail(null)} destroyOnClose
        title={detail ? `${detail.doc_name} · 第 ${detail.doc_version} 版 · ${detail.status}` : ''}>
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }}>
            {detail.dry_run && (
              <Alert type="info" showIcon message="这一轮是测试/试跑：结果表与事项表都没有写。" />
            )}
            <Descriptions size="small" column={4} items={[
              { key: 't', label: '触发', children: detail.trigger },
              { key: 'c', label: '检查', children: detail.checked_n },
              { key: 'h', label: '命中', children: detail.hit_n },
              { key: 'n', label: '新增', children: detail.new_n },
              { key: 'x', label: '关闭', children: detail.closed_n },
              { key: 'as', label: '数据截止', children: fmt(detail.data_as_of) },
              { key: 's', label: '开始', children: dt(detail.started_at) },
              { key: 'e', label: '结束', children: dt(detail.finished_at) },
            ]} />
            {detail.error && <Alert type="error" message={detail.error} />}

            <Collapse defaultActiveKey={['doc']} items={[
              {
                key: 'doc',
                label: '这一轮用的文档（对照模型实际做的事）',
                children: (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {secs.map((s) => (
                      <div key={s.key}>
                        <Text strong>{s.title}</Text>
                        <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                          {snap[s.key] || <Text type="secondary">（这一节当时是空的）</Text>}
                        </Paragraph>
                      </div>
                    ))}
                  </Space>
                ),
              },
              {
                key: 'logs',
                label: '模型实际做的事（每条 SQL、每次落库、报错）',
                children: <LogsTable logs={detail.logs} />,
              },
            ]} />
          </Space>
        )}
      </Drawer>
    </Card>
  )
}

// ══════════════════════════════════════════════ 4 事项
function ItemsTab() {
  const [rows, setRows] = useState<WfItem[]>([])
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<string | undefined>('open')
  const [detail, setDetail] = useState<WfItem | null>(null)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true)
    try { setRows((await wfApi.items({ status })).items) } catch (e) { message.error(err(e)) } finally { setLoading(false) }
  }, [status])
  useEffect(() => { void load() }, [load])

  const columns: ColumnsType<WfItem> = [
    { title: '档位', dataIndex: 'tier', width: 70, render: (v) => (v && TIER[v] ? <Tag color={TIER[v].color}>{TIER[v].text}</Tag> : '—') },
    { title: '对象', dataIndex: 'object_name', width: 160 },
    { title: '事项', dataIndex: 'title', render: (v, r) => <a onClick={async () => setDetail(await wfApi.item(r.id))}>{v}</a> },
    { title: '当前值', dataIndex: 'value', width: 100, render: fmt },
    { title: '阈值', dataIndex: 'threshold', width: 90, render: fmt },
    { title: '状态', dataIndex: 'status', width: 100, render: (v, r) => <Tag color={v === 'open' ? 'red' : 'default'}>{v === 'open' ? '未关闭' : (r.close_reason || '已关闭')}</Tag> },
    { title: '首见', dataIndex: 'first_seen', width: 120, render: (v) => (v ? String(v).slice(0, 10) : '—') },
    { title: '最近', dataIndex: 'last_seen', width: 120, render: (v) => (v ? String(v).slice(0, 10) : '—') },
    { title: '次数', dataIndex: 'seen_times', width: 60 },
  ]

  return (
    <Card title="事项" extra={<Space>
      <Select allowClear placeholder="状态" value={status} onChange={setStatus} style={{ width: 130 }}
        options={[{ value: 'open', label: '未关闭' }, { value: 'closed', label: '已关闭' }]} />
      <Button onClick={() => void load()}>刷新</Button>
    </Space>}>
      <Table rowKey="id" size="small" loading={loading} dataSource={rows} columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="暂时没有问题" /> }} />
      <Drawer open={!!detail} width={720} title={detail?.title} onClose={() => setDetail(null)} destroyOnClose
        extra={detail && (
          <Space>
            <Button onClick={async () => {
              try { const r = await wfApi.itemChat(detail.id); message.success('会话：' + r.session_id); navigate('/agent') } catch (e) { message.error(err(e)) }
            }}>进对话</Button>
            <Popconfirm title="关闭这条事项？" onConfirm={async () => {
              try { await wfApi.closeItem(detail.id, '人工关闭'); setDetail(null); await load(); message.success('已关闭') } catch (e) { message.error(err(e)) }
            }}><Button danger>关闭</Button></Popconfirm>
          </Space>
        )}>
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Descriptions size="small" column={2} items={[
              { key: 'o', label: '对象', children: `${detail.object_name}（${detail.object_key}）` },
              { key: 'd', label: '来源文档', children: detail.doc_name || detail.doc_id },
              { key: 'v', label: '当前值 / 阈值', children: `${fmt(detail.value)} / ${fmt(detail.op)} ${fmt(detail.threshold)}` },
              { key: 't', label: '档位', children: detail.tier || '—' },
              { key: 'r', label: '数据可靠', children: detail.reliable ? '是' : '否（有降级）' },
              { key: 's', label: '首见 / 最近 / 次数', children: `${dt(detail.first_seen)} / ${dt(detail.last_seen)} / ${detail.seen_times}` },
            ]} />
            <Card size="small" title="明细">
              <Descriptions size="small" column={2}
                items={Object.entries(detail.detail || {}).map(([k, v]) => ({ key: k, label: k, children: fmt(v) }))} />
            </Card>
            <Card size="small" title="依据">
              <Descriptions size="small" column={2}
                items={Object.entries(detail.evidence || {}).map(([k, v]) => ({ key: k, label: k, children: fmt(v) }))} />
            </Card>
            <Card size="small" title="历史（最近 30 轮）">
              <Table size="small" rowKey={(r) => String(r.at) + String(r.value)} dataSource={detail.history || []}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                columns={[
                  { title: '时间', dataIndex: 'at', render: dt },
                  { title: '值', dataIndex: 'value', render: fmt },
                  { title: '阈值', dataIndex: 'threshold', render: fmt },
                  { title: '档位', dataIndex: 'tier', render: fmt },
                  { title: '命中', dataIndex: 'hit', render: (v: boolean) => (v ? <Tag color="red">命中</Tag> : <Tag>未命中</Tag>) },
                ]} />
            </Card>
            <Card size="small" title="发送记录">
              <Table size="small" rowKey="id" dataSource={detail.notifies || []} pagination={false}
                columns={[
                  { title: '收件人', render: (_, r) => `${r.name || ''} ${r.login}` },
                  { title: '内容', dataIndex: 'message', render: (m: string) => <span style={{ whiteSpace: 'pre-wrap' }}>{m || '—'}</span> },
          { title: '通道', dataIndex: 'channel' },
                  { title: '依据', dataIndex: 'reason' },
                  { title: '时间', dataIndex: 'sent_at', render: dt },
                ]} />
            </Card>
          </Space>
        )}
      </Drawer>
    </Card>
  )
}

// ══════════════════════════════════════════════ 5 发送记录
function NotifyTab() {
  const [rows, setRows] = useState<any[]>([])
  const load = useCallback(async () => {
    try { setRows((await wfApi.notifications()).items) } catch (e) { message.error(err(e)) }
  }, [])
  useEffect(() => { void load() }, [load])
  return (
    <Card title="发送记录" extra={<Button onClick={() => void load()}>刷新</Button>}>
      <Table rowKey="id" size="small" dataSource={rows} pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有发过" /> }}
        columns={[
          { title: '事项', dataIndex: 'item_id', width: 80 },
          { title: '收件人', render: (_, r) => `${r.name || ''} ${r.login}` },
          { title: '内容', dataIndex: 'message', render: (m: string) => <span style={{ whiteSpace: 'pre-wrap' }}>{m || '—'}</span> },
          { title: '通道', dataIndex: 'channel', width: 90 },
          { title: '依据', dataIndex: 'reason' },
          { title: '时间', dataIndex: 'sent_at', width: 165, render: dt },
          { title: '结果', dataIndex: 'ok', width: 80, render: (v) => (v ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>) },
        ]} />
    </Card>
  )
}

export default function Workflows() {
  const [tab, setTab] = useState('docs')
  const [docs, setDocs] = useState<WfDoc[]>([])

  const reloadDocs = useCallback(async () => {
    try { setDocs((await wfApi.docs()).items) } catch (e) { message.error(err(e)) }
  }, [])
  useEffect(() => { void reloadDocs() }, [reloadDocs, tab])

  return (
    <div style={{ padding: 16 }}>
      <Title level={4} style={{ marginTop: 0 }}>工作流</Title>
      <Paragraph type="secondary" style={{ marginBottom: 12 }}>
        规则写在文档里，代码里没有业务口径；定时方案到点按文档跑一轮。
      </Paragraph>
      <Tabs activeKey={tab} onChange={setTab} destroyInactiveTabPane items={[
        { key: 'docs', label: '规则文档', children: <DocsTab /> },
        { key: 'schedules', label: '定时任务', children: <SchedulesTab docs={docs} /> },
        { key: 'runs', label: '运行记录', children: <RunsTab /> },
        { key: 'items', label: '事项', children: <ItemsTab /> },
        { key: 'notify', label: '发送记录', children: <NotifyTab /> },
      ]} />
    </div>
  )
}

