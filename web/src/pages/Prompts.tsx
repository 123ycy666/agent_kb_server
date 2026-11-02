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
  message,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { EditOutlined, LockOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import { http } from '../api/client'

const { Title, Text } = Typography

type Mat = {
  key: string
  name: string
  editable: boolean
  always?: boolean
  when?: string
  textKey?: string
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
  const [saving, setSaving] = useState(false)
  const [importText, setImportText] = useState('')
  const [showImport, setShowImport] = useState(false)
  const [compOpen, setCompOpen] = useState(false)
  const [compRows, setCompRows] = useState<any[]>([])
  const [compSaving, setCompSaving] = useState(false)
  const [txtOpen, setTxtOpen] = useState(false)
  const [txtKey, setTxtKey] = useState('')
  const [txtName, setTxtName] = useState('')
  const [txtBody, setTxtBody] = useState('')
  const [txtOverridden, setTxtOverridden] = useState(false)
  const [txtSaving, setTxtSaving] = useState(false)

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

  const loadComplete = async () => {
    try {
      const r: any = await http.get('/prompts/complete')
      setCompRows(r.rows || [])
      setCompOpen(true)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const saveComplete = async () => {
    setCompSaving(true)
    try {
      const r: any = await http.put('/prompts/complete', { rows: compRows })
      if (r?.ok) {
        message.success(`补全表已保存（${r.rows} 条），下一轮问答生效`)
        setCompOpen(false)
        await loadTree()
      } else {
        message.error(r?.error || '保存失败')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setCompSaving(false)
    }
  }

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

  const totalChars = useMemo(
    () => nodes.reduce((s, n) => s + n.materials.reduce((a, m) => a + m.chars, 0), 0),
    [nodes]
  )

  const openMaterial = async (m: Mat) => {
    // 补全表走自己的编辑器（行级可改），不是只读文本
    if (m.key === 'mat.complete') {
      await loadComplete()
      return
    }
    // 身份 / 汇报口吻 / 模式做法 / 词表：走文本编辑器（存 prompt_text，可覆盖）
    if (m.textKey) {
      await loadText(m.textKey, m.name)
      return
    }
    setCurrent(m)
    setOpen(true)
    setShowImport(false)
    setImportText('')
    const key = SLOT_KEYS[m.key]
    if (key) {
      try {
        const r: any = await http.get(`/prompts/material/${key}`)
        setSections(r.sections || [])
      } catch (e) {
        setSections([])
        message.error((e as Error).message)
      }
    } else {
      setSections([])
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
      const r: any = await http.post(`/prompts/material/${slot}/import`, { text: importText })
      if (r?.ok) {
        message.success(`已按标题切成 ${r.sections} 节并覆盖`)
        const rr: any = await http.get(`/prompts/material/${slot}`)
        setSections(rr.sections || [])
        setShowImport(false)
        setImportText('')
        await loadTree()
      } else {
        message.error(r?.error || '导入失败')
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
      message.success('已删除（真删，get_rule 也取不到了）')
      await loadTree()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const setSectionBody = (idx: number, body: string) => {
    setSections((prev) => prev.map((s, i) => (i === idx ? { ...s, body } : s)))
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
          整条问答链路共 5 份材料；点节点右侧的材料可查看与编辑
        </Text>
        <Button icon={<ReloadOutlined />} onClick={loadTree}>
          刷新
        </Button>
        <Button onClick={loadComplete}>补全表</Button>
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

            <Space style={{ marginBottom: 12 }}>
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
              <Text type="secondary" style={{ fontSize: 12 }}>
                自己加判断类型（如「十三、新的审批条件」），填完点右上角「保存」
              </Text>
            </Space>

            {sections.length === 0 ? (
              <Empty description="这一份还是空的，可以上传 md 或直接在这里写" />
            ) : null}

            {sections.map((s, i) => (
              <div key={i} style={{ marginBottom: 16 }}>
                <Space align="center" wrap>
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
                    description={`删了就没了：正文、标题一起删，get_rule 也取不到「${s.section}」。`}
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
        title="补全表（代码读，不进提示词）"
        width={760}
        open={compOpen}
        onClose={() => setCompOpen(false)}
        extra={
          <Button type="primary" loading={compSaving} onClick={saveComplete}>
            保存
          </Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="左边是「用户可能这么问」，右边是「照哪个口径答」。这一步在判题型之前跑，只有像业务问题时才用它 —— 所以写通知、闲聊不会被它改。"
        />
        {compRows.map((r, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <Input
              style={{ width: 210 }}
              value={r.match_text}
              placeholder="用户可能这么问"
              onChange={(e) =>
                setCompRows((prev) =>
                  prev.map((x, j) => (j === i ? { ...x, match_text: e.target.value } : x))
                )
              }
            />
            <Input
              value={r.fill_text}
              placeholder="照哪个口径答"
              onChange={(e) =>
                setCompRows((prev) =>
                  prev.map((x, j) => (j === i ? { ...x, fill_text: e.target.value } : x))
                )
              }
            />
            <Button danger onClick={() => setCompRows((prev) => prev.filter((_, j) => j !== i))}>
              删
            </Button>
          </div>
        ))}
        <Button
          onClick={() =>
            setCompRows((prev) => [
              ...prev,
              { kind: 'norm', match_text: '', fill_text: '', priority: (prev.length + 1) * 10 },
            ])
          }
        >
          加一行
        </Button>
      </Drawer>
      <Drawer
        title={`${txtName}　${txtBody.length.toLocaleString()} 字`}
        width={860}
        open={txtOpen}
        onClose={() => setTxtOpen(false)}
        extra={
          <Space>
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
          description="模式做法和词表是 JSON，改值就行；身份和汇报口吻是纯文本。"
        />
        <Input.TextArea
          value={txtBody}
          onChange={(e) => setTxtBody(e.target.value)}
          autoSize={{ minRows: 18, maxRows: 30 }}
          style={{ fontSize: 12 }}
        />
      </Drawer>
    </div>
  )
}
