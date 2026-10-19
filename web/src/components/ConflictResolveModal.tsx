import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Modal,
  Radio,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import { WarningOutlined } from '@ant-design/icons'
import { conflictApi } from '../api'
import type { ConflictItem } from '../api'

const { Text, Paragraph, Title } = Typography

interface Props {
  open: boolean
  docId?: number
  docName?: string
  onClose: () => void
  onResolved?: () => void
}

const RESOLUTIONS = [
  {
    value: 'overwrite',
    label: '用新文档覆盖旧版本',
    desc: '旧文档标记为已取代并退出检索，新文档正式生效',
  },
  {
    value: 'keep_old',
    label: '保留旧文档，丢弃新上传',
    desc: '删除本次上传的文档及其切片',
  },
  {
    value: 'coexist',
    label: '两者并存，仅记录冲突',
    desc: '解除新文档的检索排除，但冲突记录保留备查',
  },
  {
    value: 'ignore',
    label: '忽略此冲突',
    desc: '保留双方，清除待处理标记，不记录为已解决',
  },
  { value: 'cancel', label: '取消本次上传', desc: '等同于丢弃新文档' },
]

const RELATION_LABEL: Record<string, { text: string; color: string }> = {
  contradiction: { text: '真冲突（事实矛盾）', color: 'red' },
  entailment: { text: '蕴含/细化', color: 'blue' },
  duplication: { text: '重复', color: 'default' },
  unrelated: { text: '无关', color: 'default' },
}

const TYPE_LABEL: Record<string, string> = {
  fact: '事实型',
  rule: '规则型',
  temporal: '时效型',
  wording: '表述型',
}

/**
 * 冲突决策弹窗。
 *
 * 展示「新文件片段 vs 库内文件片段」左右对照 + 判定理由，由人工决策。
 * API 上传的文档不弹此框，而是入库挂起 + 通知管理员，在同一处处理。
 */
export default function ConflictResolveModal({ open, docId, docName, onClose, onResolved }: Props) {
  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<ConflictItem[]>([])
  const [choices, setChoices] = useState<Record<number, string>>({})
  const [submitting, setSubmitting] = useState(false)

  const load = async () => {
    if (!docId) return
    setLoading(true)
    try {
      const res = await conflictApi.list({ doc_id: docId, page_size: 50 })
      setItems(res.items)
      const init: Record<number, string> = {}
      res.items.forEach((c) => {
        init[c.id] = 'overwrite'
      })
      setChoices(init)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open && docId) load()
  }, [open, docId])

  const submitAll = async () => {
    setSubmitting(true)
    try {
      for (const c of items) {
        if (c.status !== 'pending') continue
        await conflictApi.resolve(c.id, choices[c.id] ?? 'overwrite')
      }
      message.success('冲突已处理')
      onResolved?.()
      onClose()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const pending = items.filter((c) => c.status === 'pending')

  return (
    <Modal
      title={
        <Space>
          <WarningOutlined style={{ color: '#faad14' }} />
          内容冲突检测结果
        </Space>
      }
      open={open}
      onCancel={onClose}
      width={1000}
      destroyOnClose
      footer={
        pending.length > 0
          ? [
              <Button key="cancel" onClick={onClose}>
                稍后处理
              </Button>,
              <Button key="ok" type="primary" loading={submitting} onClick={submitAll}>
                提交全部决策（{pending.length} 项）
              </Button>,
            ]
          : [
              <Button key="close" type="primary" onClick={onClose}>
                关闭
              </Button>,
            ]
      }
    >
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message={
          <span>
            《{docName}》与库内文档存在内容矛盾。该文档已入库但 <b>暂不参与检索</b>，
            直至冲突处理完毕。
          </span>
        }
        description={
          <Paragraph style={{ marginBottom: 0, fontSize: 12 }}>
            检测范围仅包含<b>你有权访问的文档</b>（受限文档不参与比对），
            因此不会泄露你无权查看的内容。
          </Paragraph>
        }
      />

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : items.length === 0 ? (
        <Empty description="未检测到冲突" />
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {items.map((c) => {
            const rel = RELATION_LABEL[c.relation] ?? { text: c.relation, color: 'default' }
            return (
              <div
                key={c.id}
                style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: 12 }}
              >
                <Space style={{ marginBottom: 8 }} wrap>
                  <Tag color={rel.color}>{rel.text}</Tag>
                  <Tag>{TYPE_LABEL[c.conflict_type] ?? c.conflict_type}</Tag>
                  {c.similarity_score != null && (
                    <Tag color="processing">向量相似度 {Number(c.similarity_score).toFixed(3)}</Tag>
                  )}
                  {c.status !== 'pending' && <Tag color="success">已处理</Tag>}
                </Space>

                <Descriptions size="small" column={2} style={{ marginBottom: 8 }}>
                  <Descriptions.Item label="冲突文件">
                    <Text strong>{c.old_doc_name ?? `#${c.old_doc_id}`}</Text>
                    {c.old_doc_version ? ` (v${c.old_doc_version})` : ''}
                  </Descriptions.Item>
                  <Descriptions.Item label="判定理由">
                    <Text>{c.reason}</Text>
                  </Descriptions.Item>
                </Descriptions>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      新上传内容
                    </Text>
                    <div className="kb-snippet-new">{c.new_snippet ?? '（无）'}</div>
                  </div>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      库内现有内容
                    </Text>
                    <div className="kb-snippet-old">{c.old_snippet ?? '（无）'}</div>
                  </div>
                </div>

                {c.status === 'pending' && (
                  <div style={{ marginTop: 10 }}>
                    <Text strong style={{ fontSize: 12 }}>
                      处理方式：
                    </Text>
                    <Radio.Group
                      style={{ display: 'block', marginTop: 6 }}
                      value={choices[c.id]}
                      onChange={(e) => setChoices((s) => ({ ...s, [c.id]: e.target.value }))}
                    >
                      <Space direction="vertical" size={2}>
                        {RESOLUTIONS.map((r) => (
                          <Radio key={r.value} value={r.value}>
                            <Text>{r.label}</Text>
                            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                              {r.desc}
                            </Text>
                          </Radio>
                        ))}
                      </Space>
                    </Radio.Group>
                  </div>
                )}
              </div>
            )
          })}
        </Space>
      )}
    </Modal>
  )
}
