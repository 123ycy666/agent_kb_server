import { useState } from 'react'
import { Button, Input, Tooltip, Typography, message } from 'antd'
import {
  CloseCircleFilled, LoadingOutlined, PaperClipOutlined, SendOutlined, StopOutlined,
} from '@ant-design/icons'
import { agentHttp } from '../../api/client'
import type { FilePart } from './types'

type Pending = {
  id: string
  name: string
  status: 'uploading' | 'ready' | 'error'
  fileId?: string
  size?: number
  mime?: string
  note?: string
}

let seq = 0

export default function Composer({
  running,
  onSend,
  onStop,
}: {
  running: boolean
  onSend: (text: string, files: FilePart[]) => void
  onStop: () => void
}) {
  const [text, setText] = useState('')
  const [items, setItems] = useState<Pending[]>([])

  const pick = async (file: File) => {
    const id = 'p' + ++seq
    setItems((all) => [...all, { id, name: file.name, status: 'uploading' }])
    const form = new FormData()
    form.append('file', file)
    form.append('purpose', 'chat')
    try {
      const r: any = await agentHttp.post('/api/files', form)
      setItems((all) => all.map((x) => x.id === id
        ? { ...x, status: 'ready', fileId: r.file_id, name: r.name || file.name, size: r.size || file.size }
        : x))
    } catch (e: any) {
      const note = e?.response?.data?.detail?.message || '上传失败'
      setItems((all) => all.map((x) => (x.id === id ? { ...x, status: 'error', note } : x)))
    }
  }

  const ready: FilePart[] = items
    .filter((x) => x.status === 'ready' && x.fileId)
    .map((x) => ({
      kind: 'file', fileId: x.fileId as string, name: x.name,
      size: x.size || 0, mime: x.mime || '', url: `/api/files/${x.fileId}`,
    }))

  const submit = () => {
    const t = text.trim()
    if (!t || running) return
    if (items.some((x) => x.status === 'uploading')) {
      message.info('文件还在上传，等它传完再发')
      return
    }
    setText('')
    setItems([])
    onSend(t, ready)
  }

  return (
    <div style={{ borderTop: '1px solid #f0f0f0', padding: 12 }}>
      {items.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
          {items.map((x) => (
            <span
              key={x.id}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 8px',
                border: '1px solid ' + (x.status === 'error' ? '#ffccc7' : '#e5e7eb'),
                borderRadius: 6, fontSize: 12,
                background: x.status === 'error' ? '#fff1f0' : '#fafafa',
              }}
            >
              {x.status === 'uploading' && <LoadingOutlined />}
              {x.status === 'error' && <CloseCircleFilled style={{ color: '#ff4d4f' }} />}
              <Tooltip title={x.status === 'error' ? x.note : x.name}>{x.name}</Tooltip>
              <a onClick={() => setItems((all) => all.filter((y) => y.id !== x.id))} style={{ color: '#999' }}>×</a>
            </span>
          ))}
        </div>
      )}
      <Input.TextArea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            submit()
          }
        }}
        placeholder="说一件事，或者拖一个文件进来…（Enter 发送，Shift+Enter 换行）"
        autoSize={{ minRows: 2, maxRows: 8 }}
        style={{ resize: 'none' }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
        <label style={{ cursor: 'pointer', color: '#666' }}>
          <PaperClipOutlined />
          <input
            type="file"
            style={{ display: 'none' }}
            accept=".docx,.xlsx,.pptx,.pdf,.txt,.md,.csv"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void pick(f)
              e.target.value = ''
            }}
          />
        </label>
        <Typography.Text type="secondary" style={{ fontSize: 12, flex: 1 }}>
          要文件直接说，成品会以卡片出现在对话里
        </Typography.Text>
        {running ? (
          <Button danger icon={<StopOutlined />} onClick={onStop}>停止</Button>
        ) : (
          <Button type="primary" icon={<SendOutlined />} onClick={submit} disabled={!text.trim()}>发送</Button>
        )}
      </div>
    </div>
  )
}
