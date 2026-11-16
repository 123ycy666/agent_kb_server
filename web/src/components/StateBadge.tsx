import type { ReactNode } from 'react'
import { Tag, Tooltip } from 'antd'
import type { ConfigState } from '../api'

const COLOR: Record<ConfigState, string> = {
  可改: 'green',
  '只读·库': 'blue',
  '只读·代码': 'default',
}

export default function StateBadge({
  state,
  source,
  updatedBy,
  updatedAt,
  extra,
}: {
  state: ConfigState | string
  source?: string
  updatedBy?: string
  updatedAt?: string
  extra?: ReactNode
}) {
  const color = COLOR[state as ConfigState] ?? 'default'
  const title = (
    <div style={{ fontSize: 12, lineHeight: 1.8 }}>
      <div>
        来源：{source || '—'}
      </div>
      <div>最近修改：{updatedBy || '—'}</div>
      {updatedAt ? <div>修改时间：{updatedAt}</div> : null}
      {extra}
    </div>
  )
  return (
    <Tooltip title={title}>
      <Tag color={color} style={{ marginInlineEnd: 0, cursor: 'help' }}>
        {state}
      </Tag>
    </Tooltip>
  )
}
