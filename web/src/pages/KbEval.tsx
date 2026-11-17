import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Input,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, PlayCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { catalogApi, kbApi } from '../api'
import type { KnowledgeBase } from '../api'

const { Text, Paragraph } = Typography

/** 黄金问题集：一组固定问题，改完目录/切片/检索跑一遍看命中率。 */
export default function KbEval() {
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [kbId, setKbId] = useState<number | null>(null)
  const [rows, setRows] = useState<any[]>([])
  const [q, setQ] = useState('')
  const [expect, setExpect] = useState('')
  const [loading, setLoading] = useState(false)
  const [report, setReport] = useState<any>(null)

  useEffect(() => {
    kbApi.list(1, 50).then((r) => {
      const items = r.items ?? []
      setBases(items)
      const hit = items.find((b: any) => String(b.name).includes('制度')) || items[0]
      setKbId(hit?.id ?? null)
    })
    catalogApi.evalList(0).then((r) => setRows(r.items ?? []))
  }, [])

  const load = () => catalogApi.evalList(0).then((r) => setRows(r.items ?? []))

  const add = async () => {
    if (!q.trim()) return message.warning('先写问题')
    await catalogApi.evalAdd(0, q.trim(), expect.trim())
    setQ('')
    setExpect('')
    await load()
    message.success('已加入问题集')
  }

  const run = async () => {
    if (!kbId) return
    setLoading(true)
    try {
      const r: any = await catalogApi.evalRun({ kb_ids: [kbId], top_k: 5 })
      if (r.error) message.error(r.error)
      else setReport(r)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
        每题写「问法 + 应该命中的文档」。改完目录、切片或检索参数就回来跑一遍；命中率掉了说明改坏了。
      </Paragraph>

      <Space wrap>
        <Select
          style={{ width: 220 }}
          value={kbId ?? undefined}
          onChange={setKbId}
          options={bases.map((b) => ({ value: b.id, label: `${b.name}（#${b.id}）` }))}
        />
        <Button
          size="small"
          onClick={() =>
            catalogApi
              .evalSeed()
              .then((r: any) => {
                message.success(`已灌 ${r.added} 道`)
                void load()
              })
              .catch((e: any) => message.error(e.message))
          }
        >
          灌默认 14 道
        </Button>
        <Button type="primary" icon={<PlayCircleOutlined />} loading={loading} onClick={() => void run()}>
          跑一遍
        </Button>
      </Space>

      {report ? (
        <Card size="small">
          <Space size={16} align="center">
            <Text strong>
              命中 {report.hit}/{report.total}
            </Text>
            <Progress
              percent={Math.round((report.rate || 0) * 100)}
              size="small"
              style={{ width: 220 }}
            />
            <Text type="secondary">top5 里有没有出现期望文档</Text>
          </Space>
        </Card>
      ) : null}

      {report ? (
        <Table
          size="small"
          rowKey="id"
          pagination={false}
          dataSource={report.items}
          columns={[
            { title: '问题', dataIndex: 'question' },
            { title: '期望', dataIndex: 'expect', width: 220 },
            {
              title: '结果',
              width: 110,
              render: (_, r: any) =>
                r.hit ? <Tag color="green">命中</Tag> : <Tag color="red">没命中</Tag>,
            },
            {
              title: '实召回',
              render: (_, r: any) => (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {(r.got || []).join('、') || r.error || '—'}
                </Text>
              ),
            },
          ]}
        />
      ) : null}

      <Space wrap>
        <Input
          style={{ width: 320 }}
          placeholder="新问题，例：值班交接要注意什么"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Input
          style={{ width: 240 }}
          placeholder="期望命中的文档名"
          value={expect}
          onChange={(e) => setExpect(e.target.value)}
        />
        <Button icon={<PlusOutlined />} onClick={() => void add()}>
          加入
        </Button>
      </Space>

      <Table
        size="small"
        rowKey="id"
        pagination={{ pageSize: 10 }}
        dataSource={rows}
        columns={[
          { title: '问题', dataIndex: 'question' },
          { title: '期望文档', dataIndex: 'expect_doc', width: 240 },
          {
            title: '操作',
            width: 90,
            render: (_, r: any) => (
              <Popconfirm
                title="删掉这道题？"
                onConfirm={() =>
                  catalogApi
                    .evalRemove(r.id)
                    .then(() => load())
                    .catch((e: any) => message.error(e.message))
                }
              >
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            ),
          },
        ]}
      />

      <Alert
        type="info"
        showIcon
        message="这道题的期望文档名要和库里的文件名一致"
        description="例如 差旅费报销标准.md。命中率只统计 top5 里有没有它。"
      />
    </Space>
  )
}
