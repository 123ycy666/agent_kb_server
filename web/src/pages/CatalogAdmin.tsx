import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { catalogApi } from '../api'

const { Text, Paragraph } = Typography

function CatalogTree() {
  const [rows, setRows] = useState<any[]>([])
  const [name, setName] = useState('')
  const [parent, setParent] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)

  const load = () =>
    catalogApi
      .tree()
      .then((r) => setRows(r.items ?? []))
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
  }, [])

  const add = async () => {
    if (!name.trim()) return message.warning('先写目录名')
    setLoading(true)
    try {
      const r: any = await catalogApi.addNode(name.trim(), parent)
      if (r.error) message.error(r.error)
      else {
        message.success(`已新建 ${r.path}`)
        setName('')
        await load()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const tops = rows.filter((r) => !r.parent_id)

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        目录管归属，也是权限边界；一篇文档只放一个主目录。一级是业务条线，二级是事项域。
      </Paragraph>
      <Space wrap>
        <Select
          style={{ width: 220 }}
          allowClear
          placeholder="挂在一级目录下（不选=建一级）"
          value={parent ?? undefined}
          onChange={(v) => setParent(v ?? null)}
          options={tops.map((t) => ({ value: t.id, label: t.name }))}
        />
        <Input
          style={{ width: 220 }}
          placeholder="新目录名"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onPressEnter={() => void add()}
        />
        <Button type="primary" icon={<PlusOutlined />} loading={loading} onClick={() => void add()}>
          新建目录
        </Button>
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>
          刷新
        </Button>
      </Space>
      <Table
        size="small"
        rowKey="id"
        pagination={false}
        dataSource={rows}
        columns={[
          {
            title: '目录',
            render: (_, r: any) => (
              <Space>
                {r.parent_id ? <Text type="secondary">└</Text> : null}
                <Text strong={!r.parent_id}>{r.name}</Text>
                <Text type="secondary">{r.path}</Text>
              </Space>
            ),
          },
          { title: '文档', dataIndex: 'docs', width: 80 },
          {
            title: '状态',
            width: 120,
            render: (_, r: any) => (
              <Switch
                size="small"
                checked={r.enabled}
                checkedChildren="启用"
                unCheckedChildren="停用"
                onChange={(v) =>
                  catalogApi.setNode(r.id, v).then(() => load()).catch((e) => message.error(e.message))
                }
              />
            ),
          },
        ]}
      />
    </Space>
  )
}

function Terms() {
  const [dim, setDim] = useState<'文种' | '主题'>('文种')
  const [rows, setRows] = useState<any[]>([])
  const [term, setTerm] = useState('')
  const [syn, setSyn] = useState('')

  const load = () =>
    catalogApi
      .terms(dim)
      .then((r) => setRows(r.items ?? []))
      .catch((e) => message.error((e as Error).message))

  useEffect(() => {
    load()
  }, [dim])

  const add = async () => {
    if (!term.trim()) return message.warning('先写词条')
    try {
      await catalogApi.addTerm(dim, term.trim(), syn.trim())
      setTerm('')
      setSyn('')
      await load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        词表是受控的：上传时只能选这里的词，手打新词进不来。文种每篇必选 1 个，主题最多 2 个。
      </Paragraph>
      <Space wrap>
        <Select
          style={{ width: 120 }}
          value={dim}
          onChange={(v) => setDim(v)}
          options={[{ value: '文种', label: '文种' }, { value: '主题', label: '主题' }]}
        />
        <Input
          style={{ width: 180 }}
          placeholder="新词"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
        />
        <Input
          style={{ width: 220 }}
          placeholder="同义词（逗号分隔，可空）"
          value={syn}
          onChange={(e) => setSyn(e.target.value)}
        />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => void add()}>
          加入词表
        </Button>
      </Space>
      <Table
        size="small"
        rowKey="id"
        pagination={{ pageSize: 20 }}
        dataSource={rows}
        columns={[
          { title: '词条', dataIndex: 'term' },
          {
            title: '同义词',
            dataIndex: 'synonyms',
            render: (v: string) => (v ? v : <Text type="secondary">—</Text>),
          },
          { title: '被用次数', dataIndex: 'usage_count', width: 100 },
          {
            title: '状态',
            width: 110,
            render: (_, r: any) => (
              <Switch
                size="small"
                checked={r.enabled}
                onChange={(v) =>
                  catalogApi.setTerm(r.id, v).then(() => load()).catch((e) => message.error(e.message))
                }
              />
            ),
          },
        ]}
      />
    </Space>
  )
}

export default function CatalogAdmin() {
  const [open, setOpen] = useState(false)
  const [stat, setStat] = useState<any>(null)

  useEffect(() => {
    catalogApi
      .completeness()
      .then((r) => setStat(r))
      .catch(() => undefined)
  }, [])

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Card size="small">
        <Space wrap>
          <Text>
            在位文档 {stat?.total ?? '—'} 篇，缺目录 {stat?.no_catalog ?? '—'} 篇，
            缺文种 {stat?.no_kind ?? '—'} 篇，缺文号 {stat?.no_no ?? '—'} 篇
          </Text>
          <Button size="small" onClick={() => setOpen(true)}>
            灌默认目录与词表
          </Button>
        </Space>
      </Card>
      <Card size="small">
        <Tabs
          items={[
            { key: 'tree', label: '目录', children: <CatalogTree /> },
            { key: 'terms', label: '词表', children: <Terms /> },
          ]}
        />
      </Card>
      <Modal
        open={open}
        title="灌默认目录与词表"
        onCancel={() => setOpen(false)}
        onOk={() =>
          catalogApi
            .seed()
            .then((r: any) => {
              message.success(`已建立 ${r.catalogs ?? 0} 个目录`)
              setOpen(false)
            })
            .catch((e) => message.error(e.message))
        }
      >
        <Text>按《知识库需求设计》灌 5 条业务条线、14 个事项域、16 个文种、9 个主题词。已存在的不覆盖。</Text>
      </Modal>
    </Space>
  )
}
