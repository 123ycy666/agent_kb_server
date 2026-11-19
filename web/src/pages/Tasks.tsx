import { useEffect, useState } from 'react'
import { Tabs, message } from 'antd'
import Page from '../components/ui/Page'
import { useTab } from '../components/ui/useTab'
import { ItemsTab, RunsTab, SchedulesTab } from './Workflows'
import { wfApi } from '../api/workflows'
import type { WfDoc } from '../api/workflows'

export default function Tasks() {
  const [tab, setTab] = useTab('items', ['items', 'runs', 'schedules'])
  const [docs, setDocs] = useState<WfDoc[]>([])

  useEffect(() => {
    wfApi
      .docs()
      .then((r) => setDocs(r.items ?? []))
      .catch((e) => message.error(e?.message || '规则文档拉不出来'))
  }, [])

  return (
    <Page title="任务">
      <div style={{ background: '#fff', borderRadius: 8, padding: 16 }}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          destroyInactiveTabPane
          items={[
            { key: 'items', label: '事项', children: <ItemsTab /> },
            { key: 'runs', label: '运行记录', children: <RunsTab /> },
            { key: 'schedules', label: '调度', children: <SchedulesTab docs={docs} /> },
          ]}
        />
      </div>
    </Page>
  )
}
