import { useCallback, useEffect, useState } from 'react'
import { Button, Drawer, Space, Table, Tabs, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { Link } from 'react-router-dom'
import Page from '../components/ui/Page'
import { useTab } from '../components/ui/useTab'
import { AskfillEditor, BodyView, SectionList, TextKeyEditor } from '../components/PromptEditors'
import { CompleteEditor, LexiconEditor, RouteRuleEditor } from '../components/ChainEditors'
import { promptsApi } from '../api'

const { Text, Paragraph } = Typography

type Item = {
  key: string
  seg: string
  name: string
  where: string
  size: string
  editable: boolean
  takes_effect: string
  note: string
  editor: string
}

function ChainTab() {
  const [rows, setRows] = useState<Item[]>([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Item | null>(null)
  const [body, setBody] = useState<any>(null)
  const [tick, setTick] = useState(0)

  const load = useCallback(() => {
    setLoading(true)
    promptsApi
      .impact()
      .then((r: any) => {
        const flat: Item[] = []
        for (const s of r.segments ?? []) {
          for (const it of s.items ?? []) flat.push({ ...it, seg: s.name })
        }
        setRows(flat)
      })
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false))
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

  const columns: ColumnsType<Item> = [
    { title: '环节', dataIndex: 'seg', width: 170, render: (v: string) => <Text strong>{v}</Text> },
    { title: '这一份', dataIndex: 'name', ellipsis: true },
    { title: '存哪', dataIndex: 'where', width: 230, render: (v: string) => <Text code>{v}</Text> },
    {
      title: '现在多大',
      dataIndex: 'size',
      width: 120,
      render: (v: string) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{v}</span>,
    },
    { title: '生效', dataIndex: 'takes_effect', width: 90 },
    {
      title: '操作',
      width: 90,
      render: (_: unknown, row: Item) => (
        <Button type="link" size="small" onClick={() => setOpen(row)} aria-label={`${row.editable ? '编辑' : '查看'} ${row.name}`}>
          {row.editable ? '编辑' : '查看'}
        </Button>
      ),
    },
  ]

  return (
    <>
      <Table<Item>
        size="small"
        rowKey="key"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={false}
      />
      <Drawer open={open != null} width={900} title={open ? open.name : ''} onClose={() => setOpen(null)}>
        {open ? (
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <Space size={16} wrap>
              <Text type="secondary">环节：{open.seg}</Text>
              <Text type="secondary">存哪：{open.where}</Text>
              <Text type="secondary">生效：{open.takes_effect}</Text>
              <Text type="secondary">{open.note}</Text>
            </Space>
            {open.editor.startsWith('material:') ? (
              <SectionList slot={open.editor.split(':')[1]} onChanged={() => setTick((t) => t + 1)} />
            ) : open.editor === 'askfill' ? (
              <AskfillEditor />
            ) : open.editor === 'complete' ? (
              <CompleteEditor />
            ) : open.editor === 'lexicon' ? (
              <LexiconEditor />
            ) : open.editor === 'route_rule' ? (
              <RouteRuleEditor />
            ) : open.editor.startsWith('text:') ? (
              <TextKeyEditor keyName={open.editor.split(':')[1]} onChanged={() => setTick((t) => t + 1)} />
            ) : null}
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
    <Paragraph style={{ margin: 0 }}>
      身份在问数线链路的第 6 项（装配 system）；回答风格、技能读取范围、工具开关、模型在{' '}
      <Link to="/agent">智能体页</Link>。
    </Paragraph>
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
