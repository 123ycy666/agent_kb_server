import { useEffect, useState } from 'react'
import { Card, Select, Space, Table, Tag, Typography, message } from 'antd'
import { docApi, kbApi } from '../api'
import type { DocItem, KnowledgeBase } from '../api'

const { Text } = Typography

const STATUS: Record<string, { color: string; label: string }> = {
  ready: { color: 'green', label: '可检索' },
  pending: { color: 'default', label: '待处理' },
  parsing: { color: 'processing', label: '解析中' },
  embedding: { color: 'processing', label: '建索引' },
  failed: { color: 'red', label: '失败' },
}

/** 资料库：真正的资料在 kb.document 里，不在 kb.spec。这页按库列出来。 */
export default function MaterialLibrary() {
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [kbId, setKbId] = useState<number | null>(null)
  const [rows, setRows] = useState<DocItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    kbApi
      .list(1, 50)
      .then((r) => {
        const items = r.items ?? []
        setBases(items)
        if (items.length) setKbId(items[0].id)
      })
      .catch((e) => message.error((e as Error).message))
  }, [])

  useEffect(() => {
    if (!kbId) return
    setLoading(true)
    docApi
      .list(kbId, { page: 1, page_size: 100 })
      .then((r) => {
        setRows(r.items ?? [])
        setTotal(r.total ?? 0)
      })
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false))
  }, [kbId])

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space wrap>
        <Text type="secondary">资料库：</Text>
        <Select
          style={{ minWidth: 220 }}
          value={kbId ?? undefined}
          onChange={setKbId}
          options={bases.map((b) => ({ value: b.id, label: `${b.name}（#${b.id}）` }))}
        />
        <Text type="secondary">共 {total} 篇（只列当前账号可见的）</Text>
      </Space>
      <Table
        size="small"
        rowKey="id"
        loading={loading}
        dataSource={rows}
        pagination={false}
        columns={[
          { title: '文件', dataIndex: 'filename' },
          { title: '格式', dataIndex: 'file_ext', width: 80 },
          { title: '版本', dataIndex: 'version_no', width: 70 },
          {
            title: '状态',
            width: 100,
            render: (_, r) => {
              const s = STATUS[r.parse_status] ?? { color: 'default', label: r.parse_status }
              return <Tag color={s.color}>{s.label}</Tag>
            },
          },
          { title: '切片', dataIndex: 'chunk_count', width: 70 },
          {
            title: '可见',
            width: 80,
            render: (_, r) =>
              r.visibility === 'public' ? <Tag>公开</Tag> : <Tag color="orange">本人</Tag>,
          },
          {
            title: '标签',
            render: (_, r) =>
              (r.tags ?? []).length ? (
                <Space wrap size={2}>
                  {(r.tags ?? []).map((t) => (
                    <Tag key={t}>{t}</Tag>
                  ))}
                </Space>
              ) : (
                <Text type="secondary">—</Text>
              ),
          },
          { title: '上传时间', dataIndex: 'created_at', width: 160 },
        ]}
      />
      <Card size="small" type="inner" title="和「说明书库」的区别">
        <Text type="secondary">
          说明书库进注入目录，命中后模型读正文照着做；资料库只参与检索，不写进目录。
          两个页签读的是两张表：说明书读 kb.spec，资料读 kb.document。
        </Text>
      </Card>
    </Space>
  )
}
