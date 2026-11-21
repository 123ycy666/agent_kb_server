import { useEffect, useState } from 'react'
import { Button, Collapse, Input, Select, Space, Table, Tag, Typography, Upload, message } from 'antd'
import {
  DownloadOutlined,
  ExportOutlined,
  PlusOutlined,
  SaveOutlined,
  UploadOutlined,
} from '@ant-design/icons'
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

/** 纠错词典：四张表可逐行增删改查（分页 + 搜索看全），五类可批量导入。 */
export function LexiconEditor() {
  const [items, setItems] = useState<any[]>([])
  const [tables, setTables] = useState<any[]>([])
  const [key, setKey] = useState('typo')
  const [cols, setCols] = useState<any[]>([])
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [saving, setSaving] = useState<number | null>(null)
  const [impKey, setImpKey] = useState('catalog')
  const [impMode, setImpMode] = useState('append')
  const [impBusy, setImpBusy] = useState(false)
  const [impResult, setImpResult] = useState<any>(null)
  const [probeQ, setProbeQ] = useState('')
  const [probe, setProbe] = useState<any>(null)
  const [probing, setProbing] = useState(false)
  const [reloading, setReloading] = useState(false)

  const loadStatus = () =>
    promptsApi
      .lexiconStatus()
      .then((r: any) => setItems(r?.items ?? []))
      .catch((e) => message.error((e as Error).message))

  const loadTables = () =>
    promptsApi
      .lexiconTables()
      .then((r: any) => {
        const list = r?.tables ?? []
        setTables(list)
        if (list.length && !list.find((t: any) => t.key === key)) setKey(list[0].key)
      })
      .catch((e) => message.error((e as Error).message))

  const loadRows = (k = key, kw = q, pg = page, ps = pageSize) =>
    promptsApi
      .lexicon(k, kw, pg, ps)
      .then((r: any) => {
        setCols(r.cols ?? [])
        setRows(r.rows ?? [])
        setTotal(Number(r.total ?? 0))
      })
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    void loadStatus()
    void loadTables()
  }, [])
  useEffect(() => {
    void loadRows(key, q, page, pageSize)
  }, [key])

  const switchKey = (k: string) => {
    setKey(k)
    setQ('')
    setPage(1)
  }

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
        await loadRows()
        await loadTables()
        await loadStatus()
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
      await loadRows()
      await loadTables()
      await loadStatus()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const doImport = async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    form.append('key', impKey)
    form.append('mode', impMode)
    setImpBusy(true)
    setImpResult(null)
    try {
      const r: any = await promptsApi.lexiconImport(form)
      setImpResult(r)
      if (r?.error) message.error(r.error)
      else {
        message.success(
          `导入完成：新增 ${r.inserted}，更新 ${r.updated}，跳过 ${r.skipped}，失败 ${
            (r.failed ?? []).length
          }`,
        )
        await loadStatus()
        await loadTables()
        await loadRows(key, q, 1, pageSize)
        setPage(1)
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setImpBusy(false)
    }
  }

  const reload = async () => {
    setReloading(true)
    try {
      const r: any = await promptsApi.lexiconReload()
      if (r?.ok) message.success('纠错引擎已重建，现在就是新词典')
      else message.error(r?.error || '重载失败')
      await loadStatus()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setReloading(false)
    }
  }

  const probeOne = async () => {
    if (!probeQ.trim()) return
    setProbing(true)
    try {
      setProbe(await promptsApi.lexiconTry(probeQ))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setProbing(false)
    }
  }

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space wrap size={6}>
        {items.map((s) => (
          <Tag key={s.key} color={s.rows ? 'green' : 'default'}>
            {String(s.name).split('（')[0]}：{s.rows}
          </Tag>
        ))}
      </Space>
      {items
        .filter((s) => s.hint)
        .map((s) => (
          <Text key={s.key} type="warning">
            · {s.hint}
          </Text>
        ))}

      <Collapse
        ghost
        size="small"
        items={[
          {
            key: 'how',
            label: '拼音怎么参与纠错（导之前先看这段）',
            children: (
              <Space direction="vertical" size={6}>
                <Text>
                  同音判断靠「拼音码」：每个汉字查拼音表得到（声母, 韵母），把声母韵母按模糊音合并
                  后逐字拼成一个串。合并规则是 zh→z、ch→c、sh→s、n/l→l、f/h→h、j→z、q→c、x→s，
                  ang→an、eng→en、ing→in、ong→on、uo→o、ie→e。
                </Text>
                <Text>
                  所以「环谭供电所」和库里的「环潭供电所」拼出来一模一样，直接对上。名称库的拼音码列
                  由系统导入时现算，不用填；手敲对不上，这条名字就永远匹配不上，而且不报错。
                </Text>
                <Text>
                  完整名称匹配还要两个条件：片段里带类别词（供电所 / 供电公司 / 服务站）才去名称库找，
                  相似度 ≥88 才改；同音候选多于一个只提示不改。
                </Text>
                <Text type="secondary">
                  导入顺序：拼音表 → 单位规则 → 名称库 → 错别字 / 标准词。导入后 60 秒自动重载，
                  点「立即生效」不用等。
                </Text>
              </Space>
            ),
          },
          {
            key: 'import',
            label: '批量导入 / 导出（CSV、XLSX）',
            children: (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Space wrap>
                  <Select
                    size="small"
                    style={{ width: 280 }}
                    value={impKey}
                    onChange={setImpKey}
                    options={items.map((s) => ({ value: s.key, label: s.name }))}
                  />
                  <Select
                    size="small"
                    style={{ width: 220 }}
                    value={impMode}
                    onChange={setImpMode}
                    options={[
                      { value: 'append', label: '追加（同名的跳过）' },
                      { value: 'replace', label: '替换（先清空这一类）' },
                    ]}
                  />
                  <Button
                    size="small"
                    icon={<DownloadOutlined />}
                    onClick={() => void promptsApi.lexiconDownload('template', impKey)}
                  >
                    下载模板
                  </Button>
                  <Button
                    size="small"
                    icon={<ExportOutlined />}
                    onClick={() => void promptsApi.lexiconDownload('export', impKey)}
                  >
                    导出现有
                  </Button>
                  <Upload
                    accept=".csv,.xlsx,.xlsm"
                    showUploadList={false}
                    beforeUpload={(f) => {
                      void doImport(f as File)
                      return false
                    }}
                  >
                    <Button size="small" type="primary" loading={impBusy} icon={<UploadOutlined />}>
                      选文件导入
                    </Button>
                  </Upload>
                </Space>
                <Text type="secondary">
                  表头照模板填，中文列名和库列名都认。导入的拼音码一律由系统现算，文件里填了也不采用。
                </Text>
                {impResult ? (
                  impResult.error ? (
                    <Text type="danger">{impResult.error}</Text>
                  ) : (
                    <Space direction="vertical" size={2}>
                      <Text>
                        共 {impResult.total} 行：新增 {impResult.inserted}，更新 {impResult.updated}，
                        跳过 {impResult.skipped}，失败 {(impResult.failed ?? []).length}
                      </Text>
                      {impResult.warning ? <Text type="warning">{impResult.warning}</Text> : null}
                      {(impResult.failed ?? []).slice(0, 8).map((f: any) => (
                        <Text key={f.row} type="danger">
                          第 {f.row} 行：{f.reason}
                        </Text>
                      ))}
                    </Space>
                  )
                ) : null}
              </Space>
            ),
          },
        ]}
      />

      <Space wrap>
        <Select
          size="small"
          style={{ width: 220 }}
          value={key}
          onChange={switchKey}
          options={tables.map((t) => ({ value: t.key, label: `${t.name}（${t.rows}）` }))}
        />
        <Input.Search
          size="small"
          allowClear
          style={{ width: 220 }}
          placeholder="按内容搜"
          onSearch={(v) => {
            setQ(v)
            setPage(1)
            void loadRows(key, v, 1, pageSize)
          }}
        />
        <Text type="secondary">共 {total} 条</Text>
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
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: [20, 50, 100],
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
            void loadRows(key, q, p, ps)
          },
        }}
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

      <Space wrap>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setRows((p) => [{}, ...p])}>
          加一行
        </Button>
        <Button size="small" loading={reloading} onClick={() => void reload()}>
          立即生效
        </Button>
        <Tag>名称库与拼音表在「批量导入」里，不走逐行编辑</Tag>
      </Space>

      <Space wrap>
        <Input
          size="small"
          style={{ width: 420 }}
          placeholder="试一句：看现在的词典把这句话改成什么"
          value={probeQ}
          onChange={(e) => setProbeQ(e.target.value)}
          onPressEnter={() => void probeOne()}
        />
        <Button size="small" loading={probing} onClick={() => void probeOne()}>
          试一句
        </Button>
        {probe ? (
          probe.error ? (
            <Text type="danger">{probe.error}</Text>
          ) : (
            <Space wrap size={4}>
              <Text type="secondary">{probe.original}</Text>
              <Text>→</Text>
              <Text strong>{probe.text}</Text>
              {probe.used ? <Tag color="green">已纠错</Tag> : <Tag>没改动</Tag>}
            </Space>
          )
        ) : null}
      </Space>
    </Space>
  )
}
