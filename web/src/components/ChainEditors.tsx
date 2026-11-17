import { useEffect, useState } from 'react'
import { Button, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import { PlusOutlined, SaveOutlined } from '@ant-design/icons'
import { injectApi, promptsApi } from '../api'

const { Text } = Typography

/** 入口分流判据：整份 JSON，直接编。 */
export function RouteRuleEditor() {
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)

  const load = () =>
    injectApi
      .routeRule()
      .then((r: any) => setText(JSON.stringify(r?.values ?? r, null, 2)))
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    void load()
  }, [])

  const save = async () => {
    let parsed: any
    try {
      parsed = JSON.parse(text)
    } catch (e) {
      message.error('JSON 格式不对：' + (e as Error).message)
      return
    }
    setSaving(true)
    try {
      const r: any = await injectApi.saveRouteRule(parsed)
      if (r?.error) message.error(r.error)
      else {
        message.success('已保存，下一轮生效')
        await load()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Text type="secondary">产物词表 / 产出性动词 / 交付说法 / 指代文件的说法，整份 JSON。</Text>
      <Input.TextArea
        autoSize={{ minRows: 14, maxRows: 30 }}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <Space>
        <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
          保存判据
        </Button>
        <Text type="secondary">存完下一轮生效</Text>
      </Space>
    </Space>
  )
}

/** 补全表：整表可编辑。 */
export function CompleteEditor() {
  const [rows, setRows] = useState<any[]>([])
  const [saving, setSaving] = useState(false)

  const load = () =>
    promptsApi
      .complete()
      .then((r: any) => setRows((r.rows ?? []).map((x: any) => ({ ...x }))))
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    void load()
  }, [])

  const setCell = (i: number, k: string, v: any) =>
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, [k]: v } : r)))

  const save = async () => {
    setSaving(true)
    try {
      const payload = rows.map((r) => ({
        id: r.id,
        kind: r.kind || 'norm',
        match_text: r.match_text || '',
        fill_text: r.fill_text || '',
        priority: Number(r.priority ?? 0),
      }))
      const res: any = await promptsApi.saveComplete(payload)
      if (res?.ok) {
        message.success('已保存，下一轮生效')
        await load()
      } else message.error(res?.error || '保存失败')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Table
        size="small"
        rowKey={(r: any, i?: number) => String(r.id ?? i)}
        dataSource={rows}
        pagination={false}
        scroll={{ x: 'max-content' }}
        columns={[
          {
            title: '匹配词',
            dataIndex: 'match_text',
            width: 160,
            render: (v: string, _: any, i: number) => (
              <Input size="small" value={v} onChange={(e) => setCell(i, 'match_text', e.target.value)} />
            ),
          },
          {
            title: '补进去的内容',
            dataIndex: 'fill_text',
            render: (v: string, _: any, i: number) => (
              <Input.TextArea
                autoSize={{ minRows: 1, maxRows: 4 }}
                value={v}
                onChange={(e) => setCell(i, 'fill_text', e.target.value)}
              />
            ),
          },
          {
            title: '优先级',
            dataIndex: 'priority',
            width: 90,
            render: (v: any, _: any, i: number) => (
              <Input size="small" value={v} onChange={(e) => setCell(i, 'priority', e.target.value)} />
            ),
          },
        ]}
      />
      <Space>
        <Button
          size="small"
          icon={<PlusOutlined />}
          onClick={() =>
            setRows((p) => [...p, { kind: 'norm', match_text: '', fill_text: '', priority: 0 }])
          }
        >
          加一行
        </Button>
        <Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
          保存整表
        </Button>
      </Space>
    </Space>
  )
}

/** 纠错词典：四张可编的表，逐行改、可增可删。 */
export function LexiconEditor() {
  const [tables, setTables] = useState<any[]>([])
  const [key, setKey] = useState('typo')
  const [cols, setCols] = useState<any[]>([])
  const [rows, setRows] = useState<any[]>([])
  const [saving, setSaving] = useState<number | null>(null)

  const loadTables = () =>
    promptsApi
      .lexiconTables()
      .then((r: any) => {
        const list = r?.tables ?? []
        setTables(list)
        if (list.length && !list.find((t: any) => t.key === key)) setKey(list[0].key)
      })
      .catch((e) => message.error((e as Error).message))

  const loadRows = (k: string) =>
    promptsApi
      .lexicon(k)
      .then((r: any) => {
        setCols(r.cols ?? [])
        setRows(r.rows ?? [])
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    void loadTables()
  }, [])
  useEffect(() => {
    void loadRows(key)
  }, [key])

  const setCell = (i: number, k: string, v: any) =>
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, [k]: v } : r)))

  const saveRow = async (row: any) => {
    const data: Record<string, unknown> = {}
    for (const c of cols) data[c.key] = row[c.key] ?? ''
    setSaving(row.id ?? -1)
    try {
      const res: any = row.id
        ? await promptsApi.lexiconUpdate(key, Number(row.id), data)
        : await promptsApi.lexiconCreate(key, data)
      if (res?.error) message.error(res.error)
      else {
        message.success('已保存，60 秒内生效')
        await loadRows(key)
        await loadTables()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(null)
    }
  }

  const del = async (row: any) => {
    if (!row.id) return
    try {
      await promptsApi.lexiconDelete(key, Number(row.id))
      message.success('已删除，60 秒内生效')
      await loadRows(key)
      await loadTables()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Space wrap>
        <Select
          size="small"
          style={{ width: 220 }}
          value={key}
          onChange={setKey}
          options={tables.map((t) => ({ value: t.key, label: `${t.name}（${t.rows}）` }))}
        />
        {tables
          .filter((t) => t.key === key)
          .map((t) => (
            <Text key={t.key} type="secondary">
              {t.note}
            </Text>
          ))}
      </Space>

      <Table
        size="small"
        rowKey={(r: any, i?: number) => String(r.id ?? `new-${i}`)}
        dataSource={rows}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        scroll={{ x: 'max-content' }}
        columns={[
          ...cols.map((c) => ({
            title: c.label,
            dataIndex: c.key,
            width: 160,
            render: (v: any, _: any, i: number) => (
              <Input size="small" value={v ?? ''} onChange={(e) => setCell(i, c.key, e.target.value)} />
            ),
          })),
          {
            title: '操作',
            width: 140,
            render: (_: any, row: any) => (
              <Space size={4}>
                <Button
                  size="small"
                  type="link"
                  loading={saving === (row.id ?? -1)}
                  onClick={() => void saveRow(row)}
                >
                  {row.id ? '存' : '新建'}
                </Button>
                {row.id ? (
                  <Button size="small" type="link" danger onClick={() => void del(row)}>
                    删
                  </Button>
                ) : null}
              </Space>
            ),
          },
        ]}
      />

      <Space>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setRows((p) => [{}, ...p])}>
          加一行
        </Button>
        <Tag>名称库与拼音库是离线编译的，不在页面里改</Tag>
      </Space>
    </Space>
  )
}
