import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  DatePicker,
  Descriptions,
  Drawer,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { EditOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { catalogApi, kbApi } from '../api'
import type { KnowledgeBase } from '../api'

const { Text, Paragraph } = Typography

const KIND_OPTIONS = ['制度', '办法', '细则', '规定', '规范', '通知', '通报', '请示',
  '报告', '批复', '函', '纪要', '模板', '台账', '流程', '方案'].map((v) => ({ value: v, label: v }))

const STATUS_LABEL: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: '待发布' },
  active: { color: 'green', label: '现行' },
  superseded: { color: 'orange', label: '已废止' },
  archived: { color: 'default', label: '已归档' },
}
const SECRECY_LABEL: Record<string, { color: string; label: string }> = {
  public: { color: 'default', label: '公开' },
  internal: { color: 'blue', label: '内部' },
  sensitive: { color: 'red', label: '敏感' },
}

/** 资料库：真正的资料在 kb.document 里，不在 kb.spec。这页按库列出来，并可归类。 */
export default function MaterialLibrary() {
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [kbId, setKbId] = useState<number | null>(null)
  const [rows, setRows] = useState<any[]>([])
  const [cats, setCats] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [edit, setEdit] = useState<any>(null)
  const [form, setForm] = useState<any>({})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    kbApi
      .list(1, 50)
      .then((r) => {
        const items = r.items ?? []
        setBases(items)
        if (items.length) setKbId(items[0].id)
      })
      .catch((e) => message.error((e as Error).message))
    catalogApi.tree().then((r) => setCats(r.items ?? [])).catch(() => undefined)
  }, [])

  const load = () => {
    if (!kbId) return
    setLoading(true)
    catalogApi
      .docs(kbId)
      .then((r: any) => setRows(r.items ?? []))
      .catch((e: any) => message.error(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [kbId])

  const catOptions = useMemo(
    () => cats.filter((c) => c.enabled).map((c) => ({ value: c.id, label: c.path })),
    [cats],
  )

  const openEdit = (r: any) => {
    setEdit(r)
    setForm({
      catalog_id: r.catalog_id ?? null,
      doc_kind: r.doc_kind || undefined,
      issuer: r.issuer || '',
      doc_no: r.doc_no || '',
      signed_on: r.signed_on || null,
      effective_from: r.effective_from || null,
      status: r.status || 'active',
      secrecy: r.secrecy || 'internal',
    })
  }

  const save = async () => {
    if (!edit) return
    if (!form.catalog_id) return message.warning('必须选一个主目录')
    if (!form.doc_kind) return message.warning('必须选文种')
    setSaving(true)
    try {
      await catalogApi.saveDoc(edit.id, form)
      message.success('已保存，下一轮检索按它过滤')
      setEdit(null)
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        一篇一个主目录；文种必选；时效与密级决定它出现在哪些检索结果里。点「归类」补元数据。
      </Paragraph>

      <Space wrap>
        <Text type="secondary">资料库：</Text>
        <Select
          style={{ minWidth: 240 }}
          value={kbId ?? undefined}
          onChange={setKbId}
          options={bases.map((b) => ({ value: b.id, label: `${b.name}（#${b.id}）` }))}
        />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>
          刷新
        </Button>
        <Text type="secondary">共 {rows.length} 篇</Text>
      </Space>

      <Table
        size="small"
        rowKey="id"
        loading={loading}
        pagination={false}
        dataSource={rows}
        columns={[
          { title: '文件', dataIndex: 'filename' },
          {
            title: '目录',
            width: 170,
            render: (_, r: any) =>
              r.catalog_path ? <Text>{r.catalog_path}</Text> : <Tag color="red">未归类</Tag>,
          },
          {
            title: '文种',
            width: 90,
            render: (_, r: any) => (r.doc_kind ? <Tag>{r.doc_kind}</Tag> : <Tag color="red">缺</Tag>),
          },
          { title: '文号', width: 170, render: (_, r: any) => r.doc_no || <Text type="secondary">—</Text> },
          {
            title: '时效',
            width: 100,
            render: (_, r: any) => {
              const s = STATUS_LABEL[r.status] ?? { color: 'default', label: r.status }
              return <Tag color={s.color}>{s.label}</Tag>
            },
          },
          {
            title: '密级',
            width: 90,
            render: (_, r: any) => {
              const s = SECRECY_LABEL[r.secrecy] ?? { color: 'default', label: r.secrecy }
              return <Tag color={s.color}>{s.label}</Tag>
            },
          },
          { title: '切片', dataIndex: 'chunk_count', width: 70 },
          {
            title: '操作',
            width: 100,
            render: (_, r: any) => (
              <Space size={4}>
                <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
                  归类
                </Button>
                {r.status === 'draft' ? (
                  <Button
                    size="small"
                    type="primary"
                    onClick={() =>
                      catalogApi
                        .saveDoc(r.id, { status: 'active' })
                        .then(() => {
                          message.success('已发布，60 秒内可被检索')
                          load()
                        })
                        .catch((e: any) => message.error(e.message))
                    }
                  >
                    发布
                  </Button>
                ) : null}
              </Space>
            ),
          },
        ]}
      />

      <Drawer
        open={!!edit}
        onClose={() => setEdit(null)}
        width={620}
        title={edit ? `归类：${edit.filename}` : ''}
        extra={
          <Button type="primary" loading={saving} onClick={() => void save()}>
            保存
          </Button>
        }
      >
        {edit ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="文件">{edit.filename}</Descriptions.Item>
              <Descriptions.Item label="切片数">{edit.chunk_count}</Descriptions.Item>
              <Descriptions.Item label="生效方式">
                保存后下一轮检索按目录、时效、密级过滤
              </Descriptions.Item>
            </Descriptions>

            <div>
              <Text strong>主目录（必填）</Text>
              <Select
                style={{ width: '100%' }}
                showSearch
                value={form.catalog_id ?? undefined}
                onChange={(v) => setForm((f: any) => ({ ...f, catalog_id: v }))}
                options={catOptions}
              />
            </div>
            <div>
              <Text strong>文种（必填）</Text>
              <Select
                style={{ width: '100%' }}
                value={form.doc_kind}
                onChange={(v) => setForm((f: any) => ({ ...f, doc_kind: v }))}
                options={KIND_OPTIONS}
              />
            </div>
            <div>
              <Text strong>发文单位</Text>
              <Input
                value={form.issuer}
                onChange={(e) => setForm((f: any) => ({ ...f, issuer: e.target.value }))}
              />
            </div>
            <div>
              <Text strong>文号</Text>
              <Input
                value={form.doc_no}
                onChange={(e) => setForm((f: any) => ({ ...f, doc_no: e.target.value }))}
              />
            </div>
            <Space size={12}>
              <div>
                <Text strong>成文日期</Text>
                <DatePicker
                  value={form.signed_on ? dayjs(form.signed_on) : null}
                  onChange={(d) => setForm((f: any) => ({ ...f, signed_on: d ? d.format('YYYY-MM-DD') : null }))}
                />
              </div>
              <div>
                <Text strong>生效日期</Text>
                <DatePicker
                  value={form.effective_from ? dayjs(form.effective_from) : null}
                  onChange={(d) =>
                    setForm((f: any) => ({ ...f, effective_from: d ? d.format('YYYY-MM-DD') : null }))
                  }
                />
              </div>
            </Space>
            <Space size={12}>
              <div>
                <Text strong>时效</Text>
                <Select
                  style={{ width: 160 }}
                  value={form.status}
                  onChange={(v) => setForm((f: any) => ({ ...f, status: v }))}
                  options={Object.entries(STATUS_LABEL).map(([v, s]) => ({ value: v, label: s.label }))}
                />
              </div>
              <div>
                <Text strong>密级</Text>
                <Select
                  style={{ width: 160 }}
                  value={form.secrecy}
                  onChange={(v) => setForm((f: any) => ({ ...f, secrecy: v }))}
                  options={Object.entries(SECRECY_LABEL).map(([v, s]) => ({ value: v, label: s.label }))}
                />
              </div>
            </Space>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  )
}
