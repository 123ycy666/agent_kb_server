import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert, AutoComplete, Button, Card, Drawer, Empty, Input, InputNumber, Modal, Select,
  Space, Switch, Table, Tabs, Tag, Typography, message,
} from 'antd'
import { flowApi, blankSpec } from '../api/flows'
import type { ColumnMeta, Cond, FlowDetail, FlowError, RunResult, WfSpec } from '../api/flows'

const { Text } = Typography
const OP_LABEL: Record<string, string> = {
  isnull: '为空', notnull: '不为空', eq: '等于', ne: '不等于', gt: '大于', ge: '大于等于',
  lt: '小于', le: '小于等于', in: '属于', notin: '不属于', contains: '包含',
  starts: '开头是', ends: '结尾是',
}
const OP_OPTIONS = Object.entries(OP_LABEL).map(([value, label]) => ({ value, label }))
const NO_VALUE = new Set(['isnull', 'notnull'])

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v))

export default function FlowEditor({ flowId, onClose, onSaved }: {
  flowId: number
  onClose: () => void
  onSaved: () => void
}) {
  const [detail, setDetail] = useState<FlowDetail | null>(null)
  const [spec, setSpec] = useState<WfSpec>(blankSpec())
  const [name, setName] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [tab, setTab] = useState('source')
  const [errors, setErrors] = useState<FlowError[]>([])
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState<RunResult | null>(null)
  const [preview, setPreview] = useState<{
    ok?: boolean; errors?: FlowError[]; select_preview?: string; write_preview?: string
  } | null>(null)
  const [tables, setTables] = useState<Record<string, { name: string; comment: string; key: string[] }[]>>({})
  const [cols, setCols] = useState<Record<string, ColumnMeta[]>>({})
  const [tkeys, setTkeys] = useState<Record<string, string[]>>({})

  const loadTables = useCallback(async (db: string) => {
    if (tables[db]) return
    try {
      const r = await flowApi.metaTables(db)
      setTables((prev) => ({ ...prev, [db]: r.items }))
    } catch (e) { message.error((e as Error).message) }
  }, [tables])

  const loadCols = useCallback(async (db: string, table: string) => {
    const key = db + '/' + table
    if (!table || cols[key]) return
    try {
      const r = await flowApi.metaColumns(db, table)
      setCols((prev) => ({ ...prev, [key]: r.columns }))
      setTkeys((prev) => ({ ...prev, [key]: r.key || [] }))
    } catch (e) { message.error((e as Error).message) }
  }, [cols])

  useEffect(() => {
    void (async () => {
      try {
        const d = await flowApi.get(flowId)
        setDetail(d)
        setSpec(clone(d.spec))
        setName(d.name)
        setEnabled(d.enabled)
        setErrors(d.errors || [])
        void loadTables(d.spec.source.db)
        setSpec((s) => s)
      } catch (e) { message.error((e as Error).message) }
    })()
  }, [flowId, loadTables])

  useEffect(() => {
    void loadTables(spec.source.db)
    for (const t of spec.source.tables) void loadCols(spec.source.db, t.table)
    if (spec.write.table) void loadCols(spec.write.db, spec.write.table)
  }, [spec.source.db, spec.source.tables, spec.write.db, spec.write.table, loadCols, loadTables])

  const edit = (fn: (s: WfSpec) => void) => {
    setSpec((prev) => {
      const next = clone(prev)
      fn(next)
      return next
    })
  }

  const tableOpts = (db: string) => (tables[db] || []).map((t) => ({
    value: t.name,
    label: t.comment ? `${t.name}（${t.comment}）` : t.name,
  }))

  const colOpts = (db: string, table: string) =>
    (cols[db + '/' + table] || []).map((c) => ({
      value: c.name,
      label: c.comment ? `${c.name}（${c.comment}）` : c.name,
    }))

  const sourceRefs = useMemo(() => {
    const out: { value: string; label: string }[] = []
    const plain: Record<string, number> = {}
    for (const t of spec.source.tables) {
      for (const c of t.columns) {
        plain[c] = (plain[c] || 0) + 1
        out.push({ value: `${t.alias}.${c}`, label: `${t.alias}.${c}` })
      }
    }
    for (const [c, n] of Object.entries(plain)) {
      if (n === 1) out.push({ value: c, label: c })
    }
    return out
  }, [spec.source.tables])

  const resultNames = useMemo(() => {
    const names: string[] = []
    for (const g of spec.calc.grain) if (g.name) names.push(g.name)
    for (const f of spec.calc.fields) if (f.name) names.push(f.name)
    return names
  }, [spec.calc])

  const valueChoices = useMemo(
    () => [...resultNames.map((n) => ({ value: n, label: n })),
      ...sourceRefs.map((r) => ({ value: r.value, label: r.label })),
      { value: 'hit', label: 'hit（命中）' }, { value: 'tier', label: 'tier（档位）' }],
    [resultNames, sourceRefs],
  )

  const save = async () => {
    setSaving(true)
    try {
      const d = await flowApi.save(flowId, { name, spec, enabled })
      setDetail(d)
      setErrors(d.errors || [])
      message.success('已保存')
      onSaved()
      if (!(d.errors || []).length) setTab('source')
    } catch (e) {
      const err = e as Error & { code?: string }
      message.error(err.message)
      try {
        const fresh = await flowApi.get(flowId)
        setErrors(fresh.errors || [])
      } catch { /* 取不到就算了 */ }
    } finally { setSaving(false) }
  }

  const doCompile = async () => {
    setBusy('compile')
    try {
      const r = await flowApi.compile(flowId)
      setPreview(r)
      setErrors(r.errors || [])
      if (!r.ok) message.warning('还有槽位没填对')
    } catch (e) { message.error((e as Error).message) } finally { setBusy('') }
  }

  const doDryRun = async () => {
    setBusy('dry')
    try {
      const r = await flowApi.dryRun(flowId)
      setResult(r)
      onSaved()
    } catch (e) { message.error((e as Error).message) } finally { setBusy('') }
  }

  const errOf = (section: string) => errors.filter((x) => x.section === section)

  const sections = [
    {
      key: 'source', label: '① 取数',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <ErrBox list={errOf('source')} />
          <Card size="small" title="从哪个库取">
            <Space>
              <Select value={spec.source.db} style={{ width: 140 }}
                onChange={(v) => edit((s) => { s.source.db = v; s.source.tables = []; s.source.joins = [] })}
                options={[{ value: 'biz', label: '业务库' }, { value: 'fw', label: '预警库' }]} />
              <Text type="secondary">换库会清空已选的表</Text>
            </Space>
          </Card>

          <Card size="small" title="来源表" extra={
            <Button size="small" onClick={() => edit((s) => {
              s.source.tables.push({ table: '', alias: 't' + (s.source.tables.length + 1), columns: [] })
            })}>加一张表</Button>}>
            <Space direction="vertical" style={{ width: '100%' }}>
              {spec.source.tables.map((t, i) => (
                <Card key={i} size="small" type="inner"
                  title={<Space>
                    <Tag>{t.alias || '—'}</Tag>
                    <AutoComplete value={t.table} options={tableOpts(spec.source.db)} style={{ width: 320 }}
                      placeholder="选表" filterOption
                      onChange={(v) => edit((s) => { s.source.tables[i].table = v; s.source.tables[i].columns = [] })} />
                    <Button size="small" danger onClick={() => edit((s) => { s.source.tables.splice(i, 1) })}>删</Button>
                  </Space>}>
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Space>
                      <Text type="secondary">别名</Text>
                      <Input value={t.alias} style={{ width: 100 }}
                        onChange={(e) => edit((s) => { s.source.tables[i].alias = e.target.value })} />
                      <Text type="secondary">计算日</Text>
                      <Select value={spec.source.limit} style={{ width: 130 }}
                        onChange={(v) => edit((s) => { s.source.limit = v })}
                        options={[1000, 10000, 200000, 500000].map((v) => ({ value: v, label: `最多 ${v} 行` }))} />
                    </Space>
                    <Select mode="multiple" allowClear style={{ width: '100%' }} placeholder="取哪些列"
                      value={t.columns} options={colOpts(spec.source.db, t.table)}
                      onChange={(v) => edit((s) => { s.source.tables[i].columns = v })} />
                  </Space>
                </Card>
              ))}
              {!spec.source.tables.length && <Empty description="还没选表" />}
            </Space>
          </Card>

          <Card size="small" title="关联" extra={
            <Button size="small" onClick={() => edit((s) => { s.source.joins.push({ left: '', right: '', type: 'inner' }) })}>
              加一条
            </Button>}>
            <Space direction="vertical" style={{ width: '100%' }}>
              {spec.source.joins.map((j, i) => (
                <Space key={i} wrap>
                  <AutoComplete value={j.left} options={sourceRefs} style={{ width: 240 }}
                    placeholder="主表.列" onChange={(v) => edit((s) => { s.source.joins[i].left = v })} />
                  <Text>=</Text>
                  <AutoComplete value={j.right} options={sourceRefs} style={{ width: 240 }}
                    placeholder="附表.列" onChange={(v) => edit((s) => { s.source.joins[i].right = v })} />
                  <Select value={j.type} style={{ width: 100 }}
                    onChange={(v) => edit((s) => { s.source.joins[i].type = v })}
                    options={[{ value: 'inner', label: '内连' }, { value: 'left', label: '左连' }]} />
                  <Button size="small" danger onClick={() => edit((s) => { s.source.joins.splice(i, 1) })}>删</Button>
                </Space>
              ))}
              {!spec.source.joins.length && <Text type="secondary">不关联就留空</Text>}
            </Space>
          </Card>

          <Card size="small" title="排掉哪些行" extra={
            <Space>
              <Select size="small" value={spec.source.filter.op} style={{ width: 90 }}
                onChange={(v) => edit((s) => { s.source.filter.op = v })}
                options={[{ value: 'AND', label: '全部满足' }, { value: 'OR', label: '任一满足' }]} />
              <Button size="small" onClick={() => edit((s) => {
                s.source.filter.items.push({ kind: 'cond', ref: '', op: 'notnull', value: '' })
              })}>加一条</Button>
            </Space>
          }>
            <Space direction="vertical" style={{ width: '100%' }}>
              {spec.source.filter.items.map((c, i) => {
                const cond = c as Cond
                return (
                  <Space key={i} wrap>
                    <AutoComplete value={cond.ref} options={sourceRefs} style={{ width: 260 }} placeholder="选列"
                      onChange={(v) => edit((s) => { (s.source.filter.items[i] as Cond).ref = v })} />
                    <Select value={cond.op} style={{ width: 130 }} options={OP_OPTIONS}
                      onChange={(v) => edit((s) => { (s.source.filter.items[i] as Cond).op = v })} />
                    {!NO_VALUE.has(cond.op) && (
                      <Input value={String(cond.value ?? '')} style={{ width: 200 }} placeholder="值"
                        onChange={(e) => edit((s) => { (s.source.filter.items[i] as Cond).value = e.target.value })} />
                    )}
                    <Button size="small" danger onClick={() => edit((s) => { s.source.filter.items.splice(i, 1) })}>删</Button>
                  </Space>
                )
              })}
              {!spec.source.filter.items.length && <Text type="secondary">不排除就留空</Text>}
            </Space>
          </Card>
        </Space>
      ),
    },
    {
      key: 'calc', label: '② 计算',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <ErrBox list={errOf('calc')} />
          <Card size="small" title="一行代表什么（分组列）" extra={
            <Button size="small" onClick={() => edit((s) => { s.calc.grain.push({ name: '', ref: '' }) })}>加一列</Button>
          }>
            <Space direction="vertical" style={{ width: '100%' }}>
              {spec.calc.grain.map((g, i) => (
                <Space key={i} wrap>
                  <Input value={g.name} placeholder="结果里的名字" style={{ width: 180 }}
                    onChange={(e) => edit((s) => { s.calc.grain[i].name = e.target.value })} />
                  <Text>=</Text>
                  <AutoComplete value={g.ref} options={sourceRefs} style={{ width: 280 }} placeholder="取哪一列"
                    onChange={(v) => edit((s) => { s.calc.grain[i].ref = v })} />
                  <Button size="small" danger onClick={() => edit((s) => { s.calc.grain.splice(i, 1) })}>删</Button>
                </Space>
              ))}
            </Space>
          </Card>

          <Card size="small" title="计算列" extra={
            <Button size="small" onClick={() => edit((s) => { s.calc.fields.push({ name: '', expr: '' }) })}>加一列</Button>
          }>
            <Space direction="vertical" style={{ width: '100%' }}>
              {spec.calc.fields.map((f, i) => (
                <Space key={i} wrap align="start">
                  <Input value={f.name} placeholder="列名，如 P" style={{ width: 140 }}
                    onChange={(e) => edit((s) => { s.calc.fields[i].name = e.target.value })} />
                  <Text>=</Text>
                  <Input.TextArea value={f.expr} autoSize={{ minRows: 1, maxRows: 4 }} style={{ width: 460 }}
                    placeholder='如 datediff(t1.due_date, today()) 或 "红" if P <= 25 else "黄"'
                    onChange={(e) => edit((s) => { s.calc.fields[i].expr = e.target.value })} />
                  <Button size="small" danger onClick={() => edit((s) => { s.calc.fields.splice(i, 1) })}>删</Button>
                </Space>
              ))}
              <Text type="secondary">
                能用：列名、数字、+ − × ÷、today()、datediff(a,b)、round、coalesce、sum/avg/min/max/count、条件写法 a if 条件 else b
              </Text>
            </Space>
          </Card>

          <Card size="small" title="同一个对象多行怎么办">
            <Space>
              <Select value={spec.calc.row_policy} style={{ width: 180 }}
                onChange={(v) => edit((s) => { s.calc.row_policy = v })}
                options={[{ value: 'first', label: '取第一行' }, { value: 'latest', label: '取最新一行' },
                  { value: 'max', label: '取该列最大的' }, { value: 'min', label: '取该列最小的' }]} />
              {spec.calc.row_policy !== 'first' && (
                <>
                  <Text type="secondary">按哪列</Text>
                  <AutoComplete value={spec.calc.order_ref} options={sourceRefs} style={{ width: 280 }}
                    onChange={(v) => edit((s) => { s.calc.order_ref = v })} />
                </>
              )}
            </Space>
          </Card>
        </Space>
      ),
    },
    {
      key: 'judge', label: '③ 判断',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <ErrBox list={errOf('judge')} />
          <Alert type="info" showIcon message="从上往下匹配，第一条成立的结论就是答案"
            description="条件可以引用计算结果里的名字，也可以直接引用本轮取到的列。" />
          {spec.judge.rules.map((r, ri) => (
            <Card key={ri} size="small" title={`第 ${ri + 1} 条`} extra={
              <Space>
                <Select size="small" value={r.op || 'AND'} style={{ width: 110 }}
                  onChange={(v) => edit((s) => { s.judge.rules[ri].op = v })}
                  options={[{ value: 'AND', label: '全部满足' }, { value: 'OR', label: '任一满足' }]} />
                <Button size="small" danger onClick={() => edit((s) => { s.judge.rules.splice(ri, 1) })}>删</Button>
              </Space>
            }>
              <Space direction="vertical" style={{ width: '100%' }}>
                {r.when.map((c, ci) => (
                  <Space key={ci} wrap>
                    <AutoComplete value={c.ref} options={valueChoices} style={{ width: 240 }} placeholder="条件列"
                      onChange={(v) => edit((s) => { s.judge.rules[ri].when[ci].ref = v })} />
                    <Select value={c.op} style={{ width: 120 }} options={OP_OPTIONS}
                      onChange={(v) => edit((s) => { s.judge.rules[ri].when[ci].op = v })} />
                    {!NO_VALUE.has(c.op) && (
                      <Input value={String(c.value ?? '')} style={{ width: 160 }} placeholder="值"
                        onChange={(e) => edit((s) => { s.judge.rules[ri].when[ci].value = e.target.value })} />
                    )}
                    <Button size="small" danger
                      onClick={() => edit((s) => { s.judge.rules[ri].when.splice(ci, 1) })}>删</Button>
                  </Space>
                ))}
                <Button size="small" onClick={() => edit((s) => {
                  s.judge.rules[ri].when.push({ ref: '', op: 'eq', value: '' })
                })}>加条件</Button>
                <Space>
                  <Text strong>则</Text>
                  <Switch checkedChildren="命中" unCheckedChildren="不命中" checked={r.then.hit}
                    onChange={(v) => edit((s) => { s.judge.rules[ri].then.hit = v })} />
                  <Text type="secondary">档位</Text>
                  <Input value={r.then.tier} style={{ width: 140 }} placeholder="如 high"
                    onChange={(e) => edit((s) => { s.judge.rules[ri].then.tier = e.target.value })} />
                </Space>
              </Space>
            </Card>
          ))}
          <Button onClick={() => edit((s) => {
            s.judge.rules.push({ when: [{ ref: '', op: 'eq', value: '' }], op: 'AND',
              then: { hit: true, tier: '' } })
          })}>加一条规则</Button>
          <Card size="small" title="都不命中时">
            <Space>
              <Switch checkedChildren="命中" unCheckedChildren="不命中" checked={spec.judge.default.hit}
                onChange={(v) => edit((s) => { s.judge.default.hit = v })} />
              <Text type="secondary">档位</Text>
              <Input value={spec.judge.default.tier} style={{ width: 140 }} placeholder="如 low"
                onChange={(e) => edit((s) => { s.judge.default.tier = e.target.value })} />
            </Space>
          </Card>
        </Space>
      ),
    },
    {
      key: 'write', label: '④ 回填',
      children: (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <ErrBox list={errOf('write')} />
          <Card size="small" title="写到哪张表">
            <Space wrap>
              <Select value={spec.write.db} style={{ width: 140 }}
                onChange={(v) => edit((s) => { s.write.db = v; s.write.table = ''; s.write.key = [] })}
                options={[{ value: 'biz', label: '业务库' }, { value: 'fw', label: '预警库' }]} />
              <AutoComplete value={spec.write.table} options={tableOpts(spec.write.db)} style={{ width: 340 }}
                placeholder="选目标表" onChange={(v) => edit((s) => { s.write.table = v; s.write.key = [] })} />
              <Select value={spec.write.mode} style={{ width: 200 }}
                onChange={(v) => edit((s) => { s.write.mode = v })}
                options={[{ value: 'update', label: '只更新已有行' },
                  { value: 'upsert', label: '有则更新，没有就插入' },
                  { value: 'insert', label: '只插入新行' }]} />
            </Space>
            {spec.write.table && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">
                  推荐匹配键：{(tkeys[spec.write.db + '/' + spec.write.table] || []).join('、') || '（这张表没有唯一键）'}
                </Text>
                <Button size="small" style={{ marginLeft: 8 }} onClick={() => edit((s) => {
                  const keys = tkeys[s.write.db + '/' + s.write.table] || []
                  s.write.key = keys.map((k) => ({ target: k, from: k }))
                })}>用推荐键</Button>
              </div>
            )}
          </Card>

          <Card size="small" title="按什么键对上" extra={
            <Button size="small" onClick={() => edit((s) => { s.write.key.push({ target: '', from: '' }) })}>加一个</Button>
          }>
            <Space direction="vertical" style={{ width: '100%' }}>
              {spec.write.key.map((k, i) => (
                <Space key={i} wrap>
                  <Text type="secondary">目标列</Text>
                  <AutoComplete value={k.target} options={colOpts(spec.write.db, spec.write.table)}
                    style={{ width: 220 }} onChange={(v) => edit((s) => { s.write.key[i].target = v })} />
                  <Text>=</Text>
                  <AutoComplete value={k.from} options={valueChoices} style={{ width: 260 }}
                    placeholder="取哪个结果"
                    onChange={(v) => edit((s) => { s.write.key[i].from = v })} />
                  <Button size="small" danger onClick={() => edit((s) => { s.write.key.splice(i, 1) })}>删</Button>
                </Space>
              ))}
              {!spec.write.key.length && <Text type="secondary">至少要一个匹配键</Text>}
            </Space>
          </Card>

          <Card size="small" title="回填哪几列" extra={
            <Button size="small" onClick={() => edit((s) => { s.write.map.push({ target: '', from: '' }) })}>加一列</Button>
          }>
            <Space direction="vertical" style={{ width: '100%' }}>
              {spec.write.map.map((m, i) => {
                const byValue = m.from === undefined || m.from === ''
                return (
                  <Space key={i} wrap>
                    <Text type="secondary">目标列</Text>
                    <AutoComplete value={m.target} options={colOpts(spec.write.db, spec.write.table)}
                      style={{ width: 220 }} onChange={(v) => edit((s) => { s.write.map[i].target = v })} />
                    <Text>=</Text>
                    <Select size="small" value={byValue ? 'value' : 'from'} style={{ width: 90 }}
                      onChange={(v) => edit((s) => {
                        if (v === 'value') { delete s.write.map[i].from; s.write.map[i].value = '' }
                        else { delete s.write.map[i].value; s.write.map[i].from = '' }
                      })}
                      options={[{ value: 'from', label: '取结果' }, { value: 'value', label: '填固定值' }]} />
                    {byValue ? (
                      <Input value={String(m.value ?? '')} style={{ width: 220 }} placeholder="固定值"
                        onChange={(e) => edit((s) => { s.write.map[i].value = e.target.value })} />
                    ) : (
                      <AutoComplete value={m.from} options={valueChoices} style={{ width: 260 }}
                        onChange={(v) => edit((s) => { s.write.map[i].from = v })} />
                    )}
                    <Button size="small" danger onClick={() => edit((s) => { s.write.map.splice(i, 1) })}>删</Button>
                  </Space>
                )
              })}
              {!spec.write.map.length && <Text type="secondary">至少要回填一列</Text>}
            </Space>
          </Card>
          <Text type="secondary">本轮没算到的行保留原值不动。</Text>
        </Space>
      ),
    },
    {
      key: 'note', label: '⑤ 备注',
      children: (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input.TextArea value={spec.note} autoSize={{ minRows: 8, maxRows: 24 }}
            placeholder="写给看的人：口径上的例外、坑、为什么这么定"
            onChange={(e) => edit((s) => { s.note = e.target.value })} />
        </Space>
      ),
    },
  ]

  return (
    <Drawer open width={1180} onClose={onClose} destroyOnClose
      title={<Space>
        <Input value={name} style={{ width: 300 }} onChange={(e) => setName(e.target.value)} />
        <Tag>v{detail?.version ?? 1}</Tag>
        <Space size={4}>
          <Switch checked={enabled} onChange={setEnabled} size="small" />
          <Text type="secondary">启用</Text>
        </Space>
      </Space>}
      extra={<Space>
        <Button loading={busy === 'compile'} onClick={() => void doCompile()}>编译预览</Button>
        <Button loading={busy === 'dry'} onClick={() => void doDryRun()}>试跑</Button>
        <Button type="primary" loading={saving} onClick={() => void save()}>保存</Button>
      </Space>}>
      <Tabs tabPosition="left" activeKey={tab} onChange={setTab} items={sections} style={{ minHeight: 560 }} />

      <Modal open={!!preview} width={900} footer={null} onCancel={() => setPreview(null)} title="编译结果">
        {preview && (
          <Space direction="vertical" style={{ width: '100%' }}>
            {preview.ok ? <Alert type="success" showIcon message="能编译" />
              : <Alert type="error" showIcon message="还有槽位没填对"
                description={(preview.errors || []).map((e) => e.message).join('；')} />}            <Card size="small" title="取数 SQL">
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>{preview.select_preview || '—'}</pre>
            </Card>
            <Card size="small" title="回填">
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>{preview.write_preview || '—'}</pre>
            </Card>
          </Space>
        )}
      </Modal>

      <Drawer open={!!result} width={1000} onClose={() => setResult(null)}
        title={result ? `试跑：取到 ${result.selected_n} 行，命中 ${result.hit_n} 行` : ''}>
        {result && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Alert type="info" showIcon message="试跑不写库"
              description={`整轮 ${result.elapsed_ms} 毫秒。下面是前 ${result.sample.length} 行结果。`} />
            <Card size="small" title="取数 SQL">
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>{result.sql}</pre>
            </Card>
            <SampleTable rows={result.sample} />
          </Space>
        )}
      </Drawer>
    </Drawer>
  )
}

function ErrBox({ list }: { list: FlowError[] }) {
  if (!list.length) return null
  return <Alert type="warning" showIcon message={`这一节还有 ${list.length} 处没填对`}
    description={list.map((e) => e.message).join('；')} />
}

export function SampleTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <Empty description="没有结果行" />
  const keys = Array.from(new Set(rows.flatMap((r) => Object.keys(r))))
  return (
    <Table size="small" rowKey={(_r, i) => String(i)} dataSource={rows} pagination={{ pageSize: 20 }}
      scroll={{ x: 'max-content' }}
      columns={keys.map((k) => ({ title: k, dataIndex: k, render: (v) => (v === null || v === undefined ? '—' : String(v)) }))} />
  )
}
