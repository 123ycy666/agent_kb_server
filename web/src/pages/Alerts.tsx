import { useEffect, useState } from 'react'
import { Space, Tabs, Typography, message } from 'antd'
import Page from '../components/ui/Page'
import { useTab } from '../components/ui/useTab'
import { DocsTab, NotifyTab } from './Workflows'
import { wfApi } from '../api/workflows'

const { Text } = Typography

function RecordsTab() {
  const [stat, setStat] = useState<{ unread: number; todo: number; last?: string } | null>(null)

  useEffect(() => {
    wfApi
      .overview()
      .then((r) =>
        setStat({
          unread: r.unread ?? 0,
          todo: (r.alert_types ?? []).reduce((n, x) => n + (x.total ?? 0), 0),
          last: r.last_run ? `${r.last_run.doc_name} · ${r.last_run.status} · 命中 ${r.last_run.hit_n}` : '',
        })
      )
      .catch((e) => message.error(e?.message || '预警概览拉不出来'))
  }, [])

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space size={24} wrap>
        <Text>
          未读 <Text strong>{stat?.unread ?? 0}</Text>
        </Text>
        <Text>
          未处理 <Text strong>{stat?.todo ?? 0}</Text>
        </Text>
        <Text type="secondary">最近一轮：{stat?.last || '还没有跑过'}</Text>
      </Space>
      <NotifyTab />
    </Space>
  )
}

export default function Alerts() {
  const [tab, setTab] = useTab('rules', ['rules', 'records'])
  return (
    <Page title="预警">
      <div style={{ background: '#fff', borderRadius: 8, padding: 16 }}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          destroyInactiveTabPane
          items={[
            { key: 'rules', label: '规则', children: <DocsTab /> },
            { key: 'records', label: '记录', children: <RecordsTab /> },
          ]}
        />
      </div>
    </Page>
  )
}
