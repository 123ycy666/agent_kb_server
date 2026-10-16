import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { kbApi } from '../api'
import type { KnowledgeBase } from '../api'
import { useAuth } from '../store/auth'

const { Title, Text, Paragraph } = Typography

export default function KnowledgeBaseList() {
  const { has } = useAuth()
  const [data, setData] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<KnowledgeBase | null>(null)
  const [reindexing, setReindexing] = useState<number | null>(null)
  const [form] = Form.useForm()

  const load = async () => {
    setLoading(true)
    try {
      const res = await kbApi.list(1, 100)
      setData(res.items)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ chunk_size: 512, chunk_overlap: 64 })
    setCreateOpen(true)
  }

  const openEdit = (kb: KnowledgeBase) => {
    setEditing(kb)
    form.setFieldsValue({
      name: kb.name,
      description: kb.description,
      chunk_size: kb.chunk_size,
      chunk_overlap: kb.chunk_overlap,
    })
    setCreateOpen(true)
  }

  const submit = async () => {
    const values = await form.validateFields()
    try {
      if (editing) {
        await kbApi.update(editing.id, values)
        message.success('知识库已更新')
      } else {
        await kbApi.create(values)
        message.success('知识库已创建')
      }
      setCreateOpen(false)
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  /** 切换「参与智能问答」标记（2026-10-15 新增）。
   *
   * 该标记决定「智能问答」页默认勾选哪些库。
   * 之所以用「库上打标记」而不是「配置里写 id 列表」：自增 id 会因删库/建库
   * 漂移（删除不复用、新建拿更大值），写死的配置会悬空或失效；标记随库走。
   */
  const toggleQa = async (kb: KnowledgeBase, checked: boolean) => {
    const prev = kb.qa_enabled
    // 乐观更新：开关要即时响应，失败再回滚
    setData((rows) => rows.map((r) => (r.id === kb.id ? { ...r, qa_enabled: checked } : r)))
    try {
      await kbApi.update(kb.id, { qa_enabled: checked })
      message.success(
        checked ? `「${kb.name}」已加入智能问答检索范围` : `「${kb.name}」已移出智能问答检索范围`,
      )
    } catch (e) {
      setData((rows) => rows.map((r) => (r.id === kb.id ? { ...r, qa_enabled: prev } : r)))
      message.error((e as Error).message)
    }
  }

  const doReindex = async (kb: KnowledgeBase) => {
    setReindexing(kb.id)
    try {
      const res = await kbApi.reindex(kb.id, false)
      message.success(res.message ?? '重建完成')
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setReindexing(null)
    }
  }

  const remove = async (kb: KnowledgeBase) => {
    try {
      await kbApi.remove(kb.id)
      message.success(`知识库《${kb.name}》已删除`)
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const columns: ColumnsType<KnowledgeBase> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name: string, row) => (
        <Space direction="vertical" size={0}>
          <Link to={`/kb/${row.id}/documents`}>
            <Text strong>{name}</Text>
          </Link>
          {row.description && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {row.description}
            </Text>
          )}
        </Space>
      ),
    },
    {
      title: '文档',
      dataIndex: 'document_count',
      width: 100,
      render: (total: number, row) => {
        const visible = row.visible_document_count
        // ⚠️ 本期不做知识库级入口权限，用户可能进库后看不到任何文档。
        //    这里必须区分「空库」与「无权限」，否则会被误判为系统故障。
        if (total > 0 && visible === 0) {
          return (
            <Tooltip title="该库中有文档，但你没有访问权限">
              <Tag color="warning">0 / {total}</Tag>
            </Tooltip>
          )
        }
        return <span>{visible === total ? total : `${visible} / ${total}`}</span>
      },
    },
    { title: '切片', dataIndex: 'chunk_count', width: 90 },
    {
      title: '向量模型',
      dataIndex: 'embedding_model',
      render: (m: string, row) => (
        <Space direction="vertical" size={0}>
          <Text className="kb-mono" style={{ fontSize: 11 }}>
            {m}
          </Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {row.embedding_dim} 维
          </Text>
        </Space>
      ),
    },
    {
      title: '切片参数',
      width: 130,
      render: (_, row) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {row.chunk_size} / 重叠 {row.chunk_overlap}
        </Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => (
        <Tag color={s === 'active' ? 'success' : 'default'}>
          {s === 'active' ? '启用' : '归档'}
        </Tag>
      ),
    },
    {
      title: '参与问答',
      dataIndex: 'qa_enabled',
      width: 100,
      render: (v: boolean, row) => (
        <Tooltip
          title={
            v
              ? '已在「智能问答」默认检索范围内，点击移出'
              : '点击加入「智能问答」默认检索范围（问答页会默认勾选）'
          }
        >
          <Switch
            size="small"
            checked={v}
            disabled={!has('kb:update')}
            onChange={(c) => toggleQa(row, c)}
          />
        </Tooltip>
      ),
    },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space>
          <Link to={`/kb/${row.id}/documents`}>
            <Button size="small" icon={<FileTextOutlined />}>
              文档
            </Button>
          </Link>
          {has('kb:update') && (
            <Tooltip title="用当前档位的向量模型重建全部切片（切换模型后必做）">
              <Button
                size="small"
                icon={<SyncOutlined />}
                loading={reindexing === row.id}
                onClick={() => doReindex(row)}
              />
            </Tooltip>
          )}
          {has('kb:update') && (
            <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} />
          )}
          {has('kb:delete') && (
            <Popconfirm
              title="删除知识库"
              description={
                <span>
                  将同时删除该库下 <b>{row.document_count}</b> 份文档及其切片与冲突记录，
                  此操作不可恢复。确认删除？
                </span>
              }
              okText="确认删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => remove(row)}
            >
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Title level={4} style={{ margin: 0 }}>
          知识库管理
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
          {has('kb:create') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              创建知识库
            </Button>
          )}
        </Space>
      </Space>

      <Card size="small">
        <Paragraph className="kb-hint" style={{ marginBottom: 0 }}>
          ⚠️ 知识库绑定<b>向量模型</b>：模型切换后，已有切片的向量与新模型不在同一向量空间，
          检索将无法命中，必须先重建索引。
        </Paragraph>
      </Card>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={data}
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: false }}
      />

      <Modal
        title={editing ? '编辑知识库' : '创建知识库'}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={submit}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入知识库名称' }]}
          >
            <Input placeholder="如：公司制度库" maxLength={128} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="可选" maxLength={500} />
          </Form.Item>
          <Space size="large">
            <Form.Item
              name="chunk_size"
              label="切片长度"
              tooltip="中文约等于字符数，建议 300~800"
            >
              <InputNumber min={64} max={4096} step={64} />
            </Form.Item>
            <Form.Item
              name="chunk_overlap"
              label="重叠长度"
              tooltip="防止关键信息正好落在切片边界"
            >
              <InputNumber min={0} max={1024} step={16} />
            </Form.Item>
          </Space>
          {editing && (
            <Alert
              type="warning"
              showIcon
              message="修改切片参数后，已入库文档不会自动重新切片，如需生效请对文档执行重建索引。"
            />
          )}
        </Form>
      </Modal>
    </Space>
  )
}
