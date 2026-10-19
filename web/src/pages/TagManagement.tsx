import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { tagApi } from '../api'
import type { Tag as TagItem } from '../api'
import { useAuth } from '../store/auth'

const { Title, Text } = Typography

/**
 * 标签管理（极简）。
 *
 * 标签就是**自由字符串**（如「行政」「预算」），不再有「维度 / 值类型 / 枚举 / 合并」那套。
 * 这里只做两件事：① 查看用过的标签 ② 管理员维护（新增 / 改说明 / 重命名 / 删除）。
 * 普通用户上传文档时直接写标签名即可，系统会自动登记进词表。
 */
export default function TagManagement() {
  const { has } = useAuth()
  const canManage = has('tag:manage')

  const [data, setData] = useState<TagItem[]>([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<TagItem | null>(null)
  const [form] = Form.useForm()
  const [renameOpen, setRenameOpen] = useState(false)
  const [renaming, setRenaming] = useState<TagItem | null>(null)
  const [renameForm] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await tagApi.list(keyword || undefined)
      setData(res.items)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [keyword])

  useEffect(() => {
    void load()
  }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    setOpen(true)
  }

  const openEdit = (t: TagItem) => {
    setEditing(t)
    form.setFieldsValue({ name: t.name, description: t.description ?? '' })
    setOpen(true)
  }

  const submit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) {
        await tagApi.update(editing.id, { description: v.description })
      } else {
        await tagApi.create({ name: v.name, description: v.description })
      }
      message.success(editing ? '已保存' : '已新增标签')
      setOpen(false)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const openRename = (t: TagItem) => {
    setRenaming(t)
    renameForm.setFieldsValue({ new_name: t.name })
    setRenameOpen(true)
  }

  const submitRename = async () => {
    const v = await renameForm.validateFields()
    if (!renaming) return
    try {
      const res = await tagApi.rename(renaming.name, v.new_name)
      message.success(res?.message ?? '已重命名')
      setRenameOpen(false)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const remove = async (t: TagItem) => {
    try {
      const res = await tagApi.remove(t.name)
      message.success(res?.message ?? '已删除')
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const columns: ColumnsType<TagItem> = [
    {
      title: '标签',
      dataIndex: 'name',
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '说明',
      dataIndex: 'description',
      render: (v?: string) => v || <Text type="secondary">—</Text>,
    },
    {
      title: '使用次数',
      dataIndex: 'usage_count',
      width: 110,
      sorter: (a, b) => a.usage_count - b.usage_count,
    },
    {
      title: '操作',
      width: 230,
      render: (_, row) =>
        canManage ? (
          <Space size={4}>
            <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>
              说明
            </Button>
            <Button size="small" onClick={() => openRename(row)}>
              重命名
            </Button>
            <Popconfirm
              title={`删除标签「${row.name}」？`}
              description="会从所有文档上摘除该标签（不影响文档内容）"
              onConfirm={() => remove(row)}
            >
              <Button size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 16 }} align="baseline">
        <Title level={4} style={{ margin: 0 }}>
          标签管理
        </Title>
        <Text type="secondary">
          给文档挂几个说明性标签（如「行政」「预算」），用于检索筛选
        </Text>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="标签无需预先创建"
        description="上传文档时直接输入标签名即可，系统会自动登记进词表供下次下拉复用。重命名会同步改写所有文档上的标签值；删除会从所有文档上摘除该标签。"
      />

      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Input.Search
            placeholder="搜索标签名"
            allowClear
            style={{ width: 240 }}
            onSearch={(v) => setKeyword(v)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
          {canManage && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新增标签
            </Button>
          )}
        </Space>

        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={data}
          pagination={{ pageSize: 20, showSizeChanger: false }}
        />
      </Card>

      <Modal
        title={editing ? '编辑标签说明' : '新增标签'}
        open={open}
        onOk={submit}
        onCancel={() => setOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="标签名"
            rules={[{ required: true, message: '请输入标签名' }, { max: 32 }]}
          >
            <Input placeholder="如：行政 / 预算" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="description" label="说明（可选）">
            <Input.TextArea rows={2} placeholder="这个标签的用途，便于他人理解" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`重命名「${renaming?.name ?? ''}」`}
        open={renameOpen}
        onOk={submitRename}
        onCancel={() => setRenameOpen(false)}
        destroyOnClose
      >
        <Form form={renameForm} layout="vertical">
          <Form.Item
            name="new_name"
            label="新名称"
            rules={[{ required: true, message: '请输入新名称' }, { max: 32 }]}
          >
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
