import { useCallback, useEffect, useState } from 'react'
import { Button, Drawer, Space, Table, Tabs, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { Link } from 'react-router-dom'
import Page from '../components/ui/Page'
import { useTab } from '../components/ui/useTab'
import { AskfillEditor, BodyView, SectionList, TextKeyEditor } from '../components/PromptEditors'
import { promptsApi } from '../api'

const { Text, Paragraph } = Typography

type Row = {
  key: string
  name: string
  editor: 'material' | 'text' | 'askfill'
  arg: string
  size: string
  where: string
}

const ROWS: Row[] = [
  { key: 'material_fetch', name: '口径材料', editor: 'material', arg: 'fetch', size: '', where: 'prompt_material' },
  { key: 'material_answer', name: '作答规则', editor: 'material', arg: 'answer', size: '', where: 'prompt_material' },
  { key: 'askfill', name: '补问法', editor: 'askfill', arg: '', size: '', where: 'prompt_text' },
  { key: 'confirm', name: '问题确定', editor: 'text', arg: 'confirm', size: '', where: 'prompt_text' },
]

function ChainTab() {
  const [rows, setRows] = useState<Row[]>(ROWS)
  const [open, setOpen] = useState<Row | null>(null)
  const [body, setBody] = useState<any>(null)
  const [tick, setTick] = useState(0)

  const load = useCallback(() => {
    promptsApi
      .impact()
      .then((r: any) => {
        const flat: any[] = (r.segments ?? []).flatMap((s: any) => s.items ?? [])
        setRows(
          ROWS.map((row) => {
            const hit = flat.find((x: any) => x.key === row.key)
            return { ...row, size: hit?.size || '—', where: hit?.where || row.where }
          })
        )
      })
      .catch((e) => message.error((e as Error).message))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!open) {
      setBody(null)
      return
    }
    promptsApi
      .impactBody(open.key)
      .then((r: any) => setBody(r))
      .catch((e) => message.error((e as Error).message))
  }, [open, tick])

  const columns: ColumnsType<Row> = [
    { title: '这一份', dataIndex: 'name', width: 160, render: (v: string) => <Text strong>{v}</Text> },
    { title: '存在哪', dataIndex: 'where', width: 200, render: (v: string) => <Text code>{v}</Text> },
    {
      title: '现在多大',
      dataIndex: 'size',
      width: 160,
      render: (v: string) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{v}</span>,
    },
    {
      title: '操作',
      width: 100,
      render: (_: unknown, row: Row) => (
        <Button type="link" size="small" onClick={() => setOpen(row)} aria-label={`编辑 ${row.name}`}>
          编辑
        </Button>
      ),
    },
  ]

  return (
    <>
      <Table<Row>
        size="small"
        rowKey="key"
        columns={columns}
        dataSource={rows}
        pagination={false}
      />
      <Drawer
        open={open != null}
        width={900}
        title={open ? open.name : ''}
        onClose={() => setOpen(null)}
      >
        {open ? (
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            {open.editor === 'material' ? (
              <SectionList slot={open.arg} onChanged={() => setTick((t) => t + 1)} />
            ) : open.editor === 'askfill' ? (
              <AskfillEditor />
            ) : (
              <TextKeyEditor keyName={open.arg} onChanged={() => setTick((t) => t + 1)} />
            )}
            <div>
              <Text strong>现在注入的原文</Text>
              <div style={{ marginTop: 6 }}>
                <BodyView data={body} />
              </div>
            </div>
          </Space>
        ) : null}
      </Drawer>
    </>
  )
}

function AgentTab() {
  return (
    <Space direction="vertical" size={12} style={{ width: '100%', maxWidth: 900 }}>
      <div>
        <Text strong>身份</Text>
        <div style={{ marginTop: 6 }}>
          <TextKeyEditor keyName="identity" />
        </div>
      </div>
      <Paragraph style={{ margin: 0 }}>
        回答风格、技能读取范围、工具开关在 <Link to="/agent">智能体页</Link> 改。
      </Paragraph>
    </Space>
  )
}

export default function Prompts() {
  const [tab, setTab] = useTab('ask', ['ask', 'agent'])
  return (
    <Page title="提示词">
      <div style={{ background: '#fff', borderRadius: 8, padding: 16 }}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            { key: 'ask', label: '问数线', children: <ChainTab /> },
            { key: 'agent', label: '智能体', children: <AgentTab /> },
          ]}
        />
      </div>
    </Page>
  )
}
