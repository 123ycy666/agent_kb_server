import { useState } from 'react'
import { Button, Spin, Typography } from 'antd'
import {
  CheckCircleFilled, CloseCircleFilled, DownOutlined, FileExcelOutlined, FilePdfOutlined,
  FilePptOutlined, FileTextOutlined, RightOutlined,
} from '@ant-design/icons'
import { msText, sizeText, TOOL_LABEL, type FilePart, type Msg, type Part, type ToolPart } from './types'

const mono: React.CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 12,
  background: '#fafafa',
  border: '1px solid #f0f0f0',
  borderRadius: 6,
  padding: '6px 8px',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
  maxHeight: 260,
  overflow: 'auto',
}

function ToolLine({ p }: { p: ToolPart }) {
  const [open, setOpen] = useState(false)
  const icon = p.status === 'running'
    ? <Spin size="small" />
    : p.isError
      ? <CloseCircleFilled style={{ color: '#ff4d4f' }} />
      : <CheckCircleFilled style={{ color: '#52c41a' }} />
  const label = TOOL_LABEL[p.name] || p.name
  const args = p.argsText.length > 2000 ? p.argsText.slice(0, 2000) + '…' : p.argsText
  return (
    <div>
      <div
        onClick={() => setOpen(!open)}
        style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '3px 0', fontSize: 13 }}
      >
        {icon}
        <span>{label}</span>
        <span style={{ color: '#bbb', fontSize: 12 }}>{p.name}</span>
        {p.status !== 'running' && <span style={{ color: '#bbb', fontSize: 12 }}>{msText(p.ms)}</span>}
        {open ? <DownOutlined style={{ fontSize: 10, color: '#bbb' }} />
          : <RightOutlined style={{ fontSize: 10, color: '#bbb' }} />}
      </div>
      {open && (
        <div style={{ margin: '2px 0 8px 22px' }}>
          {p.isError && p.result && (
            <div style={{ ...mono, color: '#cf1322', marginBottom: 6 }}>{p.result}</div>
          )}
          {args && <div style={{ ...mono, marginBottom: 6 }}>{args}</div>}
          {p.result && !p.isError && <div style={mono}>{p.result}</div>}
        </div>
      )}
    </div>
  )
}

function ToolGroup({ items }: { items: ToolPart[] }) {
  const done = items.filter((x) => x.status !== 'running').length
  const failed = items.filter((x) => x.isError).length
  const head = failed
    ? `${failed} 步失败`
    : done < items.length ? `${done}/${items.length} 步完成` : `${items.length} 步完成`
  return (
    <div style={{ margin: '6px 0', padding: '6px 10px', background: '#fbfbfb', border: '1px solid #f0f0f0', borderRadius: 8 }}>
      <div style={{ fontSize: 12, color: '#999', marginBottom: 2 }}>{head}</div>
      {items.map((p) => <ToolLine key={p.toolCallId} p={p} />)}
    </div>
  )
}

function FileIcon({ mime }: { mime: string }) {
  if (mime.includes('sheet')) return <FileExcelOutlined style={{ color: '#1d7a46' }} />
  if (mime.includes('presentation')) return <FilePptOutlined style={{ color: '#c2410c' }} />
  if (mime.includes('pdf')) return <FilePdfOutlined style={{ color: '#b91c1c' }} />
  return <FileTextOutlined style={{ color: '#1677ff' }} />
}

function FileCard({ f }: { f: FilePart }) {
  return (
    <a
      href={f.url}
      target="_blank"
      rel="noreferrer"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 10, marginTop: 8, marginRight: 8,
        padding: '8px 12px', border: '1px solid #e5e7eb', borderRadius: 8, background: '#fff',
        color: '#111', textDecoration: 'none',
      }}
    >
      <FileIcon mime={f.mime} />
      <span style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {f.name}
      </span>
      <span style={{ color: '#999', fontSize: 12 }}>{sizeText(f.size)}</span>
    </a>
  )
}

function Groups({ parts }: { parts: Part[] }) {
  const blocks: Array<{ type: 'tool'; items: ToolPart[] } | { type: 'one'; part: Part }> = []
  for (const p of parts) {
    const last = blocks[blocks.length - 1]
    if (p.kind === 'tool' && last && last.type === 'tool') last.items.push(p)
    else if (p.kind === 'tool') blocks.push({ type: 'tool', items: [p] })
    else blocks.push({ type: 'one', part: p })
  }
  return (
    <>
      {blocks.map((b, i) => {
        if (b.type === 'tool') return <ToolGroup key={i} items={b.items} />
        const p = b.part
        if (p.kind === 'text') {
          return p.text ? (
            <div key={i} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: '4px 0' }}>{p.text}</div>
          ) : null
        }
        return p.kind === 'file' ? <div key={i}><FileCard f={p} /></div> : null
      })}
    </>
  )
}

export default function MessageView({ m }: { m: Msg }) {
  if (m.role === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '10px 0' }}>
        <div style={{ maxWidth: '76%', background: '#e6f4ff', borderRadius: 10, padding: '8px 12px' }}>
          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.text}</div>
          {(m.files || []).length > 0 && (
            <div style={{ marginTop: 6, fontSize: 12, color: '#555' }}>
              {(m.files || []).map((f) => (
                <div key={f.fileId}><FileTextOutlined /> {f.name}</div>
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }
  return (
    <div style={{ margin: '10px 0', maxWidth: '86%' }}>
      <Groups parts={m.parts} />
      {m.status === 'running' && m.parts.length === 0 && <Spin size="small" />}
      {m.status === 'stopped' && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>已停止</Typography.Text>
      )}
      {m.status === 'error' && (
        <div style={{ marginTop: 6, padding: '6px 10px', background: '#fff1f0', border: '1px solid #ffccc7', borderRadius: 8, color: '#cf1322', fontSize: 13 }}>
          {m.error || '出错了'}
        </div>
      )}
      {(m.usage || m.ms) ? (
        <div style={{ marginTop: 4, fontSize: 11, color: '#c0c0c0' }}>
          {m.ms ? msText(m.ms) : ''}{m.usage ? ` · ${m.usage} tokens` : ''}
        </div>
      ) : null}
    </div>
  )
}

export function JumpToBottom({ show, onClick }: { show: boolean; onClick: () => void }) {
  if (!show) return null
  return (
    <Button
      size="small"
      shape="round"
      onClick={onClick}
      style={{ position: 'absolute', right: 24, bottom: 92, zIndex: 5, boxShadow: '0 2px 8px rgba(0,0,0,.12)' }}
    >
      回到底部
    </Button>
  )
}
