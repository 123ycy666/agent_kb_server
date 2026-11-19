import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import Page from '../components/ui/Page'
import { specsApi } from '../api'

const { Text } = Typography

type Row = {
  id?: number
  name: string
  when_to_use: string
  product: string
  status: 'published' | 'pending' | 'retired'
  updated_at?: string
  role_code?: string
}

const STATUS: Record<Row['status'], { text: string; color: string }> = {
  published: { text: '已发布', color: 'green' },
  pending: { text: '草稿', color: 'gold' },
  retired: { text: '已退役', color: 'default' },
}

const dt = (v?: string | null) => (v ? String(v).slice(0, 16).replace('T', ' ') : '—')

export default function Skills() {
  const [rows, setRows] = useState<Row[]>([])
  const [roles, setRoles] = useState<{ code: string; label: string }[]>([])
  const [loading, setLoading] = useState(false)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [open, setOpen] = useState(false)
  const [cur, setCur] = useState<any>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, retired] = await Promise.all([specsApi.list('spec'), specsApi.retired()])
      const out: Row[] = []
      for (const x of list.items ?? []) {
        out.push({ ...x, status: 'published' })
      }
      for (const x of list.pending ?? []) {
        if (x.space !== 'spec') continue
        out.push({
          id: x.id,
          name: x.name,
          when_to_use: x.when_to_use || '',
          product: x.product || '',
          status: 'pending',
          role_code: x.role_code,
        })
      }
      for (const x of retired.items ?? []) {
        if (x.space !== 'spec' || x.status !== 'retired') continue
        out.push({
          id: x.id,
          name: x.name,
          when_to_use: '',
          product: '',
          status: 'retired',
          updated_at: x.updated_at,
          role_code: x.role_code,
        })
      }
      const seen = new Set<number>()
      setRows(out.filter((r) => (r.id && !seen.has(r.id) ? seen.add(r.id) : !r.id)))
    } catch (e: any) {
      message.error(e?.message || '技能列表拉不出来')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    specsApi
      .roles()
      .then((r) => setRoles(r.items ?? []))
      .catch(() => setRoles([]))
  }, [load])

  const filtered = useMemo(
    () =>
      rows.filter((r) => {
        if (status && r.status !== status) return false
        if (!q.trim()) return true
        const s = (r.name + r.when_to_use + r.product).toLowerCase()
        return s.includes(q.trim().toLowerCase())
      }),
    [rows, q, status]
  )

  const openNew = () => {
    setCur(null)
    form.setFieldsValue({
      name: '',
      role_code: undefined,
      when_to_use: '',
      applies: '',
      product: '',
      body: '',
      check_items: '',
    })
    setOpen(true)
  }

  const openEdit = async (row: Row) => {
    if (!row.id) return
    try {
      const d = await specsApi.edit(row.id)
      setCur(d)
      form.setFieldsValue({
        name: d.name,
        role_code: d.role_code || undefined,
        when_to_use: d.when_to_use || '',
        applies: (d.applies ?? []).join('，'),
        product: d.product || '',
        body: d.body || '',
        check_items: (d.check_items ?? []).join('\n'),
      })
      setOpen(true)
    } catch (e: any) {
      message.error(e?.message || '读不出这篇技能')
    }
  }

  const payload = async () => {
    const v = await form.validateFields()
    return {
      ...v,
      applies: String(v.applies || '')
        .split(/[,，、]/)
        .map((s) => s.trim())
        .filter(Boolean),
      check_items: String(v.check_items || '')
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    }
  }

  const saveDraft = async () => {
    setSaving(true)
    try {
      const body = await payload()
      const r = cur?.id ? await specsApi.save(cur.id, body) : await specsApi.create(body)
      if (r?.error) {
        message.error(r.error)
        return
      }
      message.success('草稿已保存')
      setOpen(false)
      await load()
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const publish = async () => {
    setSaving(true)
    try {
      const body = await payload()
      let id = cur?.id as number | undefined
      if (!id) {
        const c = await specsApi.create(body)
        if (c?.error) {
          message.error(c.error)
          return
        }
        id = c.id
      } else {
        const s = await specsApi.save(id, body)
        if (s?.error) {
          message.error(s.error)
          return
        }
      }
      const p = await specsApi.publish(id!)
      if (p?.error) {
        message.error(p.error)
        return
      }
      message.success('已发布，下一轮生效')
      setOpen(false)
      await load()
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.message || '发布失败')
    } finally {
      setSaving(false)
    }
  }

  const retire = async (row: Row) => {
    if (!row.id) return
    try {
      await specsApi.retire(row.id)
      message.success('已退役')
      await load()
    } catch (e: any) {
      message.error(e?.message || '退役失败')
    }
  }

  const enable = async (row: Row) => {
    if (!row.id) return
    try {
      await specsApi.enable(row.id)
      message.success('已启用')
      await load()
    } catch (e: any) {
      message.error(e?.message || '启用失败')
    }
  }

  const columns: ColumnsType<Row> = [
    {
      title: '名称',
      dataIndex: 'name',
      width: 180,
      render: (v: string) => <Text strong>{v}</Text>,
    },
    {
      title: '什么时候用',
      dataIndex: 'when_to_use',
      ellipsis: { showTitle: false },
      render: (v: string) =>
        v ? (
          <Tooltip title={v} placement="topLeft">
            <Text>{v}</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">未填</Text>
        ),
    },
    {
      title: '产物',
      dataIndex: 'product',
      width: 130,
      render: (v: string) => (v ? <Tag>{v}</Tag> : <Text type="secondary">—</Text>),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: Row['status']) => <Tag color={STATUS[v].color}>{STATUS[v].text}</Tag>,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 140,
      render: (v?: string) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{dt(v)}</span>,
    },
    {
      title: '操作',
      width: 140,
      render: (_: unknown, row: Row) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => void openEdit(row)} aria-label={`编辑 ${row.name}`}>
            编辑
          </Button>
          {row.status === 'retired' ? (
            <Button type="link" size="small" onClick={() => void enable(row)} aria-label={`启用 ${row.name}`}>
              启用
            </Button>
          ) : (
            <Popconfirm
              title="退役这篇技能？"
              description="退役后目录里不再出现，可再启用。"
              okText="退役"
              cancelText="不退役"
              okButtonProps={{ danger: true }}
              onConfirm={() => void retire(row)}
            >
              <Button type="link" size="small" danger aria-label={`退役 ${row.name}`}>
                退役
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Page
      title="技能"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
          新建技能
        </Button>
      }
    >
      <div style={{ background: '#fff', borderRadius: 8, padding: 16 }}>
        <Space style={{ marginBottom: 12 }} wrap>
          <Input.Search
            allowClear
            placeholder="搜名称、什么时候用、产物"
            style={{ width: 260 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="搜索技能"
          />
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 120 }}
            value={status}
            onChange={setStatus}
            aria-label="按状态筛选"
            options={Object.entries(STATUS).map(([k, v]) => ({ value: k, label: v.text }))}
          />
          <Text type="secondary">共 {filtered.length} 条</Text>
        </Space>

        <Table<Row>
          size="small"
          rowKey={(r) => String(r.id ?? r.name)}
          loading={loading}
          columns={columns}
          dataSource={filtered}
          pagination={false}
          locale={{
            emptyText: (
              <div style={{ padding: '32px 0' }}>
                <div style={{ marginBottom: 12 }}>
                  <Text type="secondary">还没有技能</Text>
                </div>
                <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>
                  新建技能
                </Button>
              </div>
            ),
          }}
        />
      </div>

      <Drawer
        open={open}
        width={720}
        title={cur?.id ? `编辑技能 · ${cur.name}` : '新建技能'}
        onClose={() => setOpen(false)}
        destroyOnClose
        extra={
          <Space>
            <Button onClick={() => setOpen(false)}>取消</Button>
            <Button loading={saving} onClick={() => void saveDraft()}>
              保存草稿
            </Button>
            <Button type="primary" loading={saving} onClick={() => void publish()}>
              发布
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '填个名字' }]}
          >
            <Input placeholder="例如：公文格式" maxLength={60} />
          </Form.Item>
          <Form.Item
            name="role_code"
            label="角色"
            rules={[{ required: true, message: '选一个角色，发布要用' }]}
          >
            <Select
              placeholder="选角色"
              options={roles.map((r) => ({ value: r.code, label: r.label }))}
            />
          </Form.Item>
          <Form.Item name="when_to_use" label="什么时候用" rules={[{ required: true, message: '写一句判断条件' }]}>
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder="一句话，模型靠这句判断要不要读" />
          </Form.Item>
          <Form.Item name="applies" label="适用">
            <Input placeholder="逗号分隔，例如：通知，请示，报告" />
          </Form.Item>
          <Form.Item name="product" label="产物" rules={[{ required: true, message: '写产物是什么' }]}>
            <Input placeholder="例如：docx / checklist / 无" />
          </Form.Item>
          <Form.Item name="body" label="正文">
            <Input.TextArea autoSize={{ minRows: 14, maxRows: 30 }} placeholder="步骤、口径、样板；《书名号》是关联技能" />
          </Form.Item>
          <Form.Item name="check_items" label="检查项">
            <Input.TextArea autoSize={{ minRows: 4, maxRows: 12 }} placeholder="一行一条，交付前逐条核对" />
          </Form.Item>
        </Form>
      </Drawer>
    </Page>
  )
}
