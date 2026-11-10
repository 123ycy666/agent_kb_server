import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Divider,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { http } from '../api/client'

const { Title, Text } = Typography

const CODE_RE = /^[a-z][a-z0-9_]{1,63}$/

const AGGS = [
  { value: 'sum', label: '求和' },
  { value: 'count', label: '计数' },
  { value: 'max', label: '最大值' },
  { value: 'min', label: '最小值' },
  { value: 'last', label: '末值' },
]

const OPS = [
  { value: '=', label: '等于' },
  { value: '!=', label: '不等于' },
  { value: 'in', label: '属于（任意一个）' },
]

const DIR: Record<string, string> = {
  bigger_better: '越大越好',
  smaller_better: '越小越好',
}

type MustRaw = { col: string; op: string; value: string | string[] | number }
type BaseRaw = {
  table_name: string
  column_name: string
  agg: string
  scope_col: string
  must?: MustRaw[]
  sort_key?: string
}
type Metric = {
  code: string
  label: string
  unit: string
  digits: number
  direction: string
  expr: string
  enabled: boolean
  version_no: number
  notes: string
  updated_by?: string
  updated_at?: string
  bases?: BaseRaw[]
}
type MustEdit = { col: string; op: string; value: string }
type BaseEdit = {
  table_name: string
  column_name: string
  agg: string
  scope_col: string
  sort_key: string
  must: MustEdit[]
}
type Edit = {
  code: string
  label: string
  unit: string
  digits: number
  direction: string
  expr: string
  enabled: boolean
  notes: string
  bases: BaseEdit[]
  isNew: boolean
}
type Preview = {
  sql?: string[]
  missing?: string[]
  expanded_expr?: string
  unit?: string
  digits?: number
}
type VersionRow = {
  version_no: number
  saved_by?: string
  saved_at?: string
  body?: unknown
}
type CheckRes = {
  ok?: boolean
  missing?: { code?: string; col?: string; why?: string }[]
  hint?: string
}

const mustText = (x: MustRaw) => {
  const v = Array.isArray(x.value) ? x.value.join(',') : String(x.value ?? '')
  return x.op === 'in' ? `${x.col} in ${v}` : `${x.col}${x.op}${v}`
}

const fmtAt = (v?: string) => (v ? String(v).slice(0, 19).replace('T', ' ') : '—')

const pre: CSSProperties = {
  background: '#fafafa',
  border: '1px solid #f0f0f0',
  padding: 8,
  margin: '6px 0',
  fontSize: 12,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
}

const emptyBase = (): BaseEdit => ({
  table_name: '',
  column_name: '',
  agg: 'sum',
  scope_col: '',
  sort_key: '',
  must: [],
})

export default function Caliber() {
  const [rows, setRows] = useState<Metric[]>([])
  const [loading, setLoading] = useState(true)
  const [tables, setTables] = useState<string[]>([])
  const [cols, setCols] = useState<Record<string, string[]>>({})

  const [edit, setEdit] = useState<Edit | null>(null)
  const [mode, setMode] = useState<'base' | 'expr'>('base')
  const [tab, setTab] = useState('edit')
  const [saving, setSaving] = useState(false)
  const [pick, setPick] = useState<string | undefined>(undefined)

  const [prevYear, setPrevYear] = useState(String(new Date().getFullYear()))
  const [prevScope, setPrevScope] = useState<number | null>(null)
  const [prevBusy, setPrevBusy] = useState(false)
  const [prev, setPrev] = useState<Preview | null>(null)
  const [prevErr, setPrevErr] = useState('')

  const [hisOpen, setHisOpen] = useState(false)
  const [hisCode, setHisCode] = useState('')
  const [hisRows, setHisRows] = useState<VersionRow[]>([])
  const [hisBusy, setHisBusy] = useState(false)

  const [ckText, setCkText] = useState('')
  const [ckBusy, setCkBusy] = useState(false)
  const [ckRes, setCkRes] = useState<CheckRes | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const r: any = await http.get('/caliber/metrics')
      setRows(r.metrics || [])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const loadTables = async () => {
    try {
      const r: any = await http.get('/caliber/tables')
      setTables(r.tables || [])
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  useEffect(() => {
    void load()
    void loadTables()
  }, [])

  const ensureCols = async (table: string) => {
    if (!table || cols[table]) return
    try {
      const r: any = await http.get('/caliber/columns', { params: { table } })
      setCols((m) => ({ ...m, [table]: r.columns || [] }))
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const patch = (p: Partial<Edit>) => setEdit((e) => (e ? { ...e, ...p } : e))

  const setBase = (i: number, p: Partial<BaseEdit>) =>
    setEdit((e) =>
      e ? { ...e, bases: e.bases.map((b, j) => (j === i ? { ...b, ...p } : b)) } : e
    )

  const addBase = () => setEdit((e) => (e ? { ...e, bases: [...e.bases, emptyBase()] } : e))

  const delBase = (i: number) =>
    setEdit((e) => (e ? { ...e, bases: e.bases.filter((_, j) => j !== i) } : e))

  const addMust = (i: number) =>
    setEdit((e) =>
      e
        ? {
            ...e,
            bases: e.bases.map((b, j) =>
              j === i ? { ...b, must: [...b.must, { col: '', op: '=', value: '' }] } : b
            ),
          }
        : e
    )

  const setMust = (i: number, k: number, p: Partial<MustEdit>) =>
    setEdit((e) =>
      e
        ? {
            ...e,
            bases: e.bases.map((b, j) =>
              j === i
                ? { ...b, must: b.must.map((m, n) => (n === k ? { ...m, ...p } : m)) }
                : b
            ),
          }
        : e
    )

  const delMust = (i: number, k: number) =>
    setEdit((e) =>
      e
        ? {
            ...e,
            bases: e.bases.map((b, j) =>
              j === i ? { ...b, must: b.must.filter((_, n) => n !== k) } : b
            ),
          }
        : e
    )

  const resetDrawer = () => {
    setTab('edit')
    setPrev(null)
    setPrevErr('')
    setPick(undefined)
    setPrevYear(String(new Date().getFullYear()))
    setPrevScope(null)
  }

  const openEdit = (m: Metric) => {
    setEdit({
      code: m.code,
      label: m.label || '',
      unit: m.unit || '',
      digits: m.digits ?? 2,
      direction: m.direction || '',
      expr: m.expr || '',
      enabled: m.enabled !== false,
      notes: m.notes || '',
      bases: (m.bases || []).map((b) => ({
        table_name: b.table_name || '',
        column_name: b.column_name || '',
        agg: b.agg || 'sum',
        scope_col: b.scope_col || '',
        sort_key: b.sort_key || '',
        must: (b.must || []).map((x) => ({
          col: x.col || '',
          op: x.op || '=',
          value: Array.isArray(x.value) ? x.value.join(',') : String(x.value ?? ''),
        })),
      })),
      isNew: false,
    })
    setMode(m.expr ? 'expr' : 'base')
    resetDrawer()
    ;(m.bases || []).forEach((b) => {
      void ensureCols(b.table_name)
    })
  }

  const openNew = () => {
    setEdit({
      code: '',
      label: '',
      unit: '',
      digits: 2,
      direction: '',
      expr: '',
      enabled: true,
      notes: '',
      bases: [emptyBase()],
      isNew: true,
    })
    setMode('base')
    resetDrawer()
  }

  const appendCode = (code: string) =>
    setEdit((e) => {
      if (!e) return e
      const cur = e.expr.trim()
      return { ...e, expr: cur ? `${cur} ${code}` : code }
    })

  const appendOp = (op: string) =>
    setEdit((e) => {
      if (!e) return e
      let cur = e.expr.trim()
      if (!cur) return e
      if ('+-*/'.includes(cur.slice(-1))) cur = cur.slice(0, -1).trim()
      return { ...e, expr: `${cur} ${op}` }
    })

  const save = async () => {
    if (!edit) return
    if (edit.isNew && !CODE_RE.test(edit.code)) {
      message.error('代号要小写字母开头、只含小写字母/数字/下划线，2–64 位')
      return
    }
    setSaving(true)
    try {
      const body = {
        label: edit.label,
        unit: edit.unit,
        digits: edit.digits,
        direction: edit.direction,
        expr: mode === 'expr' ? edit.expr.trim() : '',
        enabled: edit.enabled,
        notes: edit.notes,
        bases:
          mode === 'base'
            ? edit.bases.map((b) => ({
                table_name: b.table_name,
                column_name: b.column_name,
                agg: b.agg || 'sum',
                scope_col: b.scope_col || '',
                must: b.must.map((m) => ({
                  col: m.col,
                  op: m.op,
                  value:
                    m.op === 'in' || m.value.includes(',')
                      ? m.value
                          .split(',')
                          .map((x) => x.trim())
                          .filter(Boolean)
                      : m.value,
                })),
                sort_key: b.sort_key || '',
              }))
            : [],
      }
      const r: any = await http.put(`/caliber/metrics/${edit.code}`, body)
      if (r?.ok) {
        message.success(r.version_no ? `已保存，第 ${r.version_no} 版` : '已保存')
        setEdit(null)
        await load()
      } else {
        message.error(r?.error || '保存失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (m: Metric) => {
    try {
      const r: any = await http.post(`/caliber/metrics/${m.code}/toggle`, {
        enabled: !m.enabled,
      })
      if (r?.ok) {
        message.success(m.enabled ? '已停用' : '已启用')
        await load()
      } else {
        message.error(r?.error || '操作失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const openHistory = async (code: string) => {
    setHisCode(code)
    setHisRows([])
    setHisOpen(true)
    setHisBusy(true)
    try {
      const r: any = await http.get(`/caliber/metrics/${code}/history`)
      setHisRows(r.versions || [])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setHisBusy(false)
    }
  }

  const genPreview = async () => {
    if (!edit || edit.isNew) return
    setPrevBusy(true)
    setPrev(null)
    setPrevErr('')
    try {
      const r: any = await http.post(`/caliber/metrics/${edit.code}/preview`, {
        year: prevYear.trim(),
        scope_id: prevScope == null ? null : Number(prevScope),
      })
      if (r?.error) setPrevErr(String(r.error))
      else setPrev(r as Preview)
    } catch (e) {
      setPrevErr((e as Error).message)
    } finally {
      setPrevBusy(false)
    }
  }

  const doCheck = async () => {
    if (!ckText.trim()) {
      message.error('先把 SQL 粘进来')
      return
    }
    setCkBusy(true)
    setCkRes(null)
    try {
      const r: any = await http.post('/caliber/check', { sql: ckText })
      setCkRes(r || { ok: false })
    } catch (e) {
      setCkRes({ ok: false, hint: (e as Error).message })
    } finally {
      setCkBusy(false)
    }
  }

  const columns: ColumnsType<Metric> = [
    {
      title: '代号',
      dataIndex: 'code',
      width: 220,
      render: (v: string) => <Text code>{v}</Text>,
    },
    { title: '中文名', dataIndex: 'label', width: 160 },
    {
      title: '类型',
      width: 80,
      render: (_: unknown, r: Metric) =>
        r.expr ? <Tag color="blue">派生</Tag> : <Tag>取数</Tag>,
    },
    {
      title: '算式',
      dataIndex: 'expr',
      width: 240,
      render: (v: string) => (v ? <Text code>{v}</Text> : '—'),
    },
    {
      title: '必需过滤',
      width: 280,
      render: (_: unknown, r: Metric) => {
        const chips = Array.from(
          new Set((r.bases || []).flatMap((b) => (b.must || []).map(mustText)))
        )
        return chips.length ? (
          <Space size={4} wrap>
            {chips.map((c) => (
              <Tag key={c}>{c}</Tag>
            ))}
          </Space>
        ) : (
          '—'
        )
      },
    },
    { title: '单位', dataIndex: 'unit', width: 80, render: (v: string) => v || '—' },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 110,
      render: (v: string) => (v ? DIR[v] || v : '—'),
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 85,
      render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
    },
    {
      title: '操作',
      width: 180,
      render: (_: unknown, r: Metric) => (
        <Space size="middle">
          <a onClick={() => openEdit(r)}>编辑</a>
          <a onClick={() => void openHistory(r.code)}>历史</a>
          {r.enabled ? (
            <Popconfirm
              title={`停用「${r.label || r.code}」？`}
              description="停用后闸1 不再按它校验，引用它的派生算式也出不来数。"
              okText="确定停用"
              cancelText="再想想"
              onConfirm={() => void toggle(r)}
            >
              <a>停用</a>
            </Popconfirm>
          ) : (
            <a onClick={() => void toggle(r)}>启用</a>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 16 }} align="center" wrap>
        <Title level={4} style={{ margin: 0 }}>
          指标契约
        </Title>
        <Text type="secondary">
          口径做成数据：取数过滤与派生算式都在这里配，闸1 按它校验模型写的 SQL
        </Text>
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>
          刷新
        </Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
          新增指标
        </Button>
      </Space>

      <Collapse
        style={{ marginBottom: 16 }}
        items={[
          {
            key: 'check',
            label: '验证一段 SQL',
            children: (
              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                <Input.TextArea
                  rows={6}
                  value={ckText}
                  style={{ fontSize: 12 }}
                  placeholder="把准备执行的 SQL 粘进来，看闸1 会不会因为缺必需过滤拒绝它"
                  onChange={(e) => setCkText(e.target.value)}
                />
                <Space>
                  <Button type="primary" loading={ckBusy} onClick={() => void doCheck()}>
                    验证
                  </Button>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    只按指标契约做静态检查，不连业务库
                  </Text>
                </Space>
                {ckRes &&
                  (ckRes.ok ? (
                    <Alert type="success" showIcon message="这段 SQL 能过闸1" />
                  ) : (
                    <Space direction="vertical" style={{ width: '100%' }} size={6}>
                      {ckRes.hint && <Alert type="warning" showIcon message={ckRes.hint} />}
                      {(ckRes.missing || []).length === 0 ? (
                        <Text type="secondary">没给出缺失项</Text>
                      ) : (
                        (ckRes.missing || []).map((m, i) => (
                          <div key={i}>
                            <Tag color="red">{m.code || '—'}</Tag>
                            <Text>{m.col || '—'}</Text>
                            <Text type="secondary">　{m.why || ''}</Text>
                          </div>
                        ))
                      )}
                    </Space>
                  ))}
              </Space>
            ),
          },
        ]}
      />

      <Table
        rowKey="code"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        scroll={{ x: 'max-content' }}
        locale={{ emptyText: <Empty description="还没有指标，先新增一条" /> }}
      />

      <Drawer
        title={edit ? `${edit.isNew ? '新增指标' : '编辑指标'}　${edit.code || '未填代号'}` : ''}
        width={720}
        open={!!edit}
        onClose={() => setEdit(null)}
        extra={
          <Button type="primary" loading={saving} onClick={() => void save()}>
            保存
          </Button>
        }
      >
        {!edit ? null : (
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              {
                key: 'edit',
                label: '编辑',
                children: (
                  <>
                    <Card size="small" title="基本信息" style={{ marginBottom: 12 }}>
                      <Space direction="vertical" style={{ width: '100%' }} size={10}>
                        <div>
                          <div style={{ marginBottom: 4 }}>代号</div>
                          <Input
                            value={edit.code}
                            disabled={!edit.isNew}
                            placeholder="例：defect_work_order_count"
                            onChange={(e) => patch({ code: e.target.value.trim() })}
                          />
                          {edit.isNew && (
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              小写字母开头，只含小写字母、数字、下划线，2–64 位；库里没有这个代号就新建
                            </Text>
                          )}
                        </div>
                        <Space wrap size={12} align="end">
                          <div>
                            <div style={{ marginBottom: 4 }}>中文名</div>
                            <Input
                              value={edit.label}
                              style={{ width: 220 }}
                              placeholder="例：缺陷治理工单数量"
                              onChange={(e) => patch({ label: e.target.value })}
                            />
                          </div>
                          <div>
                            <div style={{ marginBottom: 4 }}>单位</div>
                            <Input
                              value={edit.unit}
                              style={{ width: 90 }}
                              placeholder="元 / 件 / %"
                              onChange={(e) => patch({ unit: e.target.value })}
                            />
                          </div>
                          <div>
                            <div style={{ marginBottom: 4 }}>小数位</div>
                            <InputNumber
                              min={0}
                              max={6}
                              value={edit.digits}
                              style={{ width: 80 }}
                              onChange={(v) => patch({ digits: v ?? 0 })}
                            />
                          </div>
                          <div>
                            <div style={{ marginBottom: 4 }}>方向</div>
                            <Select
                              value={edit.direction}
                              style={{ width: 130 }}
                              onChange={(v) => patch({ direction: v })}
                              options={[
                                { value: '', label: '（不指定）' },
                                { value: 'bigger_better', label: '越大越好' },
                                { value: 'smaller_better', label: '越小越好' },
                              ]}
                            />
                          </div>
                          <div>
                            <div style={{ marginBottom: 4 }}>状态</div>
                            <Space size={6}>
                              <Switch
                                checked={edit.enabled}
                                onChange={(v) => patch({ enabled: v })}
                              />
                              <Text type="secondary">{edit.enabled ? '启用' : '停用'}</Text>
                            </Space>
                          </div>
                        </Space>
                        <div>
                          <div style={{ marginBottom: 4 }}>备注</div>
                          <Input.TextArea
                            value={edit.notes}
                            autoSize={{ minRows: 2, maxRows: 4 }}
                            placeholder="这条口径的业务含义、跟谁对过数"
                            onChange={(e) => patch({ notes: e.target.value })}
                          />
                        </div>
                      </Space>
                    </Card>

                    <Card size="small" title="怎么算">
                      <Radio.Group
                        value={mode}
                        style={{ marginBottom: 10 }}
                        onChange={(e) => setMode(e.target.value as 'base' | 'expr')}
                      >
                        <Radio.Button value="base">取数</Radio.Button>
                        <Radio.Button value="expr">派生</Radio.Button>
                      </Radio.Group>

                      {mode === 'expr' ? (
                        <Space direction="vertical" style={{ width: '100%' }} size={8}>
                          <Space wrap size={6}>
                            <Select
                              showSearch
                              value={pick}
                              style={{ width: 260 }}
                              placeholder="选一个已有指标"
                              optionFilterProp="label"
                              options={rows
                                .filter((m) => m.code !== edit.code)
                                .map((m) => ({
                                  value: m.code,
                                  label: `${m.code}（${m.label}）`,
                                }))}
                              onChange={(v) => {
                                appendCode(v)
                                setPick(undefined)
                              }}
                            />
                            {['+', '-', '*', '/'].map((op) => (
                              <Button key={op} onClick={() => appendOp(op)}>
                                {op}
                              </Button>
                            ))}
                            <Button onClick={() => patch({ expr: '' })}>清空</Button>
                          </Space>
                          <Input
                            readOnly
                            value={edit.expr}
                            style={{ fontFamily: 'monospace' }}
                            placeholder="用上面的指标代号和运算符拼，不能手输"
                          />
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            例：defect_work_order_budget / budget_allocated
                          </Text>
                        </Space>
                      ) : (
                        <Space direction="vertical" style={{ width: '100%' }} size={8}>
                          {edit.bases.length === 0 && (
                            <Empty description="取数至少配一条，不配就是空口径" />
                          )}
                          {edit.bases.map((b, i) => (
                            <Card
                              key={i}
                              size="small"
                              type="inner"
                              title={`取数 ${i + 1}`}
                              extra={
                                <Button
                                  size="small"
                                  type="text"
                                  danger
                                  onClick={() => delBase(i)}
                                >
                                  删
                                </Button>
                              }
                            >
                              <Space wrap size={8} align="end">
                                <div>
                                  <div style={{ marginBottom: 4 }}>表名</div>
                                  <Select
                                    showSearch
                                    style={{ width: 200 }}
                                    value={b.table_name || undefined}
                                    placeholder="选表"
                                    options={tables.map((t) => ({ value: t, label: t }))}
                                    onChange={(v) => {
                                      setBase(i, { table_name: v, column_name: '' })
                                      void ensureCols(v)
                                    }}
                                  />
                                </div>
                                <div>
                                  <div style={{ marginBottom: 4 }}>列名</div>
                                  <Select
                                    showSearch
                                    style={{ width: 170 }}
                                    value={b.column_name || undefined}
                                    placeholder={b.table_name ? '选列' : '先选表'}
                                    options={(cols[b.table_name] || []).map((c) => ({
                                      value: c,
                                      label: c,
                                    }))}
                                    onChange={(v) => setBase(i, { column_name: v })}
                                  />
                                </div>
                                <div>
                                  <div style={{ marginBottom: 4 }}>聚合</div>
                                  <Select
                                    style={{ width: 100 }}
                                    value={b.agg}
                                    options={AGGS}
                                    onChange={(v) => setBase(i, { agg: v })}
                                  />
                                </div>
                                <div>
                                  <div style={{ marginBottom: 4 }}>归属列</div>
                                  <Input
                                    style={{ width: 150 }}
                                    value={b.scope_col}
                                    placeholder="可留空"
                                    onChange={(e) => setBase(i, { scope_col: e.target.value })}
                                  />
                                </div>
                                <div>
                                  <div style={{ marginBottom: 4 }}>排序键</div>
                                  <Input
                                    style={{ width: 120 }}
                                    value={b.sort_key}
                                    placeholder="可留空"
                                    onChange={(e) => setBase(i, { sort_key: e.target.value })}
                                  />
                                </div>
                              </Space>

                              <Divider style={{ margin: '10px 0' }} />

                              <div style={{ marginBottom: 6 }}>
                                <Text type="secondary" style={{ fontSize: 12 }}>
                                  必需过滤：闸1 靠这几条判断能不能放行；值多个用逗号分隔，年份这种直接写
                                  $问题年份
                                </Text>
                              </div>
                              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                                {b.must.map((m, k) => (
                                  <Space key={k} wrap size={6}>
                                    <Input
                                      size="small"
                                      style={{ width: 140 }}
                                      value={m.col}
                                      placeholder="列名，如 year"
                                      onChange={(e) => setMust(i, k, { col: e.target.value })}
                                    />
                                    <Select
                                      size="small"
                                      style={{ width: 130 }}
                                      value={m.op}
                                      options={OPS}
                                      onChange={(v) => setMust(i, k, { op: v })}
                                    />
                                    <Input
                                      size="small"
                                      style={{ width: 200 }}
                                      value={m.value}
                                      placeholder="值，如 2,3 或 $问题年份"
                                      onChange={(e) => setMust(i, k, { value: e.target.value })}
                                    />
                                    <Button
                                      size="small"
                                      type="text"
                                      danger
                                      onClick={() => delMust(i, k)}
                                    >
                                      删
                                    </Button>
                                  </Space>
                                ))}
                              </Space>
                              <Button
                                size="small"
                                style={{ marginTop: 6 }}
                                onClick={() => addMust(i)}
                              >
                                加一条必需过滤
                              </Button>
                            </Card>
                          ))}
                          <Button onClick={addBase}>加一条取数</Button>
                        </Space>
                      )}
                    </Card>
                  </>
                ),
              },
              {
                key: 'preview',
                label: '预览',
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} size={12}>
                    <Alert
                      type="warning"
                      showIcon
                      message="这段 SQL 会被闸1 用来校验：缺必需过滤直接拒绝执行"
                    />
                    <Space wrap align="end">
                      <div>
                        <div style={{ marginBottom: 4 }}>年份</div>
                        <Input
                          value={prevYear}
                          style={{ width: 100 }}
                          onChange={(e) => setPrevYear(e.target.value)}
                        />
                      </div>
                      <div>
                        <div style={{ marginBottom: 4 }}>供电所 id</div>
                        <InputNumber
                          value={prevScope}
                          style={{ width: 150 }}
                          placeholder="可留空"
                          onChange={(v) => setPrevScope(v == null ? null : Number(v))}
                        />
                      </div>
                      <Button
                        type="primary"
                        loading={prevBusy}
                        disabled={edit.isNew}
                        onClick={() => void genPreview()}
                      >
                        生成
                      </Button>
                      {edit.isNew && <Text type="secondary">新指标保存后才能预览</Text>}
                    </Space>

                    {prevErr && <Alert type="error" showIcon message={prevErr} />}

                    {prev && (
                      <>
                        <Space wrap size={6}>
                          <Tag>算式 {prev.expanded_expr || '—'}</Tag>
                          <Tag>单位 {prev.unit || '—'}</Tag>
                          <Tag>小数位 {prev.digits ?? edit.digits}</Tag>
                        </Space>
                        {(prev.sql || []).length === 0 ? (
                          <Text type="secondary">没有生成 SQL</Text>
                        ) : (
                          (prev.sql || []).map((s, i) => (
                            <pre key={i} style={pre}>
                              {s}
                            </pre>
                          ))
                        )}
                        {(prev.missing || []).length > 0 && (
                          <Alert
                            type="warning"
                            showIcon
                            message={`缺必需过滤：${(prev.missing || []).join('、')}`}
                          />
                        )}
                      </>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Drawer>

      <Drawer
        title={`历史版本　${hisCode}`}
        width={720}
        open={hisOpen}
        onClose={() => setHisOpen(false)}
      >
        {hisBusy ? (
          <Text type="secondary">读取中…</Text>
        ) : hisRows.length === 0 ? (
          <Empty description="还没有历史版本" />
        ) : (
          <Space direction="vertical" style={{ width: '100%' }} size={10}>
            {hisRows.map((v) => (
              <Card
                key={v.version_no}
                size="small"
                title={`第 ${v.version_no} 版　${v.saved_by || '—'}　${fmtAt(v.saved_at)}`}
              >
                <pre style={pre}>{JSON.stringify(v.body ?? {}, null, 2)}</pre>
              </Card>
            ))}
          </Space>
        )}
      </Drawer>
    </div>
  )
}
