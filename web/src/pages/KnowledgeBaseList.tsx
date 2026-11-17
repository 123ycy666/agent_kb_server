import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import type { UploadFile } from 'antd'
import {
  BookOutlined,
  CloudUploadOutlined,
  EyeOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RocketOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { specsApi } from '../api'
import type { SpecDetail, SpecItem, SpecPending, SpecPreview, SpecRole } from '../api'
import ConflictList from './ConflictList'
import CatalogAdmin from './CatalogAdmin'
import UploadWizard from './UploadWizard'
import SearchProbe from './SearchProbe'
import KbEval from './KbEval'
import MaterialLibrary from './MaterialLibrary'
import TagManagement from './TagManagement'

const { Title, Text, Paragraph } = Typography

function menuLine(row: SpecItem) {
  const conf = row.conflict_with?.length ? ` · ⚠ 与《${row.conflict_with.join('》《')}》同时适用` : ''
  return `- ${row.name} · ${row.when_to_use || '（未填什么时候用）'} · ${row.product || '无产物'}${conf}`
}

function SpecLibrary({ space }: { space: 'spec' | 'material' }) {
  const [roles, setRoles] = useState<SpecRole[]>([])
  const [list, setList] = useState<{
    items: SpecItem[]
    pending: SpecPending[]
    menu: string
    count: number
  } | null>(null)
  const [loading, setLoading] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [preview, setPreview] = useState<{ id: number; data: SpecPreview } | null>(null)
  const [editPending, setEditPending] = useState<SpecPending | null>(null)
  const [tryOpen, setTryOpen] = useState(false)
  const [trySpec, setTrySpec] = useState<SpecItem | null>(null)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [detail, setDetail] = useState<SpecDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [r, role] = await Promise.all([specsApi.list(space), specsApi.roles()])
      setList(r)
      setRoles(role.items ?? [])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [space])

  const roleName = (code?: string) => roles.find((x) => x.code === code)?.label || code || '—'

  const openPreview = async (id: number) => {
    try {
      setPreview({ id, data: await specsApi.preview(id) })
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const openDetail = async (id: number) => {
    setDetailId(id)
    setDetail(null)
    setDetailLoading(true)
    try {
      setDetail(await specsApi.get(id))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setDetailLoading(false)
    }
  }

  const publish = async (id: number) => {
    try {
      const r: any = await specsApi.publish(id)
      if (r?.error) message.error(r.error)
      else {
        message.success(`《${r.name}》已发布，${r.effective_within_sec || 60} 秒内进目录`)
        setPreview(null)
        await load()
      }
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const retire = async (row: SpecItem) => {
    try {
      const r: any = await specsApi.retire(row.id)
      if (r?.error) message.error(r.error)
      else {
        message.success(`《${row.name}》已退役，不再进目录`)
        await load()
      }
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const confirmRetire = (row: SpecItem) => {
    Modal.confirm({
      title: `停用《${row.name}》？`,
      content: '停用 = 退役：不删正文、可回滚，但不再进说明书目录。当前接口不提供退役页，如需重新启用请重新上传或联系管理员处理。',
      okText: '停用（退役）',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => retire(row),
    })
  }

  const pending = (list?.pending ?? []).filter((x) =>
    space === 'spec' ? x.space === 'spec' : x.space === 'material'
  )
  const published = list?.items ?? []

  const columns: ColumnsType<SpecItem> = [
    {
      title: '目录行',
      width: 430,
      render: (_, row) => (
        <div>
          <Space size={6} wrap>
            <Text strong>{row.name}</Text>
            <Tag color="blue">v{row.version_no}</Tag>
            {row.conflict_with?.length ? (
              <Tooltip title={`与《${row.conflict_with.join('》《')}》同时适用`}>
                <Tag color="error">{row.conflict_with.length} 处冲突</Tag>
              </Tooltip>
            ) : null}
          </Space>
          <div className="kb-mono" style={{ fontSize: 11, color: '#8c8c8c', marginTop: 3 }}>
            {menuLine(row)}
          </div>
        </div>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role_code',
      width: 86,
      render: (v: string) => <Tag>{roleName(v)}</Tag>,
    },
    {
      title: '归属',
      dataIndex: 'owner',
      width: 78,
      render: (v: string) => <Tag color={v === '全局' ? 'purple' : 'cyan'}>{v}</Tag>,
    },
    {
      title: '字数',
      dataIndex: 'chars',
      width: 66,
      align: 'right',
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: '最近修改',
      dataIndex: 'updated_at',
      width: 122,
      render: (v: string) => (v ? dayjs(v).format('MM-DD HH:mm') : '—'),
    },
    {
      title: '启停',
      width: 78,
      render: (_, row) => (
        <Tooltip title="停用即退役，不再进目录">
          <Switch
            size="small"
            checked
            checkedChildren="启用"
            unCheckedChildren="停用"
            onChange={(v) => {
              if (!v) confirmRetire(row)
            }}
          />
        </Tooltip>
      ),
    },
    {
      title: '操作',
      width: 214,
      render: (_, row) => (
        <Space size={4} wrap>
          <Button size="small" icon={<EyeOutlined />} onClick={() => void openDetail(row.id)}>
            全文
          </Button>
          <Button
            size="small"
            icon={<RocketOutlined />}
            onClick={() => {
              setTrySpec(row)
              setTryOpen(true)
            }}
          >
            试跑
          </Button>
          <Popconfirm
            title="确认退役？"
            description="退役后不再进目录，不删除正文。"
            onConfirm={() => void retire(row)}
          >
            <Button size="small" danger type="text">
              退役
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const pendingColumns: ColumnsType<SpecPending> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          <Text strong>{v}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {r.space === 'material' ? '资料库 · 不进目录' : '说明书库 · 待发布'}
          </Text>
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role_code',
      width: 100,
      render: (v: string) => (v ? <Tag>{roleName(v)}</Tag> : <Tag>未选</Tag>),
    },
    {
      title: '四项',
      width: 260,
      render: (_, r) => {
        const four = [
          ['角色', r.role_code],
          ['什么时候用', r.when_to_use],
          ['产物', r.product],
          ['归属', r.owner],
        ]
        return (
          <Space size={4} wrap>
            {four.map(([label, v]) => (
              <Tag key={label} color={v ? 'green' : 'red'}>
                {label} {v ? '有' : '缺'}
              </Tag>
            ))}
          </Space>
        )
      },
    },
    {
      title: '字数',
      dataIndex: 'chars',
      width: 80,
      align: 'right',
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) =>
        v === 'material' ? <Tag>资料</Tag> : <Tag color="orange">待发布</Tag>,
    },
    {
      title: '操作',
      width: 280,
      render: (_, r) => (
        <Space size={4} wrap>
          <Button size="small" onClick={() => void openPreview(r.id)}>
            预览切片
          </Button>
          {space === 'spec' ? (
            <>
              <Button size="small" onClick={() => setEditPending(r)}>
                补四项
              </Button>
              <Popconfirm
                title="发布这篇说明书？"
                description="发布后进入目录，60 秒内生效；四项必须填全。"
                onConfirm={() => void publish(r.id)}
              >
                <Button size="small" type="primary">
                  发布
                </Button>
              </Popconfirm>
            </>
          ) : (
            <Link to="/chat">
              <Button size="small" icon={<SearchOutlined />}>
                去问答检索
              </Button>
            </Link>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
        <div>
          <Space align="baseline">
            <Title level={5} style={{ margin: 0 }}>
              {space === 'spec' ? '说明书库' : '资料库'}
            </Title>
            <Tag>{space === 'spec' ? `${published.length} 篇已发布` : `${pending.length} 份资料`}</Tag>
          </Space>
          <Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 12 }}>
            {space === 'spec'
              ? '一篇说明书 = 一类活怎么做。目录一行显示名称 · 什么时候用 · 产物；冲突会单独标红。'
              : '资料只进检索、不进说明书目录。制度、办法、附件放这里。'}
          </Paragraph>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
          <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setUploadOpen(true)}>
            {space === 'spec' ? '上传说明书' : '上传资料'}
          </Button>
        </Space>
      </Space>

      {space === 'spec' ? (
        <Card size="small" title="已发布（进目录）">
          <Table
            rowKey="id"
            size="small"
            loading={loading}
            dataSource={published}
            columns={columns}
            pagination={{ pageSize: 8, showSizeChanger: false }}
            locale={{ emptyText: <Empty description="还没有已发布的说明书，先上传一篇" /> }}
          />
        </Card>
      ) : null}

      <Card
        size="small"
        title={space === 'spec' ? `待发布（${pending.length}）` : `资料清单（${pending.length}）`}
      >
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={pending}
          columns={pendingColumns}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          scroll={{ x: 1100 }}
          locale={{
            emptyText: (
              <Empty description={space === 'spec' ? '没有待发布说明书' : '还没有资料，上传后只进检索'} />
            ),
          }}
        />
      </Card>

      <UploadModal
        open={uploadOpen}
        space={space}
        roles={roles}
        onClose={() => setUploadOpen(false)}
        onDone={async (id) => {
          setUploadOpen(false)
          await load()
          if (id) await openPreview(id)
        }}
      />

      <ClassifyModal
        pending={editPending}
        roles={roles}
        onClose={() => setEditPending(null)}
        onDone={async () => {
          setEditPending(null)
          await load()
        }}
      />

      <Modal
        open={!!preview}
        width={820}
        title={preview ? `待发布预览：${preview.data.name}` : '待发布预览'}
        onCancel={() => setPreview(null)}
        footer={
          preview ? (
            <Space>
              <Button onClick={() => setPreview(null)}>关闭</Button>
              {space === 'spec' ? (
                <Button type="primary" onClick={() => void publish(preview.id)}>
                  确认发布
                </Button>
              ) : null}
            </Space>
          ) : null
        }
      >
        {preview ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type={preview.data.missing.length ? 'warning' : 'success'}
              showIcon
              message={
                preview.data.missing.length
                  ? `还缺：${preview.data.missing.join('、')}`
                  : '四项已填全，可发布'
              }
            />
            <Card size="small" title="目录行长什么样">
              <div className="kb-mono" style={{ whiteSpace: 'pre-wrap' }}>
                {preview.data.menu_line || '（还没有目录行）'}
              </div>
            </Card>
            <Card size="small" title={`切片预览（${preview.data.slices.length} 段，共 ${preview.data.chars} 字）`}>
              {preview.data.slices.length === 0 ? (
                <Empty description="没有切出正文" />
              ) : (
                preview.data.slices.map((s) => (
                  <div key={s.no} className="kb-chunk" style={{ fontSize: 12 }}>
                    <Text type="secondary">第 {s.no} 段 · </Text>
                    {s.text}
                  </div>
                ))
              )}
            </Card>
          </Space>
        ) : null}
      </Modal>

      <TryRunModal open={tryOpen} spec={trySpec} onClose={() => setTryOpen(false)} />

      <Drawer
        open={detailId != null}
        width={860}
        title={detail?.item?.name || '说明书全文'}
        onClose={() => setDetailId(null)}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : detail ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="角色">{roleName(detail.item?.role_code)}</Descriptions.Item>
              <Descriptions.Item label="归属">{detail.owner}</Descriptions.Item>
              <Descriptions.Item label="版本">v{detail.item?.version_no}</Descriptions.Item>
              <Descriptions.Item label="字数">{detail.chars}</Descriptions.Item>
              <Descriptions.Item label="什么时候用" span={2}>
                {detail.item?.when_to_use || '—'}
              </Descriptions.Item>
              <Descriptions.Item label="产物" span={2}>
                {detail.item?.product || '无产物'}
              </Descriptions.Item>
            </Descriptions>
            <Card size="small" title="正文">
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>{detail.body}</pre>
            </Card>
            <Row gutter={12}>
              <Col span={12}>
                <Card size="small" title={`引用（${detail.refs.length}）`}>
                  {detail.refs.length === 0 ? (
                    <Text type="secondary">没有《书名号》引用</Text>
                  ) : (
                    detail.refs.map((r) => (
                      <div key={r.name} style={{ marginBottom: 4 }}>
                        <Tag color={r.resolved ? 'green' : 'red'}>{r.resolved ? '可读' : '缺失'}</Tag>
                        《{r.name}》
                      </div>
                    ))
                  )}
                </Card>
              </Col>
              <Col span={12}>
                <Card size="small" title={`冲突（${detail.conflicts.length}）`}>
                  {detail.conflicts.length === 0 ? (
                    <Text type="secondary">没有同角色同产物的并存冲突</Text>
                  ) : (
                    detail.conflicts.map((c) => (
                      <div key={c.id} style={{ marginBottom: 4 }}>
                        <Tag color="red">并存</Tag>《{c.name}》（{c.owner}）
                      </div>
                    ))
                  )}
                </Card>
              </Col>
            </Row>
            <Card size="small" title={`版本（${detail.versions.length}）`}>
              <Table
                rowKey="version_no"
                size="small"
                pagination={false}
                dataSource={detail.versions}
                columns={[
                  { title: '版本', dataIndex: 'version_no', render: (v) => `v${v}` },
                  { title: '字数', dataIndex: 'chars' },
                  { title: '保存人', dataIndex: 'saved_by' },
                  {
                    title: '时间',
                    dataIndex: 'saved_at',
                    render: (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '—'),
                  },
                ]}
              />
            </Card>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  )
}

function UploadModal({
  open,
  space,
  roles,
  onClose,
  onDone,
}: {
  open: boolean
  space: 'spec' | 'material'
  roles: SpecRole[]
  onClose: () => void
  onDone: (id?: number) => Promise<void>
}) {
  const [form] = Form.useForm()
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [files, setFiles] = useState<UploadFile[]>([])

  const submit = async () => {
    if (!file) {
      message.warning('请选择文件')
      return
    }
    const v = await form.validateFields()
    const fd = new FormData()
    fd.append('file', file)
    fd.append('role_code', space === 'spec' ? v.role_code || '' : '')
    fd.append('space', space)
    fd.append('when_to_use', v.when_to_use || '')
    fd.append('applies', v.applies || '')
    fd.append('product', v.product || '')
    fd.append('name', v.name || '')
    fd.append('global_scope', v.global_scope ? '1' : '0')
    setBusy(true)
    try {
      const r: any = await specsApi.upload(fd)
      message.success(
        space === 'spec'
          ? `已上传《${r.name}》，进待发布；填完四项后预览发布`
          : `已上传《${r.name}》到资料库，可检索、不进目录`
      )
      form.resetFields()
      setFile(null)
      setFiles([])
      await onDone(r.spec_id)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      title={space === 'spec' ? '上传说明书' : '上传资料'}
      width={720}
      onCancel={onClose}
      onOk={() => void submit()}
      okText="上传"
      confirmLoading={busy}
      destroyOnClose
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message={
          space === 'spec'
            ? '选了角色才算说明书：进说明书库、待发布；上传时填好四项，发布前还能补。'
            : '不选角色 = 资料：只进检索，不进说明书目录。'
        }
      />
      <Form form={form} layout="vertical" initialValues={{ global_scope: false }}>
        <Form.Item label="文件" required>
          <Upload
            beforeUpload={(f) => {
              setFile(f as File)
              setFiles([
                {
                  uid: f.uid,
                  name: f.name,
                  status: 'done',
                },
              ])
              return false
            }}
            fileList={files}
            maxCount={1}
            onRemove={() => {
              setFile(null)
              setFiles([])
            }}
          >
            <Button icon={<CloudUploadOutlined />}>选择文件（md / docx / txt）</Button>
          </Upload>
        </Form.Item>
        {space === 'spec' ? (
          <Form.Item
            name="role_code"
            label="角色（决定这类活由哪条线接）"
            rules={[{ required: true, message: '请选择角色' }]}
          >
            <Select
              placeholder="选一个角色"
              options={roles.map((r) => ({
                value: r.code,
                label: `${r.label}（产物 ${r.product || '无'}）`,
              }))}
            />
          </Form.Item>
        ) : null}
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item
              name="when_to_use"
              label="什么时候用"
              rules={space === 'spec' ? [{ required: true, message: '请填什么时候用' }] : []}
            >
              <Input placeholder="如：写部门年度工作总结时" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="name" label="名称（默认用文件名）">
              <Input placeholder="如：部门年度总结" />
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item
              name="applies"
              label="适用（逗号分隔）"
              rules={space === 'spec' ? [{ required: true, message: '请填适用词' }] : []}
            >
              <Input placeholder="如：年度总结,部门总结" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="product"
              label="产物"
              rules={space === 'spec' ? [{ required: true, message: '请填产物' }] : []}
            >
              <Input placeholder="如：docx / xlsx / pptx" />
            </Form.Item>
          </Col>
        </Row>
        {space === 'spec' ? (
          <Form.Item name="global_scope" label="归属：全局（管理员才可设）" valuePropName="checked">
            <Switch checkedChildren="全局" unCheckedChildren="本人" />
          </Form.Item>
        ) : null}
      </Form>
    </Modal>
  )
}

function ClassifyModal({
  pending,
  roles,
  onClose,
  onDone,
}: {
  pending: SpecPending | null
  roles: SpecRole[]
  onClose: () => void
  onDone: () => Promise<void>
}) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (pending) {
      form.setFieldsValue({
        role_code: pending.role_code,
        when_to_use: pending.when_to_use,
        product: pending.product,
        applies: '',
        owner_scope: pending.owner === '全局' ? 'global' : 'own',
        check_items: '',
        outline: '',
      })
    }
  }, [pending, form])

  const submit = async () => {
    if (!pending) return
    const v = await form.validateFields()
    setBusy(true)
    try {
      const payload: Record<string, unknown> = {
        role_code: v.role_code,
        when_to_use: v.when_to_use,
        applies: String(v.applies || '')
          .split(/[,，、]/)
          .map((x) => x.trim())
          .filter(Boolean),
        product: v.product,
        owner_scope: v.owner_scope,
      }
      if (String(v.check_items || '').trim()) {
        payload.check_items = String(v.check_items)
          .split('\n')
          .map((x) => x.trim())
          .filter(Boolean)
      }
      if (String(v.outline || '').trim()) {
        payload.outline = String(v.outline)
          .split('\n')
          .map((x) => x.trim())
          .filter(Boolean)
      }
      const r: any = await specsApi.classify(pending.id, payload)
      if (r?.error) message.error(r.error)
      else {
        message.success(r.ready ? '四项已填全，可以预览并发布' : `还缺：${(r.missing || []).join('、')}`)
        await onDone()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={!!pending}
      title={pending ? `补四项：${pending.name}` : '补四项'}
      width={680}
      onCancel={onClose}
      onOk={() => void submit()}
      okText="保存四项"
      confirmLoading={busy}
      destroyOnClose
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="四项 = 角色 / 什么时候用 / 适用 / 产物 / 归属。四项不全不能发布。"
      />
      <Form form={form} layout="vertical">
        <Form.Item name="role_code" label="角色" rules={[{ required: true, message: '请选择角色' }]}>
          <Select options={roles.map((r) => ({ value: r.code, label: r.label }))} />
        </Form.Item>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item
              name="when_to_use"
              label="什么时候用"
              rules={[{ required: true, message: '请填什么时候用' }]}
            >
              <Input />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="product"
              label="产物"
              rules={[{ required: true, message: '请填产物' }]}
            >
              <Input placeholder="docx / xlsx / pptx" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item
          name="applies"
          label="适用（逗号分隔；留空保留原值）"
        >
          <Input placeholder="如：年度总结,部门总结" />
        </Form.Item>
        <Form.Item name="owner_scope" label="归属">
          <Select
            options={[
              { value: 'own', label: '本人' },
              { value: 'global', label: '全局（管理员）' },
            ]}
          />
        </Form.Item>
        <Form.Item name="check_items" label="自检检查项（一行一项，可不填）">
          <Input.TextArea rows={3} placeholder="如：结构齐全（结论 + 依据）" />
        </Form.Item>
        <Form.Item name="outline" label="结构（一行一节，可不填）">
          <Input.TextArea rows={3} placeholder="如：总体情况 / 主要工作 / 下步打算" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

function TryRunModal({
  open,
  spec,
  onClose,
}: {
  open: boolean
  spec: SpecItem | null
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [question, setQuestion] = useState('')
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open && spec) {
      setName(spec.name)
      setQuestion('')
      setData(null)
    }
  }, [open, spec])

  const run = async () => {
    setBusy(true)
    try {
      setData(
        await specsApi.tryRun({
          name: name.trim() || undefined,
          question: question.trim() || undefined,
          spec_id: spec?.id,
        })
      )
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      title={`试跑：${spec?.name || ''}`}
      width={760}
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>关闭</Button>
          <Button type="primary" loading={busy} onClick={() => void run()}>
            跑一次
          </Button>
        </Space>
      }
      destroyOnClose
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="试跑不入会话、不写记录：只看这篇说明书会不会被选中、目录行、注入多长。"
        />
        <Space wrap>
          <Input value={name} onChange={(e) => setName(e.target.value)} style={{ width: 240 }} placeholder="说明书名" />
          <Input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            style={{ width: 340 }}
            placeholder="可选：模拟一句用户问题"
          />
        </Space>
        {data ? (
          data.hit ? (
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Alert type="success" showIcon message={`命中《${data.name}》，正文 ${data.body_chars} 字`} />
              <Card size="small" title="目录行">
                <div className="kb-mono">{data.menu_line}</div>
              </Card>
              <Descriptions size="small" column={3} bordered>
                <Descriptions.Item label="正文">{data.body_chars} 字</Descriptions.Item>
                <Descriptions.Item label="注入块">{data.block_chars} 字</Descriptions.Item>
                <Descriptions.Item label="检查项">{data.check_items?.length ?? 0} 项</Descriptions.Item>
              </Descriptions>
              <Card size="small" title="注入构成">
                <Table
                  rowKey="name"
                  size="small"
                  pagination={false}
                  dataSource={data.injection ?? []}
                  columns={[
                    { title: '组成', dataIndex: 'label' },
                    { title: '字数', dataIndex: 'chars', width: 90 },
                    { title: '注入', dataIndex: 'injected', width: 80, render: (v) => (v ? '是' : '否') },
                  ]}
                />
              </Card>
              <Row gutter={12}>
                <Col span={12}>
                  <Card size="small" title="引用">
                    {(data.refs ?? []).length === 0 ? (
                      <Text type="secondary">无引用</Text>
                    ) : (
                      data.refs.map((r: any) => (
                        <div key={r.name}>
                          <Tag color={r.resolved ? 'green' : 'red'}>{r.resolved ? '可读' : '缺失'}</Tag>
                          《{r.name}》
                        </div>
                      ))
                    )}
                  </Card>
                </Col>
                <Col span={12}>
                  <Card size="small" title="冲突">
                    {(data.conflicts ?? []).length === 0 ? (
                      <Text type="secondary">无并存冲突</Text>
                    ) : (
                      data.conflicts.map((c: any) => (
                        <div key={c.id}>
                          <Tag color="red">并存</Tag>《{c.name}》
                        </div>
                      ))
                    )}
                  </Card>
                </Col>
              </Row>
            </Space>
          ) : (
            <Alert type="warning" showIcon message="没有命中这篇说明书（可能未发布或不在你的归属里）" />
          )
        ) : null}
      </Space>
    </Modal>
  )
}

export default function KnowledgeBaseList() {
  const [tab, setTab] = useState('spec')
  const roleLegend = useMemo(
    () => [
      { code: 'gongwen', label: '公文' },
      { code: 'baogao', label: '汇报材料' },
      { code: 'taizhang', label: '台账表格' },
      { code: 'kejian', label: '汇报课件' },
    ],
    []
  )
  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
        <div>
          <Space align="baseline">
            <Title level={4} style={{ margin: 0 }}>
              知识库
            </Title>
            <Tag icon={<BookOutlined />}>两个空间</Tag>
          </Space>
          <Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 12 }}>
            说明书库进目录、按角色选；资料库只检索、不进目录。冲突与标签也在本页。
          </Paragraph>
        </div>
        <Space wrap size={4}>
          {roleLegend.map((r) => (
            <Tag key={r.code}>{r.label}</Tag>
          ))}
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="说明书 ≠ 资料"
        description="说明书是「怎么做」的正文与检查项，发布后进目录；资料是制度、办法、附件，只参与检索。说明书正文不参与向量检索。"
      />

      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: 'spec', label: '说明书库', children: <SpecLibrary space="spec" /> },
          { key: 'material', label: '资料库', children: <MaterialLibrary /> },
          { key: 'catalog', label: '目录与词表', children: <CatalogAdmin /> },
          { key: 'upload', label: '上传', children: <UploadWizard /> },
          { key: 'probe', label: '检索', children: <SearchProbe /> },
          { key: 'eval', label: '评测', children: <KbEval /> },
          { key: 'conflicts', label: '冲突', children: <ConflictList /> },
          { key: 'tags', label: '标签', children: <TagManagement /> },
        ]}
      />
    </Space>
  )
}
