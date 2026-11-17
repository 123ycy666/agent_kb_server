import { useEffect, useMemo, useState } from 'react'
import { Card, Collapse, Input, Table, Tag, message } from 'antd'
import { http } from '../api/client'

type Row = {
  group: string
  table: string
  zh: string
  purpose: string
  documented: boolean
  cols: number
  rows: number
  page: string
}

type Col = {
  name: string
  type: string
  nullable: boolean
  default: string | null
  comment: string
}

const GROUP_LABEL: Record<string, string> = {
  kb: 'kb · 知识库',
  cfg: 'cfg · 配置',
  qa: 'qa · 问答记录',
  base: 'base · 平台',
}

export default function SchemaMap() {
  const [rows, setRows] = useState<Row[]>([])
  const [summary, setSummary] = useState<{ tables: number; no_page: number; undocumented: number } | null>(null)
  const [detail, setDetail] = useState<Record<string, { columns: Col[]; rows: any[] }>>({})
  const [open, setOpen] = useState<string[]>([])

  const load = () =>
    http.get('/schema/overview').then((r: any) => {
      setRows(r.items || [])
      setSummary(r.summary || null)
    })

  useEffect(() => {
    load()
  }, [])

  const openTable = (full: string) => {
    setOpen(v => (v.includes(full) ? v : [...v, full]))
    if (detail[full]) return
    http.get(`/schema/table/${full}`).then((r: any) => setDetail(d => ({ ...d, [full]: r })))
  }

  const saveComment = (full: string, column: string, text: string) =>
    http.post('/schema/comment', { table: full, column, text }).then(() => {
      message.success('含义已写回数据库注释')
      http.get(`/schema/table/${full}`).then((r: any) => setDetail(d => ({ ...d, [full]: r })))
    })

  const groups = useMemo(() => ['kb', 'cfg', 'qa', 'base'], [])

  return (
    <Card
      title="库结构（information_schema 实时读取，不读配置文件）"
      extra={
        summary
          ? `共 ${summary.tables} 张表 · 无页面入口 ${summary.no_page} · 无表说明 ${summary.undocumented}`
          : null
      }
    >
      {groups.map(g => {
        const list = rows.filter(r => r.group === g)
        return (
          <Card key={g} type="inner" title={`${GROUP_LABEL[g]}（${list.length} 张表）`} style={{ marginBottom: 12 }}>
            <Table
              size="small"
              rowKey={r => `${r.group}.${r.table}`}
              dataSource={list}
              pagination={false}
              expandable={{
                expandedRowKeys: open.filter(k => k.startsWith(g + '.')),
                onExpand: (expanded, r) => {
                  const full = `${r.group}.${r.table}`
                  if (expanded) openTable(full)
                  else setOpen(v => v.filter(k => k !== full))
                },
                expandedRowRender: r => {
                  const full = `${r.group}.${r.table}`
                  const d = detail[full]
                  if (!d) return <span style={{ color: '#999' }}>读取中…</span>
                  return (
                    <Table
                      size="small"
                      rowKey="name"
                      dataSource={d.columns}
                      pagination={false}
                      columns={[
                        { title: '字段', dataIndex: 'name', width: 180 },
                        { title: '类型', dataIndex: 'type', width: 160 },
                        { title: '可空', width: 70, render: (_: any, c: Col) => (c.nullable ? '是' : '否') },
                        {
                          title: '含义（可直接改，写回数据库注释）',
                          render: (_: any, c: Col) => (
                            <Input
                              size="small"
                              defaultValue={c.comment}
                              placeholder="未登记"
                              onBlur={e => {
                                const v = e.target.value.trim()
                                if (v !== c.comment) saveComment(full, c.name, v)
                              }}
                            />
                          ),
                        },
                      ]}
                    />
                  )
                },
              }}
              columns={[
                {
                  title: '表',
                  width: 220,
                  render: (_: any, r: Row) => <code>{r.group}.{r.table}</code>,
                },
                {
                  title: '说明（表注释）',
                  render: (_: any, r: Row) =>
                    r.documented ? r.zh : <Tag color="orange">未登记</Tag>,
                },
                { title: '字段', dataIndex: 'cols', width: 80 },
                { title: '行数', dataIndex: 'rows', width: 90 },
                {
                  title: '页面入口',
                  width: 130,
                  render: (_: any, r: Row) =>
                    r.page ? <Tag color="green">{r.page}</Tag> : <Tag color="red">无入口</Tag>,
                },
              ]}
            />
          </Card>
        )
      })}
      <Collapse
        size="small"
        items={[
          {
            key: 'help',
            label: '这页是干什么的',
            children:
              '定稿 H14.2 要求每张表都有页面入口。标红的就是「有人在用却没人管」的表；' +
              '字段「含义」为空表示库里没有注释，直接填，写回 COMMENT ON COLUMN，psql 里也能看到。',
          },
        ]}
      />
    </Card>
  )
}
