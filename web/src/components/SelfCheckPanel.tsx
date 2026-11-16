import { Alert, Card, Col, Collapse, Descriptions, Empty, Row, Space, Tag, Typography } from 'antd'
import { CheckCircleFilled, CloseCircleFilled } from '@ant-design/icons'

const { Text, Paragraph } = Typography

type Verdict = { item: string; ok: boolean; evidence?: string }

export interface SelfCheck {
  ok?: boolean
  required?: string[]
  failed?: (Verdict | string)[]
  verdict?: string
  how?: string
  default?: string[]
  missing?: string[]
  verdicts?: Verdict[]
  items?: Verdict[]
}

function normFailed(v: (Verdict | string)[] | undefined): Verdict[] {
  return (v ?? []).map((x) =>
    typeof x === 'string' ? { item: x, ok: false, evidence: '' } : { ...x, ok: false }
  )
}

export default function SelfCheckPanel({
  selfCheck,
  verdicts,
  compact = false,
}: {
  selfCheck?: SelfCheck | null
  verdicts?: Verdict[]
  compact?: boolean
}) {
  if (!selfCheck || Object.keys(selfCheck).length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮没有自检结果" />
  }
  const required = selfCheck.required ?? []
  const merged = [
    ...normFailed(selfCheck.failed),
    ...(selfCheck.verdicts ?? []),
    ...(selfCheck.items ?? []),
    ...(verdicts ?? []),
  ]
  const failed = merged.filter((x) => !x.ok)
  const seen = new Set<string>()
  const failedUniq = failed.filter((x) => {
    const key = `${x.item}|${x.evidence ?? ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  const ok = selfCheck.ok === true && failedUniq.length === 0
  const noCheck = required.length === 0 && !selfCheck.verdict?.includes('全通过')

  return (
    <Space direction="vertical" size={compact ? 4 : 8} style={{ width: '100%' }}>
      <Alert
        type={ok ? 'success' : 'error'}
        showIcon
        icon={ok ? <CheckCircleFilled /> : <CloseCircleFilled />}
        message={
          <Space wrap size={6}>
            <Text strong>{ok ? '自检通过' : '自检不通过'}</Text>
            <Tag color={ok ? 'green' : 'red'}>{selfCheck.verdict || (ok ? '全通过' : '有待整改')}</Tag>
            {noCheck ? <Tag>无检查项</Tag> : <Tag>{required.length} 项检查</Tag>}
          </Space>
        }
        description={
          selfCheck.how && !ok ? (
            <Text style={{ fontSize: 12 }}>{selfCheck.how}</Text>
          ) : undefined
        }
      />
      {required.length > 0 ? (
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            检查项：
          </Text>
          <Space wrap size={4} style={{ marginTop: 4 }}>
            {required.map((x) => (
              <Tag key={x} color={failedUniq.some((f) => f.item === x) ? 'red' : 'green'}>
                {x}
              </Tag>
            ))}
          </Space>
        </div>
      ) : null}
      {failedUniq.length > 0 ? (
        <div>
          <Text type="danger" style={{ fontSize: 12 }}>
            不通过项与依据：
          </Text>
          {failedUniq.map((x, i) => (
            <div
              key={`${x.item}-${i}`}
              style={{ marginTop: 4, padding: '6px 8px', background: '#fff2f0', borderRadius: 4 }}
            >
              <Text strong style={{ fontSize: 12 }}>
                {x.item || '未命名检查项'}
              </Text>
              <div style={{ fontSize: 12, color: 'rgba(0,0,0,.65)' }}>
                依据：{x.evidence || '未给依据'}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </Space>
  )
}

export interface FourPackData {
  raw_question?: string
  answer?: string
  spec_used?: {
    name?: string
    version_no?: number
    check_items?: string[]
  } | null
  self_check?: SelfCheck | null
  number_sources?: any[]
  injection?: any
  artifacts?: { name?: string; kind?: string; self_check?: string; url?: string }[]
}

function SourceList({ items }: { items: any[] }) {
  if (!items || items.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮没有数字来源" />
  }
  return (
    <Collapse
      size="small"
      items={items.map((x, i) => ({
        key: String(i),
        label: (
          <Space wrap size={6}>
            <Tag color="geekblue">来源 {i + 1}</Tag>
            {x?.rows != null ? <Tag>{x.rows} 行</Tag> : null}
            {x?.step != null ? <Tag>步骤 {x.step}</Tag> : null}
            <Text type="secondary" style={{ fontSize: 12 }}>
              点开看 SQL
            </Text>
          </Space>
        ),
        children: (
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: 12, margin: 0 }}>
            {typeof x === 'string' ? x : x?.sql || JSON.stringify(x, null, 2)}
          </pre>
        ),
      }))}
    />
  )
}

export function FourPack({ data, compact = false }: { data: FourPackData; compact?: boolean }) {
  const spec = data.spec_used ?? {}
  const sources = data.number_sources ?? []
  const artifacts = data.artifacts ?? []
  if (compact) {
    return (
      <Descriptions
        size="small"
        bordered
        column={1}
        labelStyle={{ whiteSpace: 'nowrap', width: 78 }}
        contentStyle={{ minWidth: 0 }}
        items={[
          {
            key: 'spec',
            label: <span style={{ whiteSpace: 'nowrap' }}>① 说明书</span>,
            children: spec.name ? (
              <Space wrap size={4}>
                <Text strong>《{spec.name}》</Text>
                <Tag color="blue">v{spec.version_no ?? 1}</Tag>
                {spec.check_items?.length ? <Tag>{spec.check_items.length} 项检查</Tag> : null}
              </Space>
            ) : (
              <Text type="secondary">本轮未命中说明书</Text>
            ),
          },
          {
            key: 'check',
            label: <span style={{ whiteSpace: 'nowrap' }}>② 自检</span>,
            children: <SelfCheckPanel selfCheck={data.self_check} compact />,
          },
          {
            key: 'sources',
            label: <span style={{ whiteSpace: 'nowrap' }}>③ 数字来源</span>,
            children:
              sources.length > 0 ? (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <SourceList items={sources.slice(0, 3)} />
                  {sources.length > 3 ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      另有 {sources.length - 3} 个来源，到「记录」页展开看全部
                    </Text>
                  ) : null}
                </Space>
              ) : (
                <Text type="secondary">本轮没有数字来源</Text>
              ),
          },
          {
            key: 'answer',
            label: <span style={{ whiteSpace: 'nowrap' }}>④ 答案</span>,
            children: data.answer ? (
              <Paragraph
                ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}
                style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
              >
                {data.answer}
              </Paragraph>
            ) : (
              <Text type="secondary">这条记录只存四件套，答案看会话回放</Text>
            ),
          },
        ]}
      />
    )
  }
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Row gutter={12}>
        <Col span={12}>
          <Card size="small" title="① 用了哪篇说明书" styles={{ body: { minHeight: 104 } }}>
            {spec.name ? (
              <Space direction="vertical" size={4}>
                <Text strong>《{spec.name}》</Text>
                <Space wrap size={4}>
                  <Tag color="blue">v{spec.version_no ?? 1}</Tag>
                  {spec.check_items?.length ? <Tag>{spec.check_items.length} 项检查</Tag> : null}
                </Space>
                {spec.check_items?.length ? (
                  <Space wrap size={4}>
                    {spec.check_items.slice(0, 6).map((x) => (
                      <Tag key={x}>{x}</Tag>
                    ))}
                  </Space>
                ) : null}
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮未命中说明书" />
            )}
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="② 自检结果" styles={{ body: { minHeight: 104 } }}>
            <SelfCheckPanel selfCheck={data.self_check} compact />
          </Card>
        </Col>
      </Row>

      <Card size="small" title={`③ 数字来源（${sources.length}）`}>
        <SourceList items={sources} />
      </Card>

      <Card size="small" title="④ 答案">
        {data.answer ? (
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
            {data.answer}
          </Typography.Paragraph>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这条记录只存四件套，答案看会话回放" />
        )}
        {artifacts.length > 0 ? (
          <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
            {artifacts.map((a, i) => (
              <Descriptions.Item key={`${a.name}-${i}`} label={`产物 ${i + 1}`}>
                <Space size={6} wrap>
                  <Text>{a.name}</Text>
                  <Tag>{a.kind || '文件'}</Tag>
                  {a.self_check ? <Tag color="green">{a.self_check}</Tag> : null}
                </Space>
              </Descriptions.Item>
            ))}
          </Descriptions>
        ) : null}
      </Card>

      {data.injection?.total != null ? (
        <Collapse
          size="small"
          items={[
            {
              key: 'injection',
              label: `本轮注入构成（${data.injection.total} 字 / 预算 ${data.injection.budget ?? '—'} 字）`,
              children: (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  {(data.injection.parts ?? []).map((p: any) => (
                    <Space key={p.name} style={{ width: '100%', justifyContent: 'space-between' }}>
                      <Text>{p.label || p.name}</Text>
                      <Text type="secondary">
                        {p.chars} 字 {p.injected ? '' : '（未注入）'}
                      </Text>
                    </Space>
                  ))}
                  {data.injection.compressed?.length ? (
                    <Text type="warning">
                      压缩：{data.injection.compressed.map((x: any) => x.why).join('；')}
                    </Text>
                  ) : null}
                </Space>
              ),
            },
          ]}
        />
      ) : null}
    </Space>
  )
}
