import { useEffect, useState } from 'react'
import { Empty, Tag, Typography } from 'antd'
import { agentHttp } from '../../api/client'
import { msText } from './types'

type Span = {
  id: number
  kind: string
  name: string
  parent_id: number | null
  call_id: string
  level: string
  ms: number
  input?: any
  output?: any
}

const KIND: Record<string, { label: string; color: string }> = {
  agent: { label: '一轮', color: 'geekblue' },
  generation: { label: '模型', color: 'purple' },
  tool: { label: '工具', color: 'green' },
}

function depthOf(s: Span, byId: Map<number, Span>): number {
  let d = 0
  let cur = s
  while (cur.parent_id && byId.has(cur.parent_id) && d < 8) {
    cur = byId.get(cur.parent_id) as Span
    d++
  }
  return d
}

export default function TraceRail({ runId }: { runId: string }) {
  const [rows, setRows] = useState<Span[]>([])

  useEffect(() => {
    setRows([])
    if (!runId) return
    let alive = true
    let ticks = 0
    const load = async () => {
      try {
        const r: any = await agentHttp.get(`/api/agent/runs/${runId}/spans`)
        if (alive) setRows(r.items || [])
      } catch { /* 还没落库 */ }
    }
    void load()
    const timer = setInterval(() => {
      ticks++
      if (ticks > 40) { clearInterval(timer); return }
      void load()
    }, 1500)
    return () => { alive = false; clearInterval(timer) }
  }, [runId])

  return (
    <div style={{ width: 320, flex: '0 0 320px', background: '#fff', borderRadius: 12, padding: 12, overflow: 'auto' }}>
      <Typography.Text strong>运行步骤</Typography.Text>
      <div style={{ fontSize: 12, color: '#999', marginTop: 2 }}>调试用；交付都在左边对话里</div>
      <div style={{ marginTop: 12 }}>
        {rows.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有步骤" />
        ) : (() => {
          const byId = new Map(rows.map((r) => [r.id, r]))
          return rows.map((s) => {
            const k = KIND[s.kind] || { label: s.kind, color: 'default' }
            const pad = depthOf(s, byId) * 14
            const brief = s.kind === 'tool'
              ? (s.output && typeof s.output.result === 'string' ? s.output.result : '').slice(0, 60)
              : s.kind === 'generation' ? `${(s.output || {}).tokens || 0} tokens` : ''
            return (
              <div key={s.id} style={{ marginLeft: pad, padding: '3px 0', fontSize: 12 }}>
                <Tag color={s.level === 'ERROR' ? 'red' : k.color} style={{ marginInlineEnd: 6 }}>{k.label}</Tag>
                <span>{s.name || s.kind}</span>
                {s.ms ? <span style={{ color: '#bbb' }}> · {msText(s.ms)}</span> : null}
                {brief && <div style={{ color: '#999', marginLeft: 2 }}>{brief.replace(/\s+/g, ' ')}</div>}
              </div>
            )
          })
        })()}
      </div>
    </div>
  )
}
