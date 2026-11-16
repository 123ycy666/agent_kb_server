import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  List,
  Row,
  Space,
  Statistic,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  BarChartOutlined,
  BookOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  HistoryOutlined,
  HomeOutlined,
  MessageOutlined,
  ReloadOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { chatHistoryApi, evalApi, injectApi, kbApi, specsApi, systemApi } from '../api'
import type { EvalRun, InjectItem, SpecListResult } from '../api'

const { Title, Text, Paragraph } = Typography

const PAGES = [
  { path: '/home', label: '首页', desc: '今日概览', icon: <HomeOutlined /> },
  { path: '/chat', label: '智能问答', desc: '四件套 · 产物', icon: <MessageOutlined /> },
  { path: '/specs', label: '知识库', desc: '说明书 · 资料 · 冲突', icon: <BookOutlined /> },
  { path: '/ask-data', label: '问数', desc: '只影响问数线', icon: <BarChartOutlined /> },
  { path: '/inject', label: '提示词与注入', desc: '字数 · 预算 · 预览', icon: <ThunderboltOutlined /> },
  { path: '/tasks', label: '任务与预警', desc: '只读展示', icon: <DeploymentUnitOutlined /> },
  { path: '/records', label: '记录', desc: '会话 · 四件套 · 审计', icon: <HistoryOutlined /> },
  { path: '/eval', label: '评测', desc: '题集 · 跑分 · 对比', icon: <ExperimentOutlined /> },
  { path: '/system', label: '系统', desc: '三态注册表 · 入口判据', icon: <SettingOutlined /> },
]

export default function Overview() {
  const [sessions, setSessions] = useState(0)
  const [specs, setSpecs] = useState<SpecListResult | null>(null)
  const [injectItems, setInjectItems] = useState<InjectItem[]>([])
  const [injectTotal, setInjectTotal] = useState(0)
  const [injectBudget, setInjectBudget] = useState(6000)
  const [runs, setRuns] = useState<EvalRun[]>([])
  const [reports, setReports] = useState<any[]>([])
  const [kbDocs, setKbDocs] = useState(0)
  const [health, setHealth] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [sess, sp, inj, prev, ev, rep, kb, h] = await Promise.allSettled([
        chatHistoryApi.sessions({ limit: 100 }),
        specsApi.list(),
        injectApi.items(),
        injectApi.preview({ question: '' }),
        evalApi.runs(),
        injectApi.report({ limit: 5 }),
        kbApi.list(1, 100),
        systemApi.health(),
      ])
      if (sess.status === 'fulfilled') setSessions(sess.value.total ?? sess.value.sessions.length)
      if (sp.status === 'fulfilled') setSpecs(sp.value)
      if (inj.status === 'fulfilled') {
        setInjectItems(inj.value.items)
        setInjectBudget(inj.value.budget)
      }
      if (prev.status === 'fulfilled') setInjectTotal(prev.value.total)
      if (ev.status === 'fulfilled') setRuns(ev.value.items)
      if (rep.status === 'fulfilled') setReports(rep.value.items ?? [])
      if (kb.status === 'fulfilled') {
        setKbDocs(kb.value.items.reduce((sum, x) => sum + x.document_count, 0))
      }
      if (h.status === 'fulfilled') setHealth(h.value)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const materialCount = (specs?.pending ?? []).filter((x) => x.space === 'material').length
  const pendingCount = (specs?.pending ?? []).filter((x) => x.space === 'spec').length
  const latestRun = runs[0]
  const injectChars = injectItems.reduce((sum, x) => sum + x.chars, 0)

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
        <div>
          <Title level={4} style={{ margin: 0 }}>
            今日概览
          </Title>
          <Text type="secondary">
            {dayjs().format('YYYY-MM-DD')} · 会话、说明书、注入、跑分一屏看完
          </Text>
        </div>
        <Space>
          <Tag color={health?.database?.ok ? 'success' : 'error'}>
            {health?.database?.ok ? '服务正常' : '服务异常'}
          </Tag>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
        </Space>
      </Space>

      {!health?.database?.ok && health ? (
        <Alert type="error" showIcon message="数据库连接异常" description={health?.database?.detail} />
      ) : null}

      <Row gutter={12}>
        <Col span={5}>
          <Card size="small">
            <Statistic title="会话数" value={sessions} prefix={<MessageOutlined />} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              你自己的问答会话
            </Text>
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small">
            <Statistic
              title="知识库篇数"
              value={(specs?.count ?? 0) + materialCount}
              prefix={<BookOutlined />}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              说明书 {specs?.count ?? 0} · 资料 {materialCount} · 文档 {kbDocs}
            </Text>
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small">
            <Statistic title="注入字数" value={injectTotal || injectChars} suffix="字" />
            <Text type="secondary" style={{ fontSize: 12 }}>
              常驻预算 {injectBudget} 字 · {injectItems.length} 项
            </Text>
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small">
            <Statistic
              title="跑分最近一次"
              value={latestRun ? latestRun.score : '—'}
              suffix={latestRun ? '分' : ''}
              prefix={<ExperimentOutlined />}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {latestRun ? `${latestRun.passed}/${latestRun.total} 通过` : '还没有跑分'}
            </Text>
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic
              title="待发布"
              value={pendingCount}
              valueStyle={{ color: pendingCount > 0 ? '#d46b08' : undefined }}
              prefix={<FileTextOutlined />}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              四项填全即可发布
            </Text>
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={15}>
          <Card
            size="small"
            title="最近记录"
            extra={<Link to="/records">全部记录</Link>}
            styles={{ body: { padding: 0 } }}
          >
            {reports.length === 0 ? (
              <Empty description="还没有带四件套的问答记录" style={{ padding: 32 }} />
            ) : (
              <List
                size="small"
                dataSource={reports}
                renderItem={(r) => (
                  <List.Item style={{ padding: '10px 16px' }}>
                    <div style={{ width: '100%' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                        <Text strong ellipsis style={{ maxWidth: 620 }}>
                          {r.raw_question || '（无问题文本）'}
                        </Text>
                        <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                          {r.create_time ? dayjs(r.create_time).format('MM-DD HH:mm') : '—'}
                        </Text>
                      </div>
                      <Space size={4} wrap style={{ marginTop: 4 }}>
                        {r.spec_used?.name ? (
                          <Tag color="blue">《{r.spec_used.name}》</Tag>
                        ) : (
                          <Tag>未命中说明书</Tag>
                        )}
                        <Tag color={r.self_check?.ok ? 'green' : 'red'}>
                          自检 {r.self_check?.ok ? '通过' : '不通过'}
                        </Tag>
                        <Tag>{r.number_sources?.length ?? 0} 个数字来源</Tag>
                      </Space>
                    </div>
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>
        <Col span={9}>
          <Card size="small" title="入口">
            <Row gutter={[8, 8]}>
              {PAGES.map((p) => (
                <Col span={8} key={p.path}>
                  <Link to={p.path}>
                    <Card size="small" hoverable styles={{ body: { padding: 10 } }}>
                      <Space size={6}>
                        <span style={{ color: '#1677ff' }}>{p.icon}</span>
                        <Text strong style={{ fontSize: 13 }}>
                          {p.label}
                        </Text>
                      </Space>
                      <div>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {p.desc}
                        </Text>
                      </div>
                    </Card>
                  </Link>
                </Col>
              ))}
            </Row>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="今日提醒">
        <Space direction="vertical" size={4}>
          <Paragraph style={{ margin: 0, fontSize: 12 }}>
            · 说明书发布后 60 秒内进目录；未填全四项不能发布。
          </Paragraph>
          <Paragraph style={{ margin: 0, fontSize: 12 }}>
            · 问答页右侧常显四件套：用了哪篇说明书 / 自检结果 / 数字来源 / 答案。
          </Paragraph>
          <Paragraph style={{ margin: 0, fontSize: 12 }}>
            · 本页之外，注入改动只在「提示词与注入」；问数规则只在「问数」。
          </Paragraph>
        </Space>
      </Card>
    </Space>
  )
}
