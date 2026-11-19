import { useEffect, useState } from 'react'
import {
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlusOutlined, SaveOutlined } from '@ant-design/icons'
import { promptsApi } from '../api'

const { Text } = Typography

/** 原文预览：表格或整段文本。 */
export function BodyView({ data }: { data: any }) {
  if (!data) return <Empty description="读取中…" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  const rows: any[] = data.rows ?? []
  if (data.kind === 'rows' && rows.length) {
    const cols: string[] = data.columns ?? []
    return (
      <Table
        size="small"
        rowKey={(_, i?: number) => String(i)}
        dataSource={rows}
        scroll={{ x: 'max-content', y: 460 }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        columns={cols.map((c) => ({ title: c, dataIndex: c }))}
      />
    )
  }
  return (
    <pre
      style={{
        width: '100%',
        boxSizing: 'border-box',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        fontSize: 12,
        margin: 0,
        maxHeight: 620,
        overflow: 'auto',
      }}
    >
      {String(data.text ?? '')}
    </pre>
  )
}

/** 口径材料：分节编辑（可增删、改序、整份 md 导入）。 */
export function SectionList({ slot, onChanged }: { slot: string; onChanged: () => void }) {
  const [secs, setSecs] = useState<any[]>([])
  const [dead, setDead] = useState<any[]>([])
  const [saving, setSaving] = useState(false)
  const [showDead, setShowDead] = useState(false)
  const [impOpen, setImpOpen] = useState(false)
  const [impText, setImpText] = useState('')

  const load = () =>
    Promise.all([promptsApi.material(slot), promptsApi.materialDeleted(slot)])
      .then(([r, d]: any) => {
        setSecs(r.sections ?? [])
        setDead(d.rows ?? [])
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slot])

  const patch = (i: number, p: any) =>
    setSecs((prev) => prev.map((s, j) => (j === i ? { ...s, ...p } : s)))

  const move = (i: number, dir: -1 | 1) =>
    setSecs((prev) => {
      const j = i + dir
      if (j < 0 || j >= prev.length) return prev
      const next = [...prev]
      const [x] = next.splice(i, 1)
      next.splice(j, 0, x)
      return next
    })

  const save = async () => {
    const rows = secs.filter((s) => String(s.body ?? '').trim())
    if (!rows.length) {
      message.warning('还没有正文可存')
      return
    }
    setSaving(true)
    try {
      await promptsApi.materialSave(
        slot,
        rows.map((s, i) => ({
          id: s.id,
          section: s.section,
          body: s.body,
          pinned: s.pinned !== false,
          sort_no: i,
          version_no: s.version_no,
        }))
      )
      const skipped = secs.length - rows.length
      message.success(skipped ? `已保存；${skipped} 节没有正文，没存` : '已保存')
      await load()
      onChanged()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const del = async (s: any) => {
    try {
      await promptsApi.deleteSection(s.id)
      message.success('已删除，可在「已删除」里恢复')
      await load()
      onChanged()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const restore = async (s: any) => {
    try {
      await promptsApi.restoreSection(s.id)
      message.success('已恢复')
      await load()
      onChanged()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const doImport = async () => {
    setSaving(true)
    try {
      const r: any = await promptsApi.materialImport(slot, impText)
      message.success(`已导入 ${r.sections ?? 0} 节`)
      setImpOpen(false)
      setImpText('')
      await load()
      onChanged()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const chars = secs.reduce((n, s) => n + String(s.body ?? '').length, 0)
  const pinned = secs.filter((s) => s.pinned !== false).length

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space wrap>
        <Button
          size="small"
          icon={<PlusOutlined />}
          onClick={() =>
            setSecs((prev) => [...prev, { section: '新规则（点这里改标题）', body: '', pinned: true }])
          }
        >
          新增一节
        </Button>
        <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
          保存
        </Button>
        <Button size="small" onClick={() => setImpOpen(true)}>
          按 md 整份导入
        </Button>
        {dead.length ? (
          <Button type="link" size="small" danger onClick={() => setShowDead((v) => !v)}>
            已删除 {dead.length} 节
          </Button>
        ) : null}
        <Text type="secondary" style={{ fontSize: 12 }}>
          {secs.length} 节 · 必带 {pinned} 节 · {chars} 字
        </Text>
      </Space>

      {showDead
        ? dead.map((s: any) => (
            <Space key={s.id}>
              <Text delete>{s.section}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {String(s.body ?? '').length} 字
              </Text>
              <Button size="small" onClick={() => void restore(s)}>
                恢复
              </Button>
            </Space>
          ))
        : null}

      {secs.length ? null : <Empty description="还没有内容" image={Empty.PRESENTED_IMAGE_SIMPLE} />}

      {secs.map((s, i) => (
        <div key={s.id ?? `new-${i}`} style={{ width: '100%' }}>
          <Space wrap style={{ marginBottom: 4 }}>
            <Button size="small" type="text" disabled={i === 0} onClick={() => move(i, -1)} aria-label="上移">
              ↑
            </Button>
            <Button
              size="small"
              type="text"
              disabled={i === secs.length - 1}
              onClick={() => move(i, 1)}
              aria-label="下移"
            >
              ↓
            </Button>
            <Input
              size="small"
              value={s.section}
              placeholder="节标题"
              onChange={(e) => patch(i, { section: e.target.value })}
              style={{ width: 340, fontWeight: 600 }}
              aria-label="节标题"
            />
            <Tag.CheckableTag checked={s.pinned !== false} onChange={(v) => patch(i, { pinned: v })}>
              {s.pinned !== false ? '必带' : '按需'}
            </Tag.CheckableTag>
            <Text type="secondary">{String(s.body ?? '').length} 字</Text>
            {s.id ? (
              <Popconfirm
                title="删掉这一节？"
                description="不再进提示词；可在「已删除」里恢复。"
                okText="删除"
                cancelText="不删"
                okButtonProps={{ danger: true }}
                onConfirm={() => void del(s)}
              >
                <Button size="small" type="link" danger>
                  删除
                </Button>
              </Popconfirm>
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>
                还没保存
              </Text>
            )}
          </Space>
          <Input.TextArea
            autoSize={{ minRows: 8, maxRows: 24 }}
            value={s.body ?? ''}
            onChange={(e) => patch(i, { body: e.target.value })}
            aria-label="节正文"
          />
        </div>
      ))}

      <Modal
        open={impOpen}
        title="按 md 整份导入"
        width={760}
        okText="切节并覆盖"
        cancelText="取消"
        confirmLoading={saving}
        onOk={() => void doImport()}
        onCancel={() => setImpOpen(false)}
      >
        <Text type="secondary">按 ## / ### 标题切节，整份替换；原来的节软删，可在「已删除」里恢复。</Text>
        <Input.TextArea
          rows={12}
          style={{ marginTop: 8 }}
          value={impText}
          onChange={(e) => setImpText(e.target.value)}
        />
      </Modal>
    </Space>
  )
}

/** 文本键：整段编辑（问题确定、身份）。 */
export function TextKeyEditor({ keyName, onChanged }: { keyName: string; onChanged?: () => void }) {
  const [body, setBody] = useState('')
  const [saving, setSaving] = useState(false)

  const load = () =>
    promptsApi
      .texts()
      .then((r: any) => {
        const list: any[] = Array.isArray(r.items) ? r.items : Object.values(r.items ?? {})
        const v = list.find((x: any) => x.key === keyName || x.name === keyName)
        setBody(v?.body ?? '')
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyName])

  const save = async () => {
    setSaving(true)
    try {
      await promptsApi.saveTextKey(keyName, body)
      message.success('已保存')
      onChanged?.()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    try {
      await promptsApi.resetTextKey(keyName)
      message.success('已恢复默认')
      await load()
      onChanged?.()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Input.TextArea
        autoSize={{ minRows: 12, maxRows: 30 }}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        aria-label="正文"
      />
      <Space>
        <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
          保存
        </Button>
        <Popconfirm
          title="恢复成代码里的默认值？"
          description="页面上的改动会作废。"
          okText="恢复"
          cancelText="不恢复"
          onConfirm={() => void reset()}
        >
          <Button size="small" danger>
            恢复默认
          </Button>
        </Popconfirm>
        <Text type="secondary">{body.length} 字</Text>
      </Space>
    </Space>
  )
}

/** 补问法：开关 + 话术 + 三张清单。 */
export function AskfillEditor() {
  const [on, setOn] = useState(true)
  const [dflt, setDflt] = useState('')
  const [lists, setLists] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const load = () =>
    promptsApi
      .texts()
      .then((r: any) => {
        const list: any[] = Array.isArray(r.items) ? r.items : Object.values(r.items ?? {})
        const v = list.find((x: any) => x.key === 'askfill')
        let cfg: any = {}
        try {
          cfg = JSON.parse(v?.body || '{}')
        } catch {
          cfg = {}
        }
        setOn(cfg.on !== false)
        setDflt(String(cfg.default || ''))
        const next: Record<string, string> = {}
        for (const k of ['asked', 'topic', 'how']) {
          const arr = Array.isArray(cfg[k]) ? cfg[k] : []
          next[k] = arr.join('\n')
        }
        setLists(next)
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      const payload: any = {
        on,
        default: dflt,
        asked: (lists.asked || '').split('\n').map((s) => s.trim()).filter(Boolean),
        topic: (lists.topic || '').split('\n').map((s) => s.trim()).filter(Boolean),
        how: (lists.how || '').split('\n').map((s) => s.trim()).filter(Boolean),
      }
      await promptsApi.saveTextKey('askfill', JSON.stringify(payload, null, 2))
      message.success('已保存')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space>
        <Text strong>补问开关</Text>
        <Tag.CheckableTag checked={on} onChange={setOn}>
          {on ? '开：缺条件会补问一句' : '关：不补问，直接答'}
        </Tag.CheckableTag>
      </Space>
      <div>
        <Text strong>默认补问话术</Text>
        <Input value={dflt} onChange={(e) => setDflt(e.target.value)} aria-label="默认补问话术" />
      </div>
      {[
        ['asked', '会问哪些（一行一个）'],
        ['topic', '按主题问（一行一个）'],
        ['how', '怎么问（一行一个）'],
      ].map(([k, label]) => (
        <div key={k}>
          <Text strong>{label}</Text>
          <Input.TextArea
            autoSize={{ minRows: 4, maxRows: 12 }}
            value={lists[k] ?? ''}
            onChange={(e) => setLists((b) => ({ ...b, [k]: e.target.value }))}
            aria-label={label}
          />
        </div>
      ))}
      <Space>
        <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
          保存
        </Button>
        <Text type="secondary">存完下一轮生效</Text>
      </Space>
    </Space>
  )
}
