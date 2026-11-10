import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Divider,
  Drawer,
  Empty,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import { EditOutlined, LockOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import { http, getToken } from '../api/client'
import CaliberInline from '../components/CaliberInline'

const { Title, Text } = Typography

type Mat = {
  key: string
  name: string
  editable: boolean
  always?: boolean
  when?: string
  textKey?: string
  slot?: string
  sections?: number
  pinned?: number
  deleted?: number
  chars: number
  body: string
}
type TreeNode = {
  id: string
  no: number
  name: string
  desc: string
  materials: Mat[]
  branches?: string[]
}
type Section = {
  id?: number
  section: string
  body: string
  hint?: string
  version_no?: number
  pinned?: boolean
}

const SLOT_KEYS: Record<string, string> = {
  'mat.fetch': 'fetch',
  'mat.judge': 'judge',
  'mat.answer': 'answer',
  'mat.check': 'check',
}

export default function Prompts() {
  const [nodes, setNodes] = useState<TreeNode[]>([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(false)
  const [current, setCurrent] = useState<Mat | null>(null)
  const [sections, setSections] = useState<Section[]>([])
  const [deadSections, setDeadSections] = useState<Section[]>([])
  const [secQ, setSecQ] = useState('')
  const [saving, setSaving] = useState(false)
  const [importText, setImportText] = useState('')
  const [showImport, setShowImport] = useState(false)
  // ★ 2026-11-03 定稿：补全表回来了 —— 第 4 步拿原问题 + 补后问题来查它（只查不改），
  //   这张表就是用户体验，直接在页面上迭代。
  const [compOpen, setCompOpen] = useState(false)
  const [compRows, setCompRows] = useState<any[]>([])
  const [compDel, setCompDel] = useState<any[]>([])
  const [compSaving, setCompSaving] = useState(false)
  const [txtOpen, setTxtOpen] = useState(false)
  const [txtKey, setTxtKey] = useState('')
  const [txtName, setTxtName] = useState('')
  const [txtBody, setTxtBody] = useState('')
  const [txtOverridden, setTxtOverridden] = useState(false)
  const [txtSaving, setTxtSaving] = useState(false)

  // ── 纠错词典（2026-11-02）────────────────────────────────
  const [lexOpen, setLexOpen] = useState(false)
  const [lexKey, setLexKey] = useState('typo')
  const [lexCols, setLexCols] = useState<{ key: string; label: string }[]>([])
  const [lexRows, setLexRows] = useState<any[]>([])
  const [lexQ, setLexQ] = useState('')
  const [lexTotal, setLexTotal] = useState(0)
  const [lexBusy, setLexBusy] = useState(false)

  // ── 数据表元数据（2026-11-02）────────────────────────────
  const [metaOpen, setMetaOpen] = useState(false)
  const [metaTables, setMetaTables] = useState<any[]>([])
  const [metaBusy, setMetaBusy] = useState(false)

  const loadText = async (key: string, name: string) => {
    try {
      const r: any = await http.get('/prompts/texts')
      const hit = (r.items || []).find((x: any) => x.key === key)
      if (!hit) {
        message.error('没找到这份提示词')
        return
      }
      setTxtKey(key)
      setTxtName(name || hit.name)
      setTxtBody(hit.body || '')
      setTxtOverridden(!!hit.overridden)
      setTxtOpen(true)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const saveText = async () => {
    setTxtSaving(true)
    try {
      const r: any = await http.put(`/prompts/texts/${txtKey}`, { body: txtBody })
      if (r?.ok) {
        message.success('已保存，下一轮问答生效')
        setTxtOverridden(true)
        await loadTree()
      } else {
        message.error(r?.error || '保存失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setTxtSaving(false)
    }
  }

  const resetText = async () => {
    setTxtSaving(true)
    try {
      const r: any = await http.delete(`/prompts/texts/${txtKey}`)
      if (r?.ok) {
        message.success('已恢复成代码默认值')
        setTxtOpen(false)
        await loadTree()
      } else {
        message.error(r?.error || '恢复失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setTxtSaving(false)
    }
  }

  // ── 补全表（2026-11-03 恢复）─────────────────────────────────
  const loadComplete = async () => {
    try {
      const r: any = await http.get('/prompts/complete')
      setCompRows((r.rows || []).map((x: any) => ({
        id: x.id,
        kind: x.kind || 'norm',
        match_text: x.match_text || '',
        fill_text: x.fill_text || '',
        priority: x.priority ?? 0,
      })))
      setCompDel(r.deleted || [])
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const restoreComp = async (row: any) => {
    const r: any = await http.post(`/prompts/complete/${row.id}/restore`)
    if (r?.ok) {
      message.success('已恢复')
      await loadComplete()
    } else message.error(r?.error || '恢复失败')
  }

  const saveComplete = async () => {
    setCompSaving(true)
    try {
      const r: any = await http.put('/prompts/complete', { rows: compRows })
      if (r?.ok) {
        message.success(`已保存 ${r.rows} 条，下一轮问答生效`)
        await loadTree()
        await loadComplete()
      } else {
        message.error(r?.error || '保存失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setCompSaving(false)
    }
  }

  const addCompRow = () =>
    setCompRows((v) => [...v, { kind: 'norm', match_text: '', fill_text: '', priority: (v.length + 1) * 10 }])

  const delCompRow = (i: number) => setCompRows((v) => v.filter((_, idx) => idx !== i))

  const setCompCell = (i: number, k: string, val: any) =>
    setCompRows((v) => v.map((row, idx) => (idx === i ? { ...row, [k]: val } : row)))

  const slot = current ? SLOT_KEYS[current.key] : ''

  const loadTree = async () => {
    setLoading(true)
    try {
      const r: any = await http.get('/prompts/tree')
      setNodes(r.nodes || [])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTree()
  }, [])

  const [importOpen, setImportOpen] = useState(false)
  const [importBody, setImportBody] = useState('')
  const [importing, setImporting] = useState(false)

  const doImportAll = async () => {
    if (!importBody.trim()) return
    setImporting(true)
    try {
      const r: any = await http.post('/prompts/import', { text: importBody })
      if (r?.ok) {
        const parts = [
          ...Object.entries(r.materials || {}).map(([k, v]) => `${k} ${v} 节`),
          ...(r.texts || []).map((k: string) => `文本键 ${k}`),
          r.complete ? `补全表 ${r.complete} 条` : '',
          ...(r.skipped || []).map((x: string) => `跳过：${x}`),
        ].filter(Boolean)
        message.success(`已导入：${parts.join('，') || '没有变化'}`)
        setImportOpen(false)
        setImportBody('')
        await loadTree()
      } else message.error(r?.error || '导入失败')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setImporting(false)
    }
  }

  const pickFile = (file: File | null) => {
    if (!file) return
    const fr = new FileReader()
    fr.onload = () => setImportBody(String(fr.result || ''))
    fr.readAsText(file, 'utf-8')
  }

  const totalChars = useMemo(
    () => nodes.reduce((s, n) => s + n.materials.reduce((a, m) => a + m.chars, 0), 0),
    [nodes]
  )

  const pinnedCount = sections.filter((s) => s.pinned !== false).length
  const pinnedChars = sections
    .filter((s) => s.pinned !== false)
    .reduce((a, s) => a + (s.body || '').length, 0)

  const openMaterial = async (m: Mat) => {
    // 纠错词典：走词典抽屉（增删改查，60 秒生效）
    if (m.key === 'nc.typo') {
      setLexOpen(true)
      loadLex('typo', '')
      return
    }
    // 补全表：单独的抽屉（整表编辑 + 保存）
    if (m.key === 'mat.complete') {
      setCompOpen(true)
      await loadComplete()
      return
    }
    // 身份 / 模式做法 / 业务词表：走文本编辑器（存 prompt_text，可覆盖）
    if (m.textKey) {
      await loadText(m.textKey, m.name)
      return
    }
    setCurrent(m)
    setOpen(true)
    setShowImport(false)
    setImportText('')
    setSecQ('')
    const key = SLOT_KEYS[m.key]
    if (key) {
      try {
        const r: any = await http.get(`/prompts/material/${key}`)
        setSections(r.sections || [])
        const dd: any = await http.get(`/prompts/material/${key}/deleted`)
        setDeadSections(dd.rows || [])
      } catch (e) {
        setSections([])
        setDeadSections([])
        message.error((e as Error).message)
      }
    } else {
      setSections([])
      setDeadSections([])
    }
  }

  const save = async () => {
    if (!slot) return
    setSaving(true)
    try {
      const r: any = await http.put(`/prompts/material/${slot}`, { sections })
      if (r?.ok) {
        message.success('已保存，下一轮问答生效（缓存 60 秒内刷新）')
        await loadTree()
      } else {
        message.error(r?.error || '保存失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const doImport = async () => {
    if (!slot || !importText.trim()) return
    setSaving(true)
    try {
      const text = importText.trim().startsWith('#')
        ? importText
        : `# ${current?.name || slot}\n\n${importText}`
      const r: any = await http.post('/prompts/import', { text })
      if (r?.ok) {
        message.success(`已导入 ${r.materials?.[slot] ?? 0} 节`)
        const rr: any = await http.get(`/prompts/material/${slot}`)
        setSections(rr.sections || [])
        const dd: any = await http.get(`/prompts/material/${slot}/deleted`)
        setDeadSections(dd.rows || [])
        setShowImport(false)
        setImportText('')
        await loadTree()
      } else {
        message.error(r?.skipped?.join('；') || r?.error || '导入失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const delSection = async (i: number) => {
    const s = sections[i]
    try {
      if (s.id) {
        const r: any = await http.delete(`/prompts/material/section/${s.id}`)
        if (!r?.ok) {
          message.error(r?.error || '删除失败')
          return
        }
      }
      setSections((prev) => prev.filter((_, j) => j !== i))
      message.success('已删除（软删，可在「已删除」里恢复）')
      if (slot) {
        const dd: any = await http.get(`/prompts/material/${slot}/deleted`)
        setDeadSections(dd.rows || [])
      }
      await loadTree()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const restoreSection = async (s: Section) => {
    if (!s.id) return
    const r: any = await http.post(`/prompts/material/section/${s.id}/restore`)
    if (r?.ok) {
      message.success('已恢复')
      if (slot) {
        const rr: any = await http.get(`/prompts/material/${slot}`)
        setSections(rr.sections || [])
        const dd: any = await http.get(`/prompts/material/${slot}/deleted`)
        setDeadSections(dd.rows || [])
      }
      await loadTree()
    } else message.error(r?.error || '恢复失败')
  }

  const moveSection = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= sections.length) return
    setSections((prev) => {
      const next = [...prev]
      const [x] = next.splice(i, 1)
      next.splice(j, 0, x)
      return next
    })
  }

  const exportMd = async (which = '') => {
    try {
      const url = `/api/v1/prompts/export${which ? `?slot=${which}` : ''}`
      const res = await fetch(url, { headers: { Authorization: `Bearer ${getToken() || ''}` } })
      if (!res.ok) {
        message.error('导出失败')
        return
      }
      const text = await res.text()
      const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `提示词${which ? `-${which}` : ''}.md`
      a.click()
      URL.revokeObjectURL(a.href)
      message.success('已导出')
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const setSectionBody = (idx: number, body: string) => {
    setSections((prev) => prev.map((s, i) => (i === idx ? { ...s, body } : s)))
  }

  // ── 纠错词典 ────────────────────────────────────────────
  const loadLex = async (key = lexKey, q = lexQ) => {
    setLexBusy(true)
    try {
      const r: any = await http.get(`/prompts/lexicon/${key}`, { params: { q, limit: 200 } })
      if (r?.error) {
        message.error(r.error)
        return
      }
      setLexKey(key)
      setLexCols(r.cols || [])
      setLexRows(r.rows || [])
      setLexTotal(r.total || 0)
    } finally {
      setLexBusy(false)
    }
  }

  const setLexCell = (idx: number, field: string, v: string) => {
    setLexRows((prev) => prev.map((r, i) => (i === idx ? { ...r, [field]: v } : r)))
  }

  const saveLexRow = async (row: any) => {
    const body: any = {}
    lexCols.forEach((c) => (body[c.key] = row[c.key] ?? ''))
    const r: any = row.id
      ? await http.put(`/prompts/lexicon/${lexKey}/${row.id}`, body)
      : await http.post(`/prompts/lexicon/${lexKey}`, body)
    if (r?.ok) {
      message.success(row.id ? '已保存，60 秒内生效' : '已新增，60 秒内生效')
      await loadLex()
    } else message.error(r?.error || '保存失败')
  }

  const delLexRow = async (row: any) => {
    if (!row.id) {
      setLexRows((prev) => prev.filter((x) => x !== row))
      return
    }
    const r: any = await http.delete(`/prompts/lexicon/${lexKey}/${row.id}`)
    if (r?.ok) {
      message.success('已删除（软删），60 秒内生效')
      await loadLex()
    } else message.error(r?.error || '删除失败')
  }

  const addLexRow = () => {
    const blank: any = { id: 0 }
    lexCols.forEach((c) => (blank[c.key] = ''))
    setLexRows((prev) => [blank, ...prev])
  }

  // ── 数据表元数据 ────────────────────────────────────────
  const loadMeta = async () => {
    setMetaBusy(true)
    try {
      const r: any = await http.get('/prompts/meta')
      if (r?.error) {
        message.error(r.error)
        return
      }
      setMetaTables(r.tables || [])
    } finally {
      setMetaBusy(false)
    }
  }

  const saveMeta = async (kind: string, table: string, field: string, value: string,
                          column = '') => {
    const r: any = await http.put('/prompts/meta/override',
      { kind, table_name: table, column_name: column, field, value })
    if (r?.ok) message.success(value.trim() ? '已覆盖' : '已恢复业务库原值')
    else message.error(r?.error || '保存失败')
  }

  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }} align="center">
        <Title level={4} style={{ margin: 0 }}>
          提示词
        </Title>
        <Text type="secondary">
          八步流水线；点节点右侧的材料可查看与编辑，改了下一轮就生效
        </Text>
        <Button icon={<ReloadOutlined />} onClick={loadTree}>
          刷新
        </Button>
        <Button onClick={() => { setLexOpen(true); loadLex('typo', '') }}>纠错词典</Button>
        <Button onClick={() => { setCompOpen(true); loadComplete() }}>补全表</Button>
        <Button onClick={() => { setMetaOpen(true); loadMeta() }}>数据表</Button>
        <Button onClick={() => exportMd()}>导出 md</Button>
        <Button onClick={() => { setImportBody(''); setImportOpen(true) }}>导入 md</Button>
        <Tag color="blue">合计 {totalChars.toLocaleString()} 字</Tag>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="改哪一份就点哪一份：回答不好改「作答规则」，判定不对改「判定标准」，取数不对改「查数口径」。标「只读」的是代码里的常量，页面只能看。"
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {nodes.map((n) => (
          <Card key={n.id} size="small" bodyStyle={{ padding: '10px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
              <div style={{ width: 190, flex: '0 0 190px' }}>
                <Text strong>
                  {n.no}. {n.name}
                </Text>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {n.desc}
                  </Text>
                </div>
              </div>

              <div style={{ flex: 1 }}>
                <Space wrap>
                  {(n.materials || []).length === 0 && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      （这一步不需要材料）
                    </Text>
                  )}
                  {(n.materials || []).map((m) => (
                    <span key={m.key} style={{ display: 'inline-block', textAlign: 'left' }}>
                      <Button
                        size="small"
                        type={m.editable ? 'primary' : 'default'}
                        ghost={m.editable}
                        icon={m.editable ? <EditOutlined /> : <LockOutlined />}
                        onClick={() => openMaterial(m)}
                      >
                        {m.name}
                        <span style={{ marginLeft: 6, opacity: 0.7 }}>
                          {m.chars.toLocaleString()}字
                        </span>
                      </Button>
                      <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                        {m.when || '—'}
                        {m.sections ? `　｜ ${m.sections} 节（必带 ${m.pinned}）` : ''}
                        {m.deleted ? `　｜ 已删 ${m.deleted}` : ''}
                      </div>
                    </span>
                  ))}
                </Space>
                {n.branches ? (
                  <>
                    <Divider style={{ margin: '8px 0' }} />
                    <Space wrap>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        分支：
                      </Text>
                      {n.branches.map((b) => (
                        <Tag key={b}>{b}</Tag>
                      ))}
                    </Space>
                  </>
                ) : null}
              </div>
            </div>
          </Card>
        ))}
      </div>

      <Drawer
        title={
          current ? `${current.name}　${current.chars.toLocaleString()} 字` : ''
        }
        width={860}
        open={open}
        onClose={() => setOpen(false)}
        extra={
          slot ? (
            <Space>
              <Button onClick={() => exportMd(slot)}>导出 md</Button>
              <Button onClick={() => setShowImport((v) => !v)} icon={<UploadOutlined />}>
                上传 md 覆盖
              </Button>
              <Button type="primary" loading={saving} onClick={save}>
                保存
              </Button>
            </Space>
          ) : null
        }
      >
        {!current ? null : (
          <>
            <Alert
              type={current.editable ? 'info' : 'warning'}
              showIcon
              style={{ marginBottom: 12 }}
              message={`${current.name} · ${current.chars.toLocaleString()} 字 · 注入时机：${
                current.when || '只在这份材料被代码读用时'
              }`}
              description={
                current.editable
                  ? '「必带」勾上的节会在该阶段注入时一起进提示词；不勾的只存着，模型需要时才由 get_rule 取。'
                  : '这份是代码或词典里的常量/内容，这一版只读。'
              }
            />
            {!current.editable ? (
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{current.body}</pre>
            ) : null}
          </>
        )}
        {!current ? null : !current.editable ? null : slot ? (
          <>
            {slot === 'fetch' && (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="这段文字既是给模型看的口径，也是机器执行的口径；改完保存即生效"
              />
            )}
            {showImport && (
              <Card size="small" style={{ marginBottom: 12 }}>
                <Text type="secondary">
                  粘贴整份 markdown，按 ## / ### 标题自动切节并覆盖这一份
                </Text>
                <Input.TextArea
                  rows={8}
                  value={importText}
                  onChange={(e) => setImportText(e.target.value)}
                  style={{ margin: '8px 0' }}
                />
                <Space>
                  <Button type="primary" onClick={doImport} loading={saving}>
                    切节并覆盖
                  </Button>
                  <Button onClick={() => setShowImport(false)}>取消</Button>
                </Space>
              </Card>
            )}

            <Space style={{ marginBottom: 12 }} wrap>
              <Button
                onClick={() =>
                  setSections((prev) => [
                    ...prev,
                    { section: '新规则（点这里改标题）', body: '', pinned: true },
                  ])
                }
              >
                新增一节
              </Button>
              <Input
                size="small"
                placeholder="搜标题或正文"
                value={secQ}
                allowClear
                onChange={(e) => setSecQ(e.target.value)}
                style={{ width: 200 }}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                共 {sections.length} 节，必带 {pinnedCount} 节；必带合计 {pinnedChars.toLocaleString()} 字（这些会真进提示词）
              </Text>
            </Space>

            {sections.length === 0 ? (
              <Empty description="这一份还是空的，可以上传 md 或直接在这里写" />
            ) : null}

            {sections
              .map((s, i) => ({ s, i }))
              .filter(({ s }) => !secQ.trim()
                || s.section.includes(secQ.trim()) || s.body.includes(secQ.trim()))
              .map(({ s, i }) => (
              <div key={i} style={{ marginBottom: 16 }}>
                <Space align="center" wrap>
                  <Button size="small" type="text" disabled={i === 0}
                          onClick={() => moveSection(i, -1)}>↑</Button>
                  <Button size="small" type="text" disabled={i === sections.length - 1}
                          onClick={() => moveSection(i, 1)}>↓</Button>
                  <Input
                    size="small"
                    value={s.section}
                    placeholder="这一节的标题（get_rule 认这个名字）"
                    onChange={(e) =>
                      setSections((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, section: e.target.value } : x))
                      )
                    }
                    style={{ width: 360, fontWeight: 600 }}
                  />
                  <span style={{ color: '#999', fontSize: 12 }}>{s.body.length} 字</span>
                  <Checkbox
                    checked={s.pinned !== false}
                    onChange={(e) =>
                      setSections((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, pinned: e.target.checked } : x))
                      )
                    }
                  >
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      必带
                    </Text>
                  </Checkbox>
                  <Popconfirm
                    title="删除这一节？"
                    description={`删掉后不再进提示词、get_rule 也取不到「${s.section}」，但不会真删，可在下方「已删除」里恢复。`}
                    okText="确定删除"
                    okButtonProps={{ danger: true }}
                    cancelText="再想想"
                    onConfirm={() => delSection(i)}
                  >
                    <Button size="small" danger type="text">
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
                <Input.TextArea
                  autoSize={{ minRows: 4, maxRows: 20 }}
                  value={s.body}
                  onChange={(e) => setSectionBody(i, e.target.value)}
                  style={{ marginTop: 6, fontSize: 12 }}
                />
              </div>
            ))}

            {deadSections.length > 0 && (
              <Card size="small" title={`已删除（${deadSections.length} 节，可恢复）`}
                    style={{ marginTop: 12, opacity: 0.9 }}>
                {deadSections.map((s) => (
                  <Space key={s.id} style={{ display: 'flex', marginBottom: 6 }} wrap>
                    <Text delete type="secondary" style={{ fontSize: 12 }}>
                      {s.section}　{s.body.length} 字
                    </Text>
                    <Button size="small" onClick={() => restoreSection(s)}>恢复</Button>
                  </Space>
                ))}
              </Card>
            )}

            {slot === 'fetch' && (
              <Card size="small" style={{ marginTop: 16 }}>
                <CaliberInline
                  text={sections
                    .map((s) => s.body || '')
                    .filter((b) => b.trim())
                    .join('\n\n')}
                />
              </Card>
            )}
          </>
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="这份由代码或词典提供，这一版只读"
            />
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{current.body}</pre>
          </>
        )}
      </Drawer>

      <Drawer
        title={`${txtName}　${txtBody.length.toLocaleString()} 字`}
        width={860}
        open={txtOpen}
        onClose={() => setTxtOpen(false)}
        extra={
          <Space>
            <Button onClick={() => {
              setImportBody(`# ${txtName}（${txtKey}）\n\n${txtBody}`)
              setImportOpen(true)
            }}>导入</Button>
            <Button onClick={resetText} disabled={!txtOverridden}>
              恢复默认
            </Button>
            <Button type="primary" loading={txtSaving} onClick={saveText}>
              保存
            </Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={
            txtOverridden
              ? '当前用的是你改过的版本（存 prompt_text 表）；点「恢复默认」回到代码里的原文。'
              : '当前用的是代码里的原文；保存后就用你改的版本，下一轮问答生效。'
          }
          description="模式做法和词表是 JSON，改值就行；身份是纯文本。"
        />
        <Input.TextArea
          value={txtBody}
          onChange={(e) => setTxtBody(e.target.value)}
          autoSize={{ minRows: 18, maxRows: 30 }}
          style={{ fontSize: 12 }}
        />
      </Drawer>

      {/* 纠错词典 */}
      <Drawer
        title={`纠错词典　共 ${lexTotal} 条`}
        width={1080}
        open={lexOpen}
        onClose={() => setLexOpen(false)}
        extra={
          <Space>
            <Input
              placeholder="搜错误写法 / 正确写法 / 组织"
              value={lexQ}
              allowClear
              style={{ width: 220 }}
              onChange={(e) => setLexQ(e.target.value)}
              onPressEnter={() => loadLex(lexKey, lexQ)}
            />
            <Button onClick={() => loadLex(lexKey, lexQ)} loading={lexBusy}>查询</Button>
            <Button type="primary" onClick={addLexRow}>新增一条</Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="这四张表在本系统库里，改完最多 60 秒生效（纠错引擎会自己发现词典变了）。删除是软删，卡片列表里不再出现。"
          description="标准词表：首字母留空会按字表自动补；拼音码留空只按汉字+首字母匹配。"
        />
        <Space style={{ marginBottom: 12 }} wrap>
          {[
            ['typo', '错别字纠正'],
            ['term', '术语与别名'],
            ['ambiguous', '歧义确认'],
            ['wordlist', '标准词表'],
          ].map(([k, name]) => (
            <Button
              key={k}
              size="small"
              type={lexKey === k ? 'primary' : 'default'}
              onClick={() => loadLex(k, '')}
            >
              {name}
            </Button>
          ))}
        </Space>
        <Table
          size="small"
          rowKey={(r: any, i?: number) => String(r.id || `new-${i}`)}
          loading={lexBusy}
          pagination={false}
          scroll={{ y: 460, x: 'max-content' }}
          dataSource={lexRows}
          columns={[
            ...lexCols.map((c) => ({
              title: c.label,
              dataIndex: c.key,
              width: c.key === 'definition' ? 280 : 160,
              render: (v: any, _r: any, i: number) => (
                <Input
                  size="small"
                  value={v ?? ''}
                  onChange={(e) => setLexCell(i, c.key, e.target.value)}
                />
              ),
            })),
            {
              title: '操作',
              width: 150,
              render: (_v: any, row: any) => (
                <Space size={4}>
                  <Button size="small" type="link" onClick={() => saveLexRow(row)}>
                    {row.id ? '保存' : '新增'}
                  </Button>
                  <Popconfirm title="确定删除这一条？" onConfirm={() => delLexRow(row)}>
                    <Button size="small" type="link" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Drawer>

      {/* 数据表元数据 */}
      <Drawer
        title={`数据表　共 ${metaTables.length} 张`}
        width={1080}
        open={metaOpen}
        onClose={() => setMetaOpen(false)}
        extra={<Button onClick={loadMeta} loading={metaBusy}>刷新</Button>}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="业务库是只读的，这里改的是「覆盖值」，存在本系统库里；读取时覆盖值优先，清空就回到业务库原值。"
          description="表级可改中文名/业务说明；点开一张表还能改每个字段的中文名与说明。改完下一轮问答生效。"
        />
        <Table
          size="small"
          rowKey="table_name"
          loading={metaBusy}
          pagination={{ pageSize: 20 }}
          dataSource={metaTables}
          expandable={{
            expandedRowRender: (row: any) => (
              <Table
                size="small"
                rowKey="column_name"
                pagination={false}
                dataSource={row.columns || []}
                columns={[
                  { title: '字段', dataIndex: 'column_name', width: 200 },
                  { title: '类型', dataIndex: 'data_type', width: 120 },
                  {
                    title: '中文名',
                    width: 220,
                    render: (_v: any, c: any) => (
                      <Input
                        size="small"
                        defaultValue={c.column_cn_name || ''}
                        onBlur={(e) => {
                          if (e.target.value !== (c.column_cn_name || ''))
                            saveMeta('column', row.table_name, 'cn_name', e.target.value,
                                     c.column_name)
                        }}
                      />
                    ),
                  },
                  {
                    title: '说明',
                    render: (_v: any, c: any) => (
                      <Input
                        size="small"
                        defaultValue={c.business_desc || ''}
                        onBlur={(e) => {
                          if (e.target.value !== (c.business_desc || ''))
                            saveMeta('column', row.table_name, 'desc', e.target.value,
                                     c.column_name)
                        }}
                      />
                    ),
                  },
                ]}
              />
            ),
          }}
          columns={[
            { title: '表名', dataIndex: 'table_name', width: 240 },
            {
              title: '中文名',
              width: 240,
              render: (_v: any, row: any) => (
                <Input
                  size="small"
                  defaultValue={row.table_cn_name || ''}
                  onBlur={(e) => {
                    if (e.target.value !== (row.table_cn_name || ''))
                      saveMeta('table', row.table_name, 'cn_name', e.target.value)
                  }}
                />
              ),
            },
            {
              title: '业务说明',
              render: (_v: any, row: any) => (
                <Input
                  size="small"
                  defaultValue={row.business_desc || ''}
                  onBlur={(e) => {
                    if (e.target.value !== (row.business_desc || ''))
                      saveMeta('table', row.table_name, 'desc', e.target.value)
                  }}
                />
              ),
            },
            {
              title: '行数',
              dataIndex: 'count',
              width: 90,
              render: (v: any) => (v == null ? '—' : v),
            },
          ]}
        />
      </Drawer>

      <Drawer
        title={`补全表（第 4 步：只查不改）　${compRows.length} 条`}
        width={860}
        open={compOpen}
        onClose={() => setCompOpen(false)}
        extra={
          <Space>
            <Button onClick={() => {
              setImportBody('# 补全表\n\n| 类型 | 匹配 | 补成 | 优先级 |\n|---|---|---|---|\n| 归一 | 情况 | 预算分配总额，预算执行率 | 10 |\n')
              setImportOpen(true)
            }}>导入</Button>
            <Button onClick={addCompRow}>加一行</Button>
            <Button type="primary" loading={compSaving} onClick={saveComplete}>
              保存
            </Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="匹配到这一条，就把它作为「参考资料」交给第 6 步，不会替用户改问题。删掉是软删，下方「已删除」里能恢复。"
        />
        {compRows.length === 0 && (
          <Empty description="还没有补全条目（空表 = 第 4 步什么都不返回，正常跑）" />
        )}
        {compRows.map((r, i) => (
          <Card key={i} size="small" style={{ marginBottom: 8 }}>
            <Space align="start" wrap>
              <Select
                value={r.kind}
                style={{ width: 90 }}
                onChange={(v) => setCompCell(i, 'kind', v)}
                options={[
                  { value: 'norm', label: '归一' },
                  { value: 'fill', label: '补缺' },
                ]}
              />
              <Input
                value={r.match_text}
                placeholder="匹配到的话（如：情况怎么样）"
                style={{ width: 260 }}
                onChange={(e) => setCompCell(i, 'match_text', e.target.value)}
              />
              <Input
                value={r.fill_text}
                placeholder="补成什么（如：按六项指标 + 预算执行分析）"
                style={{ width: 320 }}
                onChange={(e) => setCompCell(i, 'fill_text', e.target.value)}
              />
              <InputNumber
                value={r.priority}
                min={0}
                style={{ width: 80 }}
                onChange={(v) => setCompCell(i, 'priority', v ?? 0)}
              />
              <Button danger size="small" onClick={() => delCompRow(i)}>
                删
              </Button>
            </Space>
          </Card>
        ))}

        {compDel.length > 0 && (
          <Card size="small" title={`已删除（${compDel.length} 条，可恢复）`}
                style={{ marginTop: 12, opacity: 0.9 }}>
            {compDel.map((r: any) => (
              <Space key={r.id} style={{ display: 'flex', marginBottom: 6 }} wrap>
                <Text delete type="secondary" style={{ fontSize: 12 }}>
                  {r.match_text} → {r.fill_text}
                </Text>
                <Button size="small" onClick={() => restoreComp(r)}>恢复</Button>
              </Space>
            ))}
          </Card>
        )}
      </Drawer>

      <Modal
        title="导入 markdown"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={doImportAll}
        okText="导入"
        confirmLoading={importing}
        width={860}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="格式跟导出的文件一致：材料用「# 查数口径」加「## 节标题 （必带）」分节；文本键用「# 名称（key）」整段覆盖；补全表用「# 补全表」加表格。只覆盖文件里出现的块，没出现的一行不动。"
        />
        <input
          type="file"
          accept=".md,.txt,text/markdown,text/plain"
          style={{ marginBottom: 8 }}
          onChange={(e) => pickFile(e.target.files?.[0] || null)}
        />
        <Input.TextArea
          rows={16}
          value={importBody}
          onChange={(e) => setImportBody(e.target.value)}
          placeholder={'# 查数口径\n\n## 一、表说明 （必带）\n\n正文…'}
          style={{ fontSize: 12 }}
        />
      </Modal>

    </div>
  )
}
