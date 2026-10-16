import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Form,
  Input,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { SearchOutlined, WarningOutlined } from '@ant-design/icons'
import { kbApi, searchApi, tagApi } from '../api'
import type { KnowledgeBase, SearchResponse, SearchResult } from '../api'

const { Title, Text, Paragraph } = Typography

export default function SearchTest() {
  const [form] = Form.useForm()
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [tagOptions, setTagOptions] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [resp, setResp] = useState<SearchResponse | null>(null)

  useEffect(() => {
    kbApi
      .list(1, 100)
      .then((r) => setKbs(r.items))
      .catch((e) => message.error((e as Error).message))
  }, [])

  /** 切换知识库时清空已选标签，并刷新「用过的标签」候选。 */
  const onKbChange = async () => {
    form.setFieldValue('tags', undefined)
    try {
      const res = await tagApi.options()
      setTagOptions(res.options ?? [])
    } catch {
      setTagOptions([])
    }
  }

  const onSearch = async () => {
    const values = await form.validateFields()
    setLoading(true)
    setResp(null)
    try {
      const res = await searchApi.search({
        query: values.query,
        kb_ids: values.kb_ids,
        tag_filters: values.tags ?? [],
        top_k: values.top_k ?? 5,
        rerank: values.rerank ?? true,
      })
      setResp(res)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Title level={4} style={{ margin: 0 }}>
        检索测试
      </Title>

      <Card size="small">
        <Form
          form={form}
          layout="vertical"
          initialValues={{ top_k: 5, rerank: true, kb_ids: [] }}
        >
          <Row gutter={12}>
            <Col span={10}>
              <Form.Item
                name="query"
                label="查询问题"
                rules={[{ required: true, message: '请输入查询内容' }]}
              >
                <Input.Search
                  placeholder="如：出差住宿标准是多少"
                  enterButton="检索"
                  onSearch={onSearch}
                  loading={loading}
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                name="kb_ids"
                label="知识库"
                rules={[{ required: true, message: '请选择至少一个知识库' }]}
              >
                <Select
                  mode="multiple"
                  placeholder="选择一个或多个"
                  onChange={onKbChange}
                  options={kbs.map((k) => ({
                    label: `${k.name}（可见 ${k.visible_document_count} 份文档）`,
                    value: k.id,
                  }))}
                />
              </Form.Item>
            </Col>
            <Col span={5}>
              <Form.Item name="tags" label="标签过滤（需全部命中）">
                <Select
                  mode="tags"
                  allowClear
                  placeholder="直接输入标签名，或从下方点选"
                  tokenSeparators={[',', '，']}
                  options={tagOptions.map((o) => ({ label: o.name, value: o.name }))}
                />
              </Form.Item>
            </Col>
            <Col span={3}>
              <Form.Item name="top_k" label="Top K">
                <Select
                  options={[3, 5, 8, 10, 20].map((v) => ({ label: String(v), value: v }))}
                />
              </Form.Item>
            </Col>
          </Row>

          {tagOptions.length > 0 && (
            <>
              <Divider orientation="left" plain style={{ marginTop: 0 }}>
                可用标签（点击填入过滤条件）
              </Divider>
              <Space wrap size={6}>
                {tagOptions.map((o) => (
                  <Tag
                    key={o.name}
                    style={{ cursor: 'pointer' }}
                    color="blue"
                    onClick={() => {
                      const cur: string[] = form.getFieldValue('tags') ?? []
                      if (!cur.includes(o.name)) {
                        form.setFieldValue('tags', [...cur, o.name])
                      }
                    }}
                  >
                    {o.name}
                    <Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                      {o.usage_count}
                    </Text>
                  </Tag>
                ))}
              </Space>
            </>
          )}

          <Space style={{ marginTop: 8 }}>
            <Form.Item name="rerank" label="启用精排" valuePropName="checked" noStyle>
              <Switch size="small" />
            </Form.Item>
            <Text type="secondary" style={{ fontSize: 12 }}>
              关闭精排可对比召回差异；开启后会调用 rerank 模型重排候选
            </Text>
          </Space>
        </Form>
      </Card>

      {loading && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin tip="检索中…" />
        </div>
      )}

      {resp && (
        <>
          {resp.degraded && (
            <Alert type="warning" showIcon message="精排降级" description={resp.degraded_reason} />
          )}

          {resp.filter_notice && (
            <Alert
              type="info"
              showIcon
              message={resp.filter_notice.message}
              description={
                <Space direction="vertical" size={2}>
                  <Text>{resp.filter_notice.suggestion}</Text>
                  {resp.filter_notice.relaxable_filters?.length > 0 && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      可放宽的条件：{resp.filter_notice.relaxable_filters.join('、')}
                    </Text>
                  )}
                </Space>
              }
            />
          )}

          <Card
            size="small"
            title={
              <Space>
                <span>检索结果</span>
                <Tag>命中 {resp.total}</Tag>
                <Tag>召回 {resp.recall_count}</Tag>
                <Tag>耗时 {resp.took_ms} ms</Tag>
              </Space>
            }
          >
            {resp.results.length === 0 ? (
              <Empty description="没有找到相关内容" />
            ) : (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                {resp.results.map((r: SearchResult, i) => (
                  <Card
                    key={r.chunk_id}
                    size="small"
                    type="inner"
                    title={
                      <Space wrap size={6}>
                        <Tag color="blue">#{i + 1}</Tag>
                        <Text strong>{r.doc_name}</Text>
                        {r.version_no > 1 && <Tag color="purple">v{r.version_no}</Tag>}
                        {r.kb_name && <Tag>{r.kb_name}</Tag>}
                        {r.has_conflict && (
                          <Tooltip title="该来源存在未处理的冲突，需谨慎采信">
                            <Tag icon={<WarningOutlined />} color="error">
                              存在冲突
                            </Tag>
                          </Tooltip>
                        )}
                      </Space>
                    }
                    extra={
                      <Space size={4}>
                        <Tooltip
                          title={
                            r.source === 'hybrid'
                              ? '向量路与关键词路都召回了这条'
                              : r.source === 'keyword'
                                ? '仅关键词路召回（向量路未命中）'
                                : '仅向量路召回'
                          }
                        >
                          <Tag
                            color={
                              r.source === 'hybrid'
                                ? 'purple'
                                : r.source === 'keyword'
                                  ? 'orange'
                                  : 'blue'
                            }
                          >
                            {r.source === 'hybrid'
                              ? '双路'
                              : r.source === 'keyword'
                                ? '关键词'
                                : '向量'}
                          </Tag>
                        </Tooltip>
                        {r.rerank_score != null && (
                          <Tag color="green">精排 {Number(r.rerank_score).toFixed(3)}</Tag>
                        )}
                        {r.vector_score != null && (
                          <Tag>向量 {Number(r.vector_score).toFixed(3)}</Tag>
                        )}
                        {r.keyword_score != null && (
                          <Tag>关键词 {Number(r.keyword_score).toFixed(2)}</Tag>
                        )}
                      </Space>
                    }
                  >
                    {r.heading_path && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        章节：{r.heading_path}
                      </Text>
                    )}
                    <div className="kb-chunk" style={{ marginTop: 6 }}>
                      {r.content}
                    </div>
                    {r.parent_content && r.parent_content !== r.content && (
                      <div
                        style={{
                          marginTop: 6,
                          padding: 8,
                          background: '#f6f8fa',
                          borderRadius: 4,
                        }}
                      >
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          父块上下文（同章节，更完整）：
                        </Text>
                        <Paragraph
                          style={{ marginBottom: 0, fontSize: 13 }}
                          ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}
                        >
                          {r.parent_content}
                        </Paragraph>
                      </div>
                    )}
                    <Space wrap size={4}>
                      {(r.tags ?? []).map((name) => (
                        <Tag key={name} color="blue" style={{ fontSize: 11 }}>
                          {name}
                        </Tag>
                      ))}
                    </Space>
                  </Card>
                ))}
              </Space>
            )}
          </Card>

          <Alert
            type="success"
            showIcon
            message="本次检索已记入审计日志"
            description="审计记录包含命中的文档 ID，用于事后追溯数据流向。可在「审计日志」中查看。"
          />
        </>
      )}
    </Space>
  )
}
