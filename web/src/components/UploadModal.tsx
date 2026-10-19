import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Divider,
  Form,
  Modal,
  Select,
  Space,
  Switch,
  Typography,
  Upload,
  message,
} from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'
import { docApi, tagApi } from '../api'

const { Text } = Typography

interface Props {
  open: boolean
  kbId: number
  onClose: () => void
  onSuccess: () => void
}

/**
 * 上传弹窗。
 *
 * ⚠️ 核心设计：**默认私有（最小权限）**
 *    只有显式打开「公开」开关，文档才会对所有人可见、可检索；
 *    否则只有上传者本人可见。
 *    不做「默认公开」—— 否则未留意的文档会被所有人搜到，且无法追回。
 */
export default function UploadModal({ open, kbId, onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const [options, setOptions] = useState<{ name: string; usage_count: number }[]>([])
  const [files, setFiles] = useState<UploadFile[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [results, setResults] = useState<string[]>([])
  const [isPublic, setIsPublic] = useState(false)

  useEffect(() => {
    if (!open) return
    // 词表只用于「下拉复用」，即使失败也不影响上传
    tagApi
      .options()
      .then((r) => setOptions(r.options ?? []))
      .catch(() => setOptions([]))
    form.resetFields()
    setFiles([])
    setResults([])
    setIsPublic(false)
  }, [open, form])

  const submit = async () => {
    if (files.length === 0) {
      message.warning('请先选择文件')
      return
    }
    const v = await form.validateFields()
    const tags: string[] = v.tags ?? []

    setSubmitting(true)
    const out: string[] = []
    for (const f of files) {
      const raw = f.originFileObj as File | undefined
      if (!raw) {
        out.push(`✗ ${f.name}：文件读取失败`)
        continue
      }
      try {
        // ⚠️ 用 uploadAndWait 而不是 upload：上传已改为异步受理，
        //    直接用 upload 拿到的 conflict_count 恒为 0，
        //    「检出冲突」的提示会静默失效。
        const res = await docApi.uploadAndWait(kbId, raw, isPublic, tags)
        if (res.status === 'failed') {
          out.push(`✗ ${f.name}：${res.error_msg ?? '处理失败'}`)
        } else if (res.conflict_status === 'checking') {
          // 两段式：入库已完成、冲突检测仍在后台跑。
          // ⚠️ 此处绝不能落到下面的「未发现冲突」分支 —— 那会把"还没判"说成"没冲突"。
          out.push(
            `✓ ${f.name}：已解析入库（${res.chunk_count ?? 0} 个切片）｜` +
              '冲突检测仍在后台进行，稍后刷新文档列表即可看到结果',
          )
        } else if (res.conflict_status === 'failed') {
          out.push(
            `✓ ${f.name}：已解析入库（${res.chunk_count ?? 0} 个切片）｜` +
              '冲突检测未完成，可到「任务」列表查看',
          )
        } else if (res.conflict_count > 0) {
          const who = res.conflict_docs?.length
            ? `，与《${res.conflict_docs.join('》《')}》矛盾`
            : ''
          out.push(
            `⚠ ${f.name}：检出 ${res.conflict_count} 处冲突${who}｜已暂停参与检索，请到「冲突管理」处理`
          )
        } else {
          out.push(
            `✓ ${f.name}：已解析入库（${res.chunk_count ?? 0} 个切片），未发现冲突`
          )
        }
        for (const w of res.warnings ?? []) out.push(`    · ${w}`)
      } catch (e) {
        out.push(`✗ ${f.name}：${(e as Error).message}`)
      }
    }
    setResults(out)
    setSubmitting(false)
    onSuccess()
  }

  return (
    <Modal
      title="上传文档"
      open={open}
      onCancel={onClose}
      width={580}
      destroyOnClose
      footer={
        results.length > 0
          ? [
              <Button key="done" type="primary" onClick={onClose}>
                完成
              </Button>,
            ]
          : undefined
      }
      okText="开始上传"
      confirmLoading={submitting}
      onOk={submit}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="上传后系统会自动做两件事"
        description={
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li>
              <b>可见性</b>：不打开「公开」开关时，文档只有你自己能看、能搜
              （管理员在文档列表能看到，但检索同样搜不到）
            </li>
            <li>
              <b>冲突检测</b>：自动与库内已有文档比对，
              若发现规定或数值互相矛盾会立即提示，并暂停该文档参与检索，等人工处理
            </li>
          </ul>
        }
      />

      <Form form={form} layout="vertical">
        <Form.Item label="选择文件" required>
          <Upload.Dragger
            multiple
            accept=".pdf,.docx,.xlsx,.xlsm,.xls,.md,.markdown,.txt,.csv"
            fileList={files}
            beforeUpload={() => false}
            onChange={({ fileList }) => setFiles(fileList)}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此处</p>
            <p className="ant-upload-hint">
              支持 pdf / docx / xlsx / xlsm / xls（含 2003 旧版）/ md / txt / csv
            </p>
          </Upload.Dragger>
        </Form.Item>

        <Form.Item label="是否公开">
          <Space>
            <Switch checked={isPublic} onChange={setIsPublic} />
            <Text type={isPublic ? 'success' : 'secondary'}>
              {isPublic ? '公开：所有人可见、可检索' : '私有：仅自己可见、可检索'}
            </Text>
          </Space>
        </Form.Item>

        <Form.Item
          name="tags"
          label="标签（可选）"
          tooltip="可直接输入新标签，系统会自动记录，下次可下拉选择"
        >
          <Select
            mode="tags"
            allowClear
            placeholder="如：行政、预算（回车确认，可多个）"
            tokenSeparators={[',', '，', ' ']}
            options={options.map((o) => ({ label: o.name, value: o.name }))}
          />
        </Form.Item>
      </Form>

      {results.length > 0 && (
        <>
          <Divider>上传结果</Divider>
          <Space direction="vertical" style={{ width: '100%' }}>
            {results.map((r, i) => (
              <Text
                key={i}
                type={
                  r.startsWith('✓')
                    ? 'success'
                    : r.startsWith('⚠')
                      ? 'warning'
                      : r.trimStart().startsWith('·')
                        ? 'secondary'
                        : 'danger'
                }
                style={{ whiteSpace: 'pre-wrap' }}
              >
                {r}
              </Text>
            ))}
          </Space>
        </>
      )}
    </Modal>
  )
}
