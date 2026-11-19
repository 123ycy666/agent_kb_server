import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Drawer,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import Page from '../components/ui/Page'
import { useTab } from '../components/ui/useTab'
import { docApi, kbApi, searchApi } from '../api'
import type { DocItem, KnowledgeBase, SearchResult } from '../api'

const { Text } = Typography

const dt = (v?: string | null) => (v ? String(v).slice(0, 16).replace('T', ' ') : '—')

const PARSE: Record<string, { text: string; color: string }> = {
  ready: { text: '已解析', color: 'green' },
  done: { text: '已解析', color: 'green' },
  processing: { text: '解析中', color: 'blue' },
  pending: { text: '排队中', color: 'gold' },
  failed: { text: '失败', color: 'red' },
}

function DocsTab({ kbs }: { kbs: KnowledgeBase[] }) {
  const [kbId, setKbId] = useState<number | undefined>(kbs[0]?.id)
  const [rows, setRows] = useState<DocItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [chunkDoc, setChunkDoc] = useState<DocItem | null>(null)
  const [chunks, setChunks] = useState<{ no: number; text: string }[]>([])

  useEffect(() => {
    if (kbId === undefined && kbs[0]) setKbId(kbs[0].id)
  }, [kbs, kbId])

  const load = useCallback(async (id = kbId, p = page) => {
    if (!id) return
    setLoading(true)
    try {
      const r = await docApi.list(id, { page: p, page_size: 20 })
      setRows(r.items ?? [])
      setTotal(r.total ?? 0)
    } catch (e: any) {
      message.error(e?.message || '文档列表拉不出来')
    } finally {
      setLoading(false)
    }
  }, [kbId, page])

  useEffect(() => {
    void load()
  }, [load])

  const upload = async (file: File) => {
    if (!kbId) return
    setUploading(true)
    try {
      const r = await docApi.uploadAndWait(kbId, file, true, [])
      if (r.status === 'failed') message.error(r.error_msg || '上传失败')
      else message.success(`已入库 ${file.name}${r.chunk_count ? `，${r.chunk_count} 段` : ''}`)
      await load(kbId, 1)
    } catch (e: any) {
      message.error(e?.message || '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const openChunks = async (doc: DocItem) => {
    setChunkDoc(doc)
    setChunks([])
    try {
      const list = await docApi.chunks(doc.id, 80)
      setChunks(
        (list ?? []).map((c: any, i: number) => ({
          no: c.chunk_no ?? i + 1,
          text: c.content ?? c.text ?? '',
        }))
      )
    } catch (e: any) {
      message.error(e?.message || '切片拉不出来')
    }
  }

  const columns: ColumnsType<DocItem> = [
    {
      title: '文件名',
      dataIndex: 'filename',
      ellipsis: { showTitle: false },
      render: (v: string, r) => (
        <Tooltip title={v} placement="topLeft">
          <a onClick={() => void openChunks(r)}>{v}</a>
        </Tooltip>
      ),
    },
    { title: '类型', dataIndex: 'file_ext', width: 80, render: (v: string) => <Tag>{v}</Tag> },
    {
      title: '切片数',
      dataIndex: 'chunk_count',
      width: 90,
      render: (v: number) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{v ?? 0}</span>,
    },
    {
      title: '可见性',
      dataIndex: 'visibility',
      width: 90,
      render: (v: string) => (
        <Tag color={v === 'public' ? 'blue' : 'default'}>{v === 'public' ? '公开' : '私有'}</Tag>
      ),
    },
    {
      title: '解析状态',
      dataIndex: 'parse_status',
      width: 100,
      render: (v: string) => {
        const m = PARSE[v] ?? { text: v || '—', color: 'default' }
        return <Tag color={m.color}>{m.text}</Tag>
      },
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 140,
      render: (v?: string) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{dt(v)}</span>,
    },
    {
      title: '操作',
      width: 90,
      render: (_: unknown, r) => (
        <Button type="link" size="small" onClick={() => void openChunks(r)} aria-label={`看切片 ${r.filename}`}>
          看切片
        </Button>
      ),
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          style={{ width: 220 }}
          value={kbId}
          onChange={(v) => {
            setKbId(v)
            setPage(1)
            void load(v, 1)
          }}
          options={kbs.map((k) => ({ value: k.id, label: k.name }))}
          aria-label="选库"
        />
        <Upload
          showUploadList={false}
          accept=".pdf,.docx,.xlsx,.md,.txt,.csv"
          beforeUpload={(f) => {
            void upload(f as File)
            return false
          }}
        >
          <Button icon={<UploadOutlined />} loading={uploading}>
            上传
          </Button>
        </Upload>
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>
          刷新
        </Button>
        <Text type="secondary">共 {total} 篇</Text>
      </Space>

      <Table<DocItem>
        size="small"
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          showSizeChanger: false,
          onChange: (p) => {
            setPage(p)
            void load(kbId, p)
          },
        }}
        locale={{ emptyText: <Empty description="这个库还没有文档" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
      />

      <Drawer
        open={chunkDoc != null}
        width={720}
        title={chunkDoc ? `${chunkDoc.filename} · 切片` : ''}
        onClose={() => setChunkDoc(null)}
      >
        {chunks.length === 0 ? (
          <Empty description="还没有切片" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          chunks.map((c) => (
            <div key={c.no} className="kb-chunk">
              <Text type="secondary" style={{ fontSize: 12 }}>
                #{c.no}
              </Text>
              <div>{c.text}</div>
            </div>
          ))
        )}
      </Drawer>
    </>
  )
}

function LibsTab({ kbs, loading }: { kbs: KnowledgeBase[]; loading: boolean }) {
  const columns: ColumnsType<KnowledgeBase> = [
    { title: '名称', dataIndex: 'name', render: (v: string) => <Text strong>{v}</Text> },
    {
      title: '文档数',
      dataIndex: 'document_count',
      width: 100,
      render: (v: number) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{v ?? 0}</span>,
    },
    {
      title: '切片数',
      dataIndex: 'chunk_count',
      width: 100,
      render: (v: number) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{v ?? 0}</span>,
    },
    {
      title: '参与检索',
      dataIndex: 'qa_enabled',
      width: 100,
      render: (v: boolean) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 150,
      render: (v?: string) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{dt(v)}</span>,
    },
  ]
  return (
    <Table<KnowledgeBase>
      size="small"
      rowKey="id"
      loading={loading}
      columns={columns}
      dataSource={kbs}
      pagination={false}
    />
  )
}

function SearchTab({ kbs }: { kbs: KnowledgeBase[] }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)

  const run = async () => {
    const text = q.trim()
    if (!text) return
    setLoading(true)
    try {
      const ids = kbs.filter((k) => k.qa_enabled).map((k) => k.id)
      const r = await searchApi.search({ query: text, kb_ids: ids, top_k: 8 })
      setRows(r.results ?? [])
      if (!r.results?.length) message.info('没有命中')
    } catch (e: any) {
      message.error(e?.message || '检索失败')
    } finally {
      setLoading(false)
    }
  }

  const columns: ColumnsType<SearchResult> = [
    { title: '文档名', dataIndex: 'doc_name', width: 220, ellipsis: true },
    {
      title: '相似度',
      width: 90,
      render: (_: unknown, r) => {
        const s = r.rerank_score ?? r.fused_score ?? r.vector_score ?? 0
        return <span style={{ fontVariantNumeric: 'tabular-nums' }}>{Number(s).toFixed(3)}</span>
      },
    },
    {
      title: '片段',
      dataIndex: 'content',
      render: (v: string) => (
        <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.7, maxHeight: 140, overflow: 'auto' }}>
          {v}
        </div>
      ),
    },
  ]

  return (
    <>
      <Space.Compact style={{ width: '100%', maxWidth: 560, marginBottom: 12 }}>
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onPressEnter={() => void run()}
          placeholder="输入一句话检索"
          aria-label="检索内容"
        />
        <Button type="primary" loading={loading} onClick={() => void run()}>
          检索
        </Button>
      </Space.Compact>
      <Table<SearchResult>
        size="small"
        rowKey={(r) => String(r.chunk_id)}
        columns={columns}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: <Empty description="还没有检索" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
      />
    </>
  )
}

export default function Kb() {
  const [tab, setTab] = useTab('docs', ['docs', 'libs', 'search'])
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(false)

  const loadKbs = useCallback(async () => {
    setLoading(true)
    try {
      const r = await kbApi.list(1, 100)
      setKbs(r.items ?? [])
    } catch (e: any) {
      message.error(e?.message || '库列表拉不出来')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadKbs()
  }, [loadKbs])

  return (
    <Page title="知识库">
      <div style={{ background: '#fff', borderRadius: 8, padding: 16 }}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            { key: 'docs', label: '文档', children: <DocsTab kbs={kbs} /> },
            { key: 'libs', label: '库', children: <LibsTab kbs={kbs} loading={loading} /> },
            { key: 'search', label: '检索测试', children: <SearchTab kbs={kbs} /> },
          ]}
        />
      </div>
    </Page>
  )
}
