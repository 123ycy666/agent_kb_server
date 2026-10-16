import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  Alert,
  Badge,
  Button,
  Card,
  Descriptions,
  Drawer,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  GlobalOutlined,
  HistoryOutlined,
  LockOutlined,
  PlusOutlined,
  ReloadOutlined,
  TagsOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { conflictApi, docApi, kbApi, tagApi } from '../api'
import type { DocItem, KnowledgeBase } from '../api'
import UploadModal from '../components/UploadModal'
import ConflictResolveModal from '../components/ConflictResolveModal'
import { useAuth } from '../store/auth'

const { Title, Text, Paragraph } = Typography

const STATUS_LABEL: Record<string, { text: string; color: string }> = {
  pending: { text: '待处理', color: 'default' },
  parsing: { text: '解析中', color: 'processing' },
  embedding: { text: '向量化中', color: 'processing' },
  ready: { text: '已就绪', color: 'success' },
  failed: { text: '失败', color: 'error' },
}

export default function DocumentList() {
  const { kbId } = useParams()
  const id = Number(kbId)
  const { has } = useAuth()

  const [kb, setKb] = useState<KnowledgeBase | null>(null)
  const [docs, setDocs] = useState<DocItem[]>([])
  const [tagOptions, setTagOptions] = useState<{ name: string; usage_count: number }[]>([])
  const [loading, setLoading] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)

  // 详情抽屉
  const [detail, setDetail] = useState<DocItem | null>(null)
  const [chunks, setChunks] = useState<any[]>([])
  const [versions, setVersions] = useState<any[]>([])

  // 标签编辑
  const [tagDoc, setTagDoc] = useState<DocItem | null>(null)
  const [tagNames, setTagNames] = useState<string[]>([])

  // 冲突决策
  const [conflictOpen, setConflictOpen] = useState(false)
  const [conflictDoc, setConflictDoc] = useState<DocItem | null>(null)

  // 原文件预览（blob objectURL，需手动 revoke 防泄漏）
  const [preview, setPreview] = useState<{
    title: string
    url: string
    revoke: () => void
  } | null>(null)

  const load = async () => {
    if (!id) return
    setLoading(true)
    try {
      const [k, d, t] = await Promise.allSettled([
        kbApi.get(id),
        docApi.list(id, { page_size: 200 }),
        tagApi.options(),
      ])
      if (k.status === 'fulfilled') setKb(k.value)
      if (d.status === 'fulfilled') setDocs(d.value.items)
      if (t.status === 'fulfilled') setTagOptions(t.value.options ?? [])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [id])

  const openDetail = async (doc: DocItem) => {
    setDetail(doc)
    setChunks([])
    setVersions([])
    const [c, v] = await Promise.allSettled([
      docApi.chunks(doc.id, 100),
      docApi.versions(doc.id),
    ])
    if (c.status === 'fulfilled') setChunks(c.value)
    if (v.status === 'fulfilled') setVersions(v.value)
  }

  const remove = async (doc: DocItem) => {
    try {
      const res = await docApi.remove(doc.id)
      // ⚠️ 删除新版时后端会自动把被取代的旧版回滚复活。
      //    这条提示必须展示给用户 —— 否则用户会以为「删干净了」，
      //    而实际上旧内容又重新可检索了（这是预期行为，但用户必须知情）。
      const warns = res?.warnings ?? []
      if (warns.length > 0) {
        message.success(`《${doc.filename}》已删除`, 3)
        warns.forEach((w: string) => message.info(w, 6))
      } else {
        message.success(`《${doc.filename}》已删除`)
      }
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const runDetect = async (doc: DocItem) => {
    try {
      const res = await conflictApi.detect(doc.id)
      if (res.conflict_count > 0) {
        message.warning(
          `检测到 ${res.conflict_count} 处冲突（比对 ${res.candidate_pairs} 对候选）｜仅冲突切片被隔离，其余内容仍可检索`
        )
        setConflictDoc(doc)
        setConflictOpen(true)
      } else {
        message.success(`未检测到冲突（比对 ${res.candidate_pairs} 对候选）`)
      }
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const openTagEdit = (doc: DocItem) => {
    setTagDoc(doc)
    setTagNames([...(doc.tags ?? [])])
  }

  const saveTags = async () => {
    if (!tagDoc) return
    try {
      await docApi.setTags(tagDoc.id, tagNames)
      message.success('标签已更新（已同步下沉到切片）')
      setTagDoc(null)
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  /** 下载原文件（仅上传者本人或管理员，后端会校验）。 */
  const download = async (doc: DocItem) => {
    try {
      await docApi.download(doc.id, doc.filename)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  /** 内联预览原文件。
   *
   * ⚠️ 必须走 blob：`<iframe src="/api/v1/documents/{id}/content">` 带不上
   *    Authorization 头，会 401。拉成 blob 再用 objectURL 才能渲染。
   */
  const openPreview = async (doc: DocItem) => {
    try {
      const { url, revoke } = await docApi.preview(doc.id)
      setPreview((prev) => {
        prev?.revoke() // 关掉上一个，避免 objectURL 泄漏
        return { title: doc.filename, url, revoke }
      })
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const closePreview = () => {
    preview?.revoke()
    setPreview(null)
  }

  /** 切换公开 / 私有。仅上传者本人或管理员会成功，其他人后端返回 404/403。 */
  const toggleVisibility = async (doc: DocItem) => {
    const next = doc.visibility === 'public' ? 'private' : 'public'
    try {
      await docApi.setVisibility(doc.id, next)
      message.success(
        next === 'public' ? '已设为公开：所有人可见、可检索' : '已设为私有：仅自己可见、可检索'
      )
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const columns: ColumnsType<DocItem> = [
    {
      title: '文件名',
      dataIndex: 'filename',
      render: (name: string, row) => (
        <Space direction="vertical" size={0}>
          <Space size={6}>
            <a onClick={() => openDetail(row)}>{name}</a>
            {row.version_no > 1 && <Tag color="blue">v{row.version_no}</Tag>}
            {row.has_conflict && (
              <Tooltip title="存在待处理冲突：仅「冲突的那几片」被隔离、暂不参与检索，同文档其余内容仍可正常检索">
                <Tag icon={<ExclamationCircleOutlined />} color="error">
                  冲突待处理
                </Tag>
              </Tooltip>
            )}
          </Space>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {(row.file_size / 1024).toFixed(1)} KB · {row.file_ext}
          </Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'parse_status',
      width: 100,
      render: (s: string) => {
        const cfg = STATUS_LABEL[s] ?? { text: s, color: 'default' }
        return <Badge status={cfg.color as any} text={cfg.text} />
      },
    },
    { title: '切片', dataIndex: 'chunk_count', width: 80 },
    {
      title: '标签',
      dataIndex: 'tags',
      render: (t: string[]) =>
        !t || t.length === 0 ? (
          <Text type="secondary">—</Text>
        ) : (
          <Space wrap size={4}>
            {t.map((name) => (
              <Tag key={name} color="blue">
                {name}
              </Tag>
            ))}
          </Space>
        ),
    },
    {
      title: '可见性',
      dataIndex: 'visibility',
      width: 100,
      render: (v: string) =>
        v === 'public' ? (
          <Tooltip title="所有人可见、可检索">
            <Tag color="green">公开</Tag>
          </Tooltip>
        ) : (
          <Tooltip title="仅上传者本人可见、可检索（管理员在列表可见但检索不到）">
            <Tag color="orange">私有</Tag>
          </Tooltip>
        ),
    },
    {
      title: '上传时间',
      dataIndex: 'created_at',
      width: 160,
      render: (t: string) => (
        <Text style={{ fontSize: 12 }}>{dayjs(t).format('YYYY-MM-DD HH:mm')}</Text>
      ),
    },
    {
      title: '操作',
      width: 320,
      render: (_, row) => (
        <Space size={4}>
          <Tooltip title="查看详情">
            <Button size="small" icon={<HistoryOutlined />} onClick={() => openDetail(row)} />
          </Tooltip>
          <Tooltip title="预览原文件（浏览器内直接打开）">
            <Button size="small" icon={<EyeOutlined />} onClick={() => openPreview(row)} />
          </Tooltip>
          <Tooltip title="下载原文件（仅上传者本人或管理员）">
            <Button size="small" icon={<DownloadOutlined />} onClick={() => download(row)} />
          </Tooltip>
          {has('doc:update') && (
            <Tooltip
              title={
                row.visibility === 'public'
                  ? '设为私有（仅自己可见）'
                  : '设为公开（所有人可见）'
              }
            >
              <Button
                size="small"
                icon={row.visibility === 'public' ? <LockOutlined /> : <GlobalOutlined />}
                onClick={() => toggleVisibility(row)}
              />
            </Tooltip>
          )}
          {has('doc:tag') && (
            <Tooltip title="编辑标签">
              <Button size="small" icon={<TagsOutlined />} onClick={() => openTagEdit(row)} />
            </Tooltip>
          )}
          {has('conflict:resolve') && (
            <Tooltip title="运行冲突检测">
              <Button
                size="small"
                icon={<ExclamationCircleOutlined />}
                onClick={() => runDetect(row)}
              />
            </Tooltip>
          )}
          {row.has_conflict && has('conflict:resolve') && (
            <Button
              size="small"
              type="primary"
              danger
              onClick={() => {
                setConflictDoc(row)
                setConflictOpen(true)
              }}
            >
              处理冲突
            </Button>
          )}
          {has('doc:delete') && (
            <Popconfirm
              title="删除文档"
              description={`确认删除《${row.filename}》及其全部切片？`
              }
              okText="删除"
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
        <Space direction="vertical" size={0}>
          <Space>
            <Link to="/kb">
              <Button type="link" size="small" style={{ padding: 0 }}>
                ← 知识库
              </Button>
            </Link>
            <Title level={4} style={{ margin: 0 }}>
              {kb?.name ?? '文档管理'}
            </Title>
          </Space>
          {kb && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              向量模型 <Text className="kb-mono">{kb.embedding_model}</Text> · 切片{' '}
              {kb.chunk_size}/{kb.chunk_overlap} · 文档 {kb.document_count} 份 · 切片{' '}
              {kb.chunk_count} 条
            </Text>
          )}
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
          {has('doc:upload') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setUploadOpen(true)}>
              上传文档
            </Button>
          )}
        </Space>
      </Space>

      {kb && kb.document_count > 0 && kb.visible_document_count === 0 && (
        <Alert
          type="warning"
          showIcon
          message="你暂时没有权限查看该库的任何文档"
          description={`该知识库共有 ${kb.document_count} 份文档，但没有一份授权给你。请联系管理员为你授权，或换用有权限的账号。`}
        />
      )}

      <Table
        rowKey="id"
        columns={columns}
        dataSource={docs}
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: false }}
      />

      <UploadModal
        open={uploadOpen}
        kbId={id}
        onClose={() => setUploadOpen(false)}
        onSuccess={load}
      />

      <ConflictResolveModal
        open={conflictOpen}
        docId={conflictDoc?.id}
        docName={conflictDoc?.filename}
        onClose={() => setConflictOpen(false)}
        onResolved={load}
      />

      {/* 原文件预览抽屉：blob objectURL，关闭时 revoke 防泄漏 */}
      <Drawer
        title={`预览：${preview?.title ?? ''}`}
        open={!!preview}
        onClose={closePreview}
        width="70%"
      >
        {preview && (
          <iframe
            src={preview.url}
            title={preview.title}
            style={{ width: '100%', height: '100%', border: 'none' }}
          />
        )}
      </Drawer>

      {/* 文档详情抽屉 */}
      <Drawer
        title={detail?.filename}
        open={!!detail}
        onClose={() => setDetail(null)}
        width={780}
      >
        {detail && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="状态">
                {STATUS_LABEL[detail.parse_status]?.text ?? detail.parse_status}
              </Descriptions.Item>
              <Descriptions.Item label="版本">v{detail.version_no}</Descriptions.Item>
              <Descriptions.Item label="切片数">{detail.chunk_count}</Descriptions.Item>
              <Descriptions.Item label="大小">
                {(detail.file_size / 1024).toFixed(1)} KB
              </Descriptions.Item>
              <Descriptions.Item label="内容指纹" span={2}>
                <Text className="kb-mono" style={{ fontSize: 11 }}>
                  {detail.content_hash}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="存储路径" span={2}>
                <Space size={6} wrap>
                  <Text className="kb-mono" style={{ fontSize: 11 }}>
                    {detail.storage_path || '—'}
                  </Text>
                  <Button size="small" icon={<EyeOutlined />} onClick={() => openPreview(detail)}>
                    预览
                  </Button>
                  <Button size="small" icon={<DownloadOutlined />} onClick={() => download(detail)}>
                    下载原文件
                  </Button>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="可见性" span={2}>
                {detail.visibility === 'public' ? (
                  <Tag color="green">公开 — 所有人可见、可检索</Tag>
                ) : (
                  <Tag color="orange">私有 — 仅上传者本人可见、可检索</Tag>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="标签" span={2}>
                {detail.tags?.length ? (
                  <Space wrap size={4}>
                    {detail.tags.map((t) => (
                      <Tag key={t} color="blue">
                        {t}
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  <Text type="secondary">—</Text>
                )}
              </Descriptions.Item>
            </Descriptions>

            {versions.length > 1 && (
              <Card title="版本历史" size="small">
                <Table
                  size="small"
                  rowKey="id"
                  pagination={false}
                  dataSource={versions}
                  columns={[
                    { title: '版本', dataIndex: 'version_no', render: (v: number) => `v${v}` },
                    {
                      title: '状态',
                      dataIndex: 'is_active',
                      render: (a: boolean) =>
                        a ? <Tag color="green">当前生效</Tag> : <Tag>已取代</Tag>,
                    },
                    { title: '切片', dataIndex: 'chunk_count' },
                    {
                      title: '时间',
                      dataIndex: 'created_at',
                      render: (t: string) => dayjs(t).format('YYYY-MM-DD HH:mm'),
                    },
                  ]}
                />
              </Card>
            )}

            <Card title={`切片预览（${chunks.length} 条）`} size="small">
              <Paragraph className="kb-hint">
                注意看每条切片开头的<b>【标题路径】</b>，它由解析阶段保留并前置，
                是提升检索命中率的关键。
              </Paragraph>
              <div style={{ maxHeight: 420, overflow: 'auto' }}>
                {chunks.map((c) => (
                  <div key={c.id} className="kb-chunk">
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      #{c.chunk_index} · {c.embedding_model}
                    </Text>
                    <div>{c.content}</div>
                  </div>
                ))}
              </div>
            </Card>
          </Space>
        )}
      </Drawer>

      {/* 标签编辑 */}
      <Modal
        title={`编辑标签 — ${tagDoc?.filename ?? ''}`}
        open={!!tagDoc}
        onCancel={() => setTagDoc(null)}
        onOk={saveTags}
        okText="保存"
        cancelText="取消"
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="标签可自由填写"
          description="直接输入新标签即可，系统会自动登记进词表供下次下拉复用。保存后会同步下沉到该文档的每一条切片，检索时即可按标签过滤。"
        />
        <Select
          mode="tags"
          style={{ width: '100%' }}
          allowClear
          placeholder="如：行政、预算（回车确认，可多个）"
          tokenSeparators={[',', '，', ' ']}
          value={tagNames}
          onChange={setTagNames}
          options={tagOptions.map((o) => ({ label: o.name, value: o.name }))}
        />
      </Modal>
    </Space>
  )
}
