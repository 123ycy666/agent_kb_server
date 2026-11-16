import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlayCircleOutlined, PlusOutlined, ReloadOutlined, ScanOutlined, SwapOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { conflictApi, evalApi, kbApi } from '../api'
import type { ConflictItem, EvalItem, EvalRun, EvalSuite, KnowledgeBase } from '../api'
import { useAuth } from '../store/auth'

const { Title, Text, Paragraph } = Typography

const RELATION_LABEL: Record<string, { text: string; color: string }> = {
  contradiction: { text: '真冲突', color: 'red' },
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

const RESOLUTION_OPTIONS = [
  { label: '覆盖旧版', value: 'overwrite' },
  { label: '保留旧版', value: 'keep_old' },
  { label: '并存', value: 'coexist' },
  { label: '忽略', value: 'ignore' },
]

export default function ConflictList() {
  const { has } = useAuth()
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [kbId, setKbId] = useState<number | undefined>()
  const [status, setStatus] = useState<string>('pending')
  const [items, setItems] = useState<ConflictItem[]>([])
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [choices, setChoices] = useState<Record<number, string>>({})
  const [scanning, setScanning] = useState(false)

  const doFullScan = async () => {
    if (!kbId) {
      message.warning('请先选择要扫描的知识库')
      return
    }
    setScanning(true)
    try {
      const res = await conflictApi.fullScan(kbId, true)
      if (res.total_conflicts > 0) {
        message.warning(res.message)
      } else {
        message.success(res.message)
      }
      load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setScanning(false)
    }
  }

  const load = async () => {
    setLoading(true)
    try {
      const [list, st, k] = await Promise.allSettled([
        conflictApi.list({ kb_id: kbId, status: status || undefined, page_size: 100 }),
        conflictApi.stats(kbId),
        kbApi.list(1, 100),
      ])
      if (list.status === 'fulfilled') {
        setItems(list.value.items)
        const init: Record<number, string> = {}
        list.value.items.forEach((c) => {
          init[c.id] = 'overwrite'
        })
        setChoices(init)
      }
      if (st.status === 'fulfilled') setStats(st.value)
      if (k.status === 'fulfilled') setKbs(k.value.items)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [kbId, status])

  const resolve = async (c: ConflictItem) => {
    try {
      await conflictApi.resolve(c.id, choices[c.id] ?? 'overwrite')
      message.success('已处理')
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const columns: ColumnsType<ConflictItem> = [
    {
      title: '关系',
      dataIndex: 'relation',
      width: 100,
      render: (r: string) => {
        const cfg = RELATION_LABEL[r] ?? { text: r, color: 'default' }
        return <Tag color={cfg.color}>{cfg.text}</Tag>
      },
    },
    {
      title: '类型',
      dataIndex: 'conflict_type',
      width: 90,
      render: (t: string) => <Tag>{TYPE_LABEL[t] ?? t}</Tag>,
    },
    {
      title: '冲突文件',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Text style={{ fontSize: 12 }}>
            新：<Text strong>{row.new_doc_name ?? `#${row.new_doc_id}`}</Text>
          </Text>
          <Text style={{ fontSize: 12 }}>
            旧：<Text strong>{row.old_doc_name ?? `#${row.old_doc_id}`}</Text>
            {row.old_doc_version ? ` (v${row.old_doc_version})` : ''}
          </Text>
        </Space>
      ),
    },
    {
      title: '判定理由',
      dataIndex: 'reason',
      render: (r: string, row) => (
        <Space direction="vertical" size={4} style={{ maxWidth: 420 }}>
          <Text style={{ fontSize: 12 }}>{r}</Text>
          {row.new_snippet && (
            <div className="kb-snippet-new" style={{ fontSize: 11, padding: '4px 8px' }}>
              新：{row.new_snippet.slice(0, 120)}
            </div>
          )}
          {row.old_snippet && (
            <div className="kb-snippet-old" style={{ fontSize: 11, padding: '4px 8px' }}>
              旧：{row.old_snippet.slice(0, 120)}
            </div>
          )}
        </Space>
      ),
    },
    {
      title: '相似度',
      dataIndex: 'similarity_score',
      width: 90,
      render: (s: number) => (s != null ? Number(s).toFixed(3) : '—'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => (
        <Tag color={s === 'pending' ? 'warning' : s === 'resolved' ? 'success' : 'default'}>
          {s === 'pending' ? '待处理' : s === 'resolved' ? '已处理' : '已忽略'}
        </Tag>
      ),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 150,
      render: (t: string) => (
        <Text style={{ fontSize: 12 }}>{dayjs(t).format('YYYY-MM-DD HH:mm')}</Text>
      ),
    },
    {
      title: '处理',
      width: 240,
      render: (_, row) =>
        row.status !== 'pending' ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {row.resolution ?? '—'}
          </Text>
        ) : has('conflict:resolve') ? (
          <Space direction="vertical" size={4}>
            <Radio.Group
              size="small"
              value={choices[row.id]}
              onChange={(e) => setChoices((s) => ({ ...s, [row.id]: e.target.value }))}
            >
              {RESOLUTION_OPTIONS.map((o) => (
                <Radio.Button key={o.value} value={o.value} style={{ fontSize: 12 }}>
                  {o.label}
                </Radio.Button>
              ))}
            </Radio.Group>
            <Button type="primary" size="small" onClick={() => resolve(row)}>
              确认处理
            </Button>
          </Space>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>
            无处理权限
          </Text>
        ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Title level={4} style={{ margin: 0 }}>
          冲突管理
        </Title>
        <Space>
          <Select
            allowClear
            placeholder="全部知识库"
            style={{ width: 200 }}
            value={kbId}
            onChange={setKbId}
            options={kbs.map((k) => ({ label: k.name, value: k.id }))}
          />
          <Select
            style={{ width: 130 }}
            value={status}
            onChange={setStatus}
            options={[
              { label: '待处理', value: 'pending' },
              { label: '已处理', value: 'resolved' },
              { label: '已忽略', value: 'ignored' },
              { label: '全部', value: '' },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
          {has('system:scan') && (
            <Popconfirm
              title="执行全库一致性扫描？"
              description={
                <span>
                  将检查该知识库内<b>全部文档（含你不可见的文档）</b>，
                  用于补上「受限文档与公开文档冲突却无人发现」的盲区。
                  会消耗模型调用，操作将记入审计日志。
                </span>
              }
              okText="开始扫描"
              cancelText="取消"
              onConfirm={doFullScan}
            >
              <Button icon={<ScanOutlined />} loading={scanning} disabled={!kbId}>
                全库扫描
              </Button>
            </Popconfirm>
          )}
        </Space>
      </Space>

      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title="冲突总数" value={stats?.total ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="待处理"
              value={stats?.pending ?? 0}
              valueStyle={{ color: (stats?.pending ?? 0) > 0 ? '#cf1322' : undefined }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已处理" value={stats?.resolved ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已忽略" value={stats?.ignored ?? 0} />
          </Card>
        </Col>
      </Row>

      <Alert
        type="info"
        showIcon
        message="关于冲突检测的权限边界"
        description={
          <Paragraph style={{ marginBottom: 0, fontSize: 12 }}>
            检测范围仅包含<b>你有权访问的文档</b>。受限文档完全不参与比对与提示，
            避免通过上传文件探测到无权查看的内容。
            管理员可另行发起「全库一致性扫描」覆盖全部文档（操作会记入审计日志）。
          </Paragraph>
        }
      />

      <Table
        rowKey="id"
        columns={columns}
        dataSource={items}
        loading={loading}
        locale={{ emptyText: <Empty description="暂无冲突记录" /> }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        scroll={{ x: 1200 }}
      />
    </Space>
  )
}

function parseQuestions(text: string): unknown[] {
  const raw = text.trim()
  if (!raw) return []
  try {
    const data = JSON.parse(raw)
    if (Array.isArray(data)) return data
  } catch {
    /* 不是 JSON，按行解析 */
  }
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [cls, q, expect] = line.split('|').map((x) => x.trim())
      const item: Record<string, unknown> = { cls: cls || '未分类', q: q || cls }
      if (expect) item.expect = expect.split(';').map((x) => x.trim()).filter(Boolean)
      return item
    })
}

function ImportSuiteModal({
  open,
  classes,
  onClose,
  onDone,
}: {
  open: boolean
  classes: string[]
  onClose: () => void
  onDone: () => Promise<void>
}) {
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      setName('')
      setNote('')
      setText('')
    }
  }, [open])

  const parsePreview = parseQuestions(text)

  const submit = async () => {
    if (!name.trim()) {
      message.warning('请填题集名称')
      return
    }
    if (parsePreview.length === 0) {
      message.warning('至少导入一道题')
      return
    }
    setBusy(true)
    try {
      const r = await evalApi.createSuite({ name: name.trim(), note, questions: parsePreview })
      message.success(`已导入题集「${name}」，共 ${r.n} 题`)
      await onDone()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      title="导入题集"
      width={780}
      onCancel={onClose}
      onOk={() => void submit()}
      okText="导入"
      confirmLoading={busy}
      destroyOnClose
    >
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="题集按 10 类：闲聊 / 写通知 / 速写 / 纠错 / 汇报 / 台账 / 查数 / 接力 / 未命中 / 归属"
          description={
            <div style={{ fontSize: 12 }}>
              每行一题：类别 | 问题 | 期望词（可选，分号分隔）。也可以粘贴 JSON 数组：
              {' '}
              <Text code>[{'{'}&quot;cls&quot;:&quot;查数&quot;,&quot;q&quot;:&quot;今年工单数&quot;{'}'}]</Text>
            </div>
          }
        />
        <Space wrap>
          {classes.map((c) => (
            <Tag key={c}>{c}</Tag>
          ))}
        </Space>
        <Space wrap>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="题集名称"
            style={{ width: 300 }}
          />
          <Input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="备注（可选）"
            style={{ width: 360 }}
          />
        </Space>
        <Input.TextArea
          rows={12}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={'查数|今年各供电所工单数是多少|件数\n写通知|写一份迎峰度夏通知|通知'}
          style={{ fontSize: 12 }}
        />
        <Space wrap>
          <Text type="secondary">已识别 {parsePreview.length} 题</Text>
          <Button
            size="small"
            onClick={() =>
              setText(
                classes
                  .map((c) => `${c}|（把这一类的问题写在这里）|`)
                  .join('\n')
              )
            }
          >
            按 10 类生成模板
          </Button>
        </Space>
      </Space>
    </Modal>
  )
}

function RunSuiteModal({
  suite,
  onClose,
  onDone,
}: {
  suite: EvalSuite | null
  onClose: () => void
  onDone: () => Promise<void>
}) {
  const [tag, setTag] = useState('')
  const [limit, setLimit] = useState<number>(0)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)

  useEffect(() => {
    if (suite) {
      setTag(dayjs().format('MM-DD HH:mm'))
      setLimit(0)
      setResult(null)
    }
  }, [suite])

  const run = async () => {
    if (!suite) return
    setBusy(true)
    setResult(null)
    try {
      const r: any = await evalApi.run({ suite_id: suite.id, tag, limit })
      if (r?.error) message.error(r.error)
      else {
        setResult(r)
        message.success(`跑完：${r.passed}/${r.total}，${r.score} 分`)
        await onDone()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={!!suite}
      title={suite ? `跑分：${suite.name}` : '跑分'}
      width={640}
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>关闭</Button>
          <Button type="primary" icon={<PlayCircleOutlined />} loading={busy} onClick={() => void run()}>
            开始跑
          </Button>
        </Space>
      }
      destroyOnClose
    >
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Alert
          type="warning"
          showIcon
          message="跑分同步执行"
          description="题目多时耗时长，可先用 limit 只跑前几题；正式跑分建议在空闲时段。"
        />
        <Space wrap>
          <div>
            <div style={{ marginBottom: 4 }}>标签</div>
            <Input value={tag} onChange={(e) => setTag(e.target.value)} style={{ width: 240 }} />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>只跑前几题（0 = 全部）</div>
            <InputNumber min={0} value={limit} onChange={(v) => setLimit(Number(v || 0))} style={{ width: 160 }} />
          </div>
        </Space>
        {result ? (
          <Card size="small" title="本次结果">
            <Space size={16}>
              <Statistic title="通过" value={result.passed} suffix={`/ ${result.total}`} />
              <Statistic title="得分" value={result.score} suffix="分" />
              <Statistic title="运行 id" value={result.run_id} />
            </Space>
          </Card>
        ) : null}
      </Space>
    </Modal>
  )
}

function RunDetailDrawer({
  runId,
  onClose,
}: {
  runId: number | null
  onClose: () => void
}) {
  const [data, setData] = useState<{ run: EvalRun; items: EvalItem[] } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!runId) {
      setData(null)
      return
    }
    setBusy(true)
    evalApi
      .runDetail(runId)
      .then(setData)
      .catch((e) => message.error((e as Error).message))
      .finally(() => setBusy(false))
  }, [runId])

  return (
    <Drawer open={runId != null} width={900} title="逐题结果" onClose={onClose}>
      {busy ? (
        <div style={{ padding: 40, textAlign: 'center' }}>加载中…</div>
      ) : data ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Descriptions size="small" column={4} bordered>
            <Descriptions.Item label="得分">{data.run.score}</Descriptions.Item>
            <Descriptions.Item label="通过">{`${data.run.passed}/${data.run.total}`}</Descriptions.Item>
            <Descriptions.Item label="标签">{data.run.tag || '—'}</Descriptions.Item>
            <Descriptions.Item label="时间">
              {data.run.created_at ? dayjs(data.run.created_at).format('YYYY-MM-DD HH:mm') : '—'}
            </Descriptions.Item>
          </Descriptions>
          <Table
            rowKey="seq"
            size="small"
            dataSource={data.items}
            pagination={{ pageSize: 20, showSizeChanger: false }}
            columns={[
              { title: '#', dataIndex: 'seq', width: 50 },
              { title: '类别', dataIndex: 'cls', width: 90, render: (v) => <Tag>{v || '未分类'}</Tag> },
              { title: '问题', dataIndex: 'question', width: 280 },
              {
                title: '结果',
                dataIndex: 'passed',
                width: 80,
                render: (v) => <Tag color={v ? 'green' : 'red'}>{v ? '通过' : '不通过'}</Tag>,
              },
              { title: '判据', dataIndex: 'verdict', width: 180 },
              { title: '耗时', dataIndex: 'secs', width: 80, render: (v) => `${v}s` },
              {
                title: '答案',
                render: (_, r) => (
                  <Typography.Paragraph
                    ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}
                    style={{ marginBottom: 0, fontSize: 12, maxWidth: 420 }}
                  >
                    {r.answer}
                  </Typography.Paragraph>
                ),
              },
            ]}
          />
        </Space>
      ) : null}
    </Drawer>
  )
}

function ComparePanel({ runs }: { runs: EvalRun[] }) {
  const [a, setA] = useState<number | undefined>()
  const [b, setB] = useState<number | undefined>()
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    if (!a || !b) {
      message.warning('请选两次跑分')
      return
    }
    setBusy(true)
    try {
      setData(await evalApi.compare(a, b))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="改造前后对比"
        description="选两次跑分，看总分、通过题数和哪几道题发生了变化。"
      />
      <Card size="small">
        <Space wrap>
          <Select
            placeholder="A（改造前）"
            style={{ width: 280 }}
            value={a}
            onChange={setA}
            options={runs.map((r) => ({
              value: r.id,
              label: `#${r.id} · ${r.tag || '未命名'} · ${r.score} 分`,
            }))}
          />
          <Select
            placeholder="B（改造后）"
            style={{ width: 280 }}
            value={b}
            onChange={setB}
            options={runs.map((r) => ({
              value: r.id,
              label: `#${r.id} · ${r.tag || '未命名'} · ${r.score} 分`,
            }))}
          />
          <Button type="primary" icon={<SwapOutlined />} loading={busy} onClick={() => void run()}>
            对比
          </Button>
        </Space>
      </Card>
      {data ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Row gutter={12}>
            <Col span={6}>
              <Card size="small">
                <Statistic title="A 得分" value={data.a?.score ?? 0} suffix="分" />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <Statistic title="B 得分" value={data.b?.score ?? 0} suffix="分" />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <Statistic title="变好" value={data.gained} suffix="题" valueStyle={{ color: '#389e0d' }} />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <Statistic title="变差" value={data.lost} suffix="题" valueStyle={{ color: '#cf1322' }} />
              </Card>
            </Col>
          </Row>
          <Card size="small" title="逐题变化">
            <Table
              rowKey="question"
              size="small"
              dataSource={(data.rows ?? []).filter((x: any) => x.changed)}
              pagination={{ pageSize: 12, showSizeChanger: false }}
              columns={[
                { title: '问题', dataIndex: 'question' },
                {
                  title: 'A',
                  dataIndex: 'a_passed',
                  width: 100,
                  render: (v) => <Tag color={v ? 'green' : 'red'}>{v ? '通过' : '不通过'}</Tag>,
                },
                {
                  title: 'B',
                  dataIndex: 'b_passed',
                  width: 100,
                  render: (v) => <Tag color={v ? 'green' : 'red'}>{v ? '通过' : '不通过'}</Tag>,
                },
                { title: 'A 判据', dataIndex: 'a_verdict', width: 200 },
                { title: 'B 判据', dataIndex: 'b_verdict', width: 200 },
              ]}
            />
          </Card>
        </Space>
      ) : null}
    </Space>
  )
}

export function EvalPage() {
  const [tab, setTab] = useState('suites')
  const [classes, setClasses] = useState<string[]>([])
  const [suites, setSuites] = useState<EvalSuite[]>([])
  const [runs, setRuns] = useState<EvalRun[]>([])
  const [loading, setLoading] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [runSuite, setRunSuite] = useState<EvalSuite | null>(null)
  const [detailRun, setDetailRun] = useState<number | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const [c, s, r] = await Promise.all([evalApi.classes(), evalApi.suites(), evalApi.runs()])
      setClasses(c.items ?? [])
      setSuites(s.items ?? [])
      setRuns(r.items ?? [])
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
        <div>
          <Title level={4} style={{ margin: 0 }}>
            评测
          </Title>
          <Text type="secondary">
            题集、跑分、逐题结果与两次对比；结果落库，可回看每一题。
          </Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setImportOpen(true)}>
            导入题集
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="跑分是同步任务"
        description="题目多时耗时较长；可以先用 limit 跑前几题验证判据。逐题结果里能看到每题的判据结论和答案。"
      />

      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'suites',
            label: `题集（${suites.length}）`,
            children: (
              <Card size="small" title="题集列表">
                <Table
                  rowKey="id"
                  size="small"
                  loading={loading}
                  dataSource={suites}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  locale={{ emptyText: <Empty description="还没有题集，点右上角「导入题集」" /> }}
                  columns={[
                    { title: 'ID', dataIndex: 'id', width: 70 },
                    { title: '名称', dataIndex: 'name' },
                    { title: '题数', dataIndex: 'n', width: 80 },
                    { title: '备注', dataIndex: 'note' },
                    {
                      title: '导入时间',
                      dataIndex: 'created_at',
                      width: 150,
                      render: (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '—'),
                    },
                    {
                      title: '操作',
                      width: 110,
                      render: (_, r) => (
                        <Button
                          size="small"
                          type="primary"
                          icon={<PlayCircleOutlined />}
                          onClick={() => setRunSuite(r)}
                        >
                          跑分
                        </Button>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: 'runs',
            label: `跑分历史（${runs.length}）`,
            children: (
              <Card size="small" title="跑分记录">
                <Table
                  rowKey="id"
                  size="small"
                  loading={loading}
                  dataSource={runs}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  locale={{ emptyText: <Empty description="还没有跑分" /> }}
                  columns={[
                    { title: 'ID', dataIndex: 'id', width: 70 },
                    { title: '标签', dataIndex: 'tag' },
                    { title: '题集', dataIndex: 'suite_id', width: 80 },
                    {
                      title: '得分',
                      dataIndex: 'score',
                      width: 90,
                      render: (v) => <Text strong>{v} 分</Text>,
                    },
                    { title: '通过', width: 100, render: (_, r) => `${r.passed}/${r.total}` },
                    {
                      title: '时间',
                      dataIndex: 'created_at',
                      width: 150,
                      render: (v) => (v ? dayjs(v).format('MM-DD HH:mm') : '—'),
                    },
                    {
                      title: '操作',
                      width: 90,
                      render: (_, r) => (
                        <Button size="small" onClick={() => setDetailRun(r.id)}>
                          逐题
                        </Button>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: 'compare',
            label: '两次对比',
            children: <ComparePanel runs={runs} />,
          },
        ]}
      />

      <ImportSuiteModal
        open={importOpen}
        classes={classes}
        onClose={() => setImportOpen(false)}
        onDone={async () => {
          setImportOpen(false)
          await load()
        }}
      />
      <RunSuiteModal
        suite={runSuite}
        onClose={() => setRunSuite(null)}
        onDone={async () => {
          await load()
        }}
      />
      <RunDetailDrawer runId={detailRun} onClose={() => setDetailRun(null)} />
    </Space>
  )
}
