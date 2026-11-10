import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Divider,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Switch,
  Typography,
  message,
} from 'antd'
import { http } from '../api/client'

const { Text } = Typography

export type ParsedMust = { col: string; op: string; value: string | string[] | number }
export type ParsedBase = {
  table_name: string
  column_name: string
  agg: string
  scope_col: string
}
export type ParsedMetric = {
  code: string
  label: string
  kind: 'base' | 'derived'
  expr: string
  unit: string
  digits: number
  must: ParsedMust[]
  bases: ParsedBase[]
}

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

type MustRaw = { col: string; op: string; value: string | string[] | number }
type BaseRaw = {
  table_name: string
  column_name: string
  agg: string
  scope_col: string
  must?: MustRaw[]
  sort_key?: string
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

export const mustText = (x: MustRaw) => {
  const v = Array.isArray(x.value) ? x.value.join(',') : String(x.value ?? '')
  return x.op === 'in' ? `${x.col} in ${v}` : `${x.col}${x.op}${v}`
}

const emptyBase = (): BaseEdit => ({
  table_name: '',
  column_name: '',
  agg: 'sum',
  scope_col: '',
  sort_key: '',
  must: [],
})

const toMustEdit = (x: MustRaw): MustEdit => ({
  col: x.col || '',
  op: x.op || '=',
  value: Array.isArray(x.value) ? x.value.join(',') : String(x.value ?? ''),
})

export default function CaliberMetricDrawer({
  seed,
  onClose,
  onSaved,
}: {
  seed: ParsedMetric
  onClose: () => void
  onSaved: () => void
}) {
  const [edit, setEdit] = useState<Edit | null>(null)
  const [mode, setMode] = useState<'base' | 'expr'>('base')
  const [saving, setSaving] = useState(false)
  const [tables, setTables] = useState<string[]>([])
  const [cols, setCols] = useState<Record<string, string[]>>({})
  const [siblings, setSiblings] = useState<ParsedMetric[]>([])
  const [pick, setPick] = useState<string | undefined>(undefined)

  const ensureCols = async (table: string) => {
    if (!table || cols[table]) return
    try {
      const r: any = await http.get('/caliber/columns', { params: { table } })
      setCols((v) => ({ ...v, [table]: r.columns || [] }))
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  useEffect(() => {
    const init = async () => {
      let rows: any[] = []
      try {
        const r: any = await http.get('/caliber/metrics')
        rows = r.metrics || []
        setSiblings(rows)
      } catch (e) {
        message.error((e as Error).message)
      }
      try {
        const r: any = await http.get('/caliber/tables')
        setTables(r.tables || [])
      } catch (e) {
        message.error((e as Error).message)
      }

      const m = rows.find((x: any) => x.code === seed.code)
      const rawBases: BaseRaw[] = m ? m.bases || [] : seed.bases || []
      const seedMust = m ? [] : seed.must || []
      const bases: BaseEdit[] = rawBases.length
        ? rawBases.map((b, i) => ({
            table_name: b.table_name || '',
            column_name: b.column_name || '',
            agg: b.agg || 'sum',
            scope_col: b.scope_col || '',
            sort_key: b.sort_key || '',
            must: [
              ...(b.must || []).map(toMustEdit),
              ...(i === 0 ? seedMust.map(toMustEdit) : []),
            ],
          }))
        : seed.kind === 'derived'
          ? []
          : [{ ...emptyBase(), must: seedMust.map(toMustEdit) }]

      const expr = String((m ? m.expr : seed.expr) || '').trim()
      setEdit({
        code: seed.code,
        label: m?.label || seed.label || '',
        unit: m?.unit || seed.unit || '',
        digits: m?.digits ?? seed.digits ?? 2,
        direction: m?.direction || '',
        expr,
        enabled: m ? m.enabled !== false : true,
        notes: m?.notes || '',
        bases,
        isNew: !m,
      })
      setMode(expr ? 'expr' : 'base')
      bases.forEach((b) => {
        void ensureCols(b.table_name)
      })
    }
    void init()
  }, [])

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
        onSaved()
        onClose()
      } else {
        message.error(r?.error || '保存失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Drawer
      title={edit ? `改口径　${edit.code}${edit.isNew ? '（新建）' : ''}` : '改口径'}
      width={720}
      open
      onClose={onClose}
      extra={
        <Button type="primary" loading={saving} disabled={!edit} onClick={() => void save()}>
          保存
        </Button>
      }
    >
      {!edit ? (
        <Text type="secondary">读取中…</Text>
      ) : (
        <>
          <Card size="small" title="基本信息" style={{ marginBottom: 12 }}>
            <Space direction="vertical" style={{ width: '100%' }} size={10}>
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
                    <Switch checked={edit.enabled} onChange={(v) => patch({ enabled: v })} />
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
                    options={siblings
                      .filter((m) => m.code !== edit.code)
                      .map((m) => ({ value: m.code, label: `${m.code}（${m.label}）` }))}
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
                {edit.bases.length === 0 && <Empty description="取数至少配一条，不配就是空口径" />}
                {edit.bases.map((b, i) => (
                  <Card
                    key={i}
                    size="small"
                    type="inner"
                    title={`取数 ${i + 1}`}
                    extra={
                      <Button size="small" type="text" danger onClick={() => delBase(i)}>
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
                        必需过滤：机器靠这几条判断 SQL 能不能放行；值多个用逗号分隔，年份这种直接写
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
                          <Button size="small" type="text" danger onClick={() => delMust(i, k)}>
                            删
                          </Button>
                        </Space>
                      ))}
                    </Space>
                    <Button size="small" style={{ marginTop: 6 }} onClick={() => addMust(i)}>
                      加一条必需过滤
                    </Button>
                  </Card>
                ))}
                <Button onClick={addBase}>加一条取数</Button>
              </Space>
            )}
          </Card>
        </>
      )}
    </Drawer>
  )
}
