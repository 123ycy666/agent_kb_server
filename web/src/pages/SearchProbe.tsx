import { useEffect, useState } from 'react'
import { Button, Card, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { kbApi, catalogApi } from '../api'
import { http } from '../api/client'
import type { KnowledgeBase } from '../api'

const { Text, Paragraph } = Typography

/** 检索试一下：直接在页面上看召回结果与出处，不用去问数页。 */
export default function SearchProbe() {
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [kbId, setKbId] = useState<number | null>(null)
  const [cats, setCats] = useState<any[]>([])
  const [catId, setCatId] = useState<number | undefined>()
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [meta, setMeta] = useState<any>(null)

  useEffect(() => {
    kbApi.list(1, 50).then((r) => {
      const items = r.items ?? []
      setBases(items)
      const hit = items.find((b: any) => String(b.name).includes('制度')) || items[0]
      setKbId(hit?.id ?? null)
    })
    catalogApi
      .tree()
      .then((r) => setCats((r.items ?? []).filter((c: any) => c.enabled)))
      .catch(() => undefined)
  }, [])

  const run = async () => {
    if (!q.trim() || !kbId) return
    setLoading(true)
    try {
      const r: any = await http.post('/search', {
        query: q,
        kb_ids: [kbId],
        top_k: 5,
        catalog_id: catId ?? null,
      })
      setRows(r.results ?? [])
      setMeta({ total: r.total, degraded: r.degraded, notice: r.filter_notice })
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        这里跑的是问答用的同一条检索链路：只召现行、按密级与可见性裁剪，每条结果都带出处。
      </Paragraph>
      <Space wrap>
        <Select
          style={{ width: 220 }}
          value={kbId ?? undefined}
          onChange={setKbId}
          options={bases.map((b) => ({ value: b.id, label: `${b.name}（#${b.id}）` }))}
        />
        <Input
          style={{ width: 340 }}
          placeholder="例：差旅费住宿标准是多少"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onPressEnter={() => void run()}
        />
        <Select
          allowClear
          style={{ width: 200 }}
          placeholder="限定目录（含下级）"
          value={catId}
          onChange={setCatId}
          options={cats.map((c) => ({ value: c.id, label: c.path }))}
        />
        <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={() => void run()}>
          检索
        </Button>
        {meta ? (
          <Text type="secondary">
            命中 {meta.total} 条{meta.degraded ? ' · 精排不可用，按融合分排序' : ''}
          </Text>
        ) : null}
      </Space>

      <Table
        size="small"
        rowKey="chunk_id"
        pagination={false}
        dataSource={rows}
        columns={[
          { title: '文档', dataIndex: 'doc_name', width: 200 },
          {
            title: '出处',
            width: 260,
            render: (_, r: any) => (
              <Space direction="vertical" size={0}>
                <Text>{r.catalog_path || '未归类'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {[r.doc_no, r.effective_date].filter(Boolean).join(' · ') || '—'}
                </Text>
              </Space>
            ),
          },
          {
            title: '时效',
            width: 90,
            render: (_, r: any) =>
              r.doc_status === 'active' ? <Tag color="green">现行</Tag> : <Tag>{r.doc_status}</Tag>,
          },
          { title: '章节', dataIndex: 'heading_path', width: 140 },
          {
            title: '内容',
            render: (_, r: any) => (
              <Text style={{ fontSize: 12 }} ellipsis>
                {String(r.content || '').slice(0, 90)}
              </Text>
            ),
          },
          { title: '来源', width: 90, render: (_, r: any) => <Tag>{r.source}</Tag> },
        ]}
      />

      {meta?.notice ? (
        <Card size="small">
          <Text>{meta.notice.message}</Text>
          <div>
            <Text type="secondary">{meta.notice.suggestion}</Text>
          </div>
        </Card>
      ) : null}
    </Space>
  )
}
