import { useState } from 'react'
import type { Key } from 'react'
import {
  Alert,
  Button,
  Empty,
  Input,
  InputNumber,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { http } from '../api/client'
import CaliberMetricDrawer, { mustText } from './CaliberMetricDrawer'
import type { ParsedMetric } from './CaliberMetricDrawer'

const { Text } = Typography

type TryRes = {
  value?: string | number
  unit?: string
  label?: string
  year?: string
  error?: string
}

function TryRun({ code }: { code: string }) {
  const [year, setYear] = useState(String(new Date().getFullYear()))
  const [scope, setScope] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<TryRes | null>(null)

  const run = async () => {
    setBusy(true)
    setRes(null)
    try {
      const r: any = await http.get('/caliber/try', {
        params: { code, year: year.trim(), scope_id: scope },
      })
      setRes(r || {})
    } catch (e) {
      setRes({ error: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Space wrap align="end">
        <div>
          <div style={{ marginBottom: 4 }}>年份</div>
          <Input
            size="small"
            value={year}
            style={{ width: 90 }}
            onChange={(e) => setYear(e.target.value)}
          />
        </div>
        <div>
          <div style={{ marginBottom: 4 }}>对象 id</div>
          <InputNumber
            size="small"
            value={scope}
            style={{ width: 120 }}
            placeholder="可留空"
            onChange={(v) => setScope(v == null ? null : Number(v))}
          />
        </div>
        <Button size="small" type="primary" loading={busy} onClick={() => void run()}>
          试跑
        </Button>
      </Space>
      {res?.error ? (
        <Text type="danger">{res.error}</Text>
      ) : res ? (
        <Text>
          {res.label || code}={res.value}
          {res.unit || ''}
        </Text>
      ) : null}
    </Space>
  )
}

export default function CaliberInline({ text }: { text: string }) {
  const [rows, setRows] = useState<ParsedMetric[]>([])
  const [unresolved, setUnresolved] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [edit, setEdit] = useState<ParsedMetric | null>(null)
  const [expanded, setExpanded] = useState<readonly Key[]>([])

  const runParse = async () => {
    setBusy(true)
    try {
      const r: any = await http.post('/caliber/parse', { text })
      if (r?.error) {
        message.error(String(r.error))
        return
      }
      setRows(r.metrics || [])
      setUnresolved(r.unresolved || [])
      setExpanded([])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const columns: ColumnsType<ParsedMetric> = [
    {
      title: '指标名',
      width: 200,
      render: (_: unknown, r: ParsedMetric) => (
        <div>
          <div>{r.label || '—'}</div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {r.code}
          </Text>
        </div>
      ),
    },
    {
      title: '类型',
      width: 80,
      render: (_: unknown, r: ParsedMetric) =>
        r.kind === 'derived' ? <Tag color="blue">派生</Tag> : <Tag>取数</Tag>,
    },
    {
      title: '公式',
      width: 220,
      render: (_: unknown, r: ParsedMetric) =>
        r.kind === 'derived' && r.expr ? <Text code>{r.expr}</Text> : '—',
    },
    {
      title: '必需过滤',
      width: 280,
      render: (_: unknown, r: ParsedMetric) => {
        const chips = Array.from(new Set((r.must || []).map(mustText)))
        return chips.length ? (
          <Space size={4} wrap>
            {chips.map((c) => (
              <Tag key={c}>{c}</Tag>
            ))}
          </Space>
        ) : (
          '—'
        )
      },
    },
    {
      title: '操作',
      width: 110,
      render: (_: unknown, r: ParsedMetric) => (
        <Space size="middle">
          <a onClick={() => setEdit(r)}>改</a>
          <a
            onClick={() =>
              setExpanded((v) =>
                v.includes(r.code) ? v.filter((k) => k !== r.code) : [...v, r.code]
              )
            }
          >
            试跑
          </a>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Space wrap align="center" style={{ marginBottom: 8 }}>
        <Text type="secondary">机器从这段口径里读出来的东西（只读，可点开改）</Text>
        <Button size="small" icon={<ReloadOutlined />} loading={busy} onClick={() => void runParse()}>
          让机器重新读这段
        </Button>
      </Space>

      {unresolved.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 8 }}
          message={`这句机器没读懂：${unresolved.join('；')}`}
          description="换个说法重写，或直接点开改。"
        />
      )}

      <Table
        rowKey="code"
        size="small"
        dataSource={rows}
        columns={columns}
        pagination={false}
        scroll={{ x: 'max-content' }}
        locale={{ emptyText: <Empty description="还没读；点「让机器重新读这段」" /> }}
        expandable={{
          expandedRowKeys: expanded,
          onExpandedRowsChange: setExpanded,
          showExpandColumn: false,
          expandedRowRender: (r: ParsedMetric) => <TryRun code={r.code} />,
        }}
      />

      {edit && (
        <CaliberMetricDrawer
          seed={edit}
          onClose={() => setEdit(null)}
          onSaved={() => void runParse()}
        />
      )}
    </div>
  )
}
