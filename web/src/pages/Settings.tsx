import { useEffect, useState } from 'react'
import { Alert, Button, Descriptions, Radio, Space, Tabs, Tag, Typography, message } from 'antd'
import Page from '../components/ui/Page'
import { useTab } from '../components/ui/useTab'
import { setUiPrefs, uiPrefs } from '../components/ui/prefs'
import type { UiPrefs } from '../components/ui/prefs'
import { agentHttp } from '../api/client'
import { http } from '../api/client'
import { modelApi, systemApi } from '../api'

const { Text, Paragraph } = Typography

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 12, padding: '10px 0', borderBottom: '1px solid #f0f0f0' }}>
      <div style={{ width: 120, flex: '0 0 120px', textAlign: 'right', paddingTop: 2 }}>
        <Text strong>{label}</Text>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {children}
        {hint ? (
          <div style={{ marginTop: 4 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {hint}
            </Text>
          </div>
        ) : null}
      </div>
    </div>
  )
}

function ModelTab() {
  const [cfg, setCfg] = useState<any>(null)
  const [agent, setAgent] = useState<{ name: string; default?: string; models: any[] } | null>(null)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<any>(null)

  useEffect(() => {
    modelApi.config().then(setCfg).catch((e) => message.error(e?.message || '模型配置拉不出来'))
    agentHttp
      .get<any, { default: string; models: any[] }>('/api/models')
      .catch(() => agentHttp.get<any, { default: string; models: any[] }>('/api/models'))
      .then((r) => setAgent({ ...r, name: r.default }))
      .catch(() => setAgent(null))
  }, [])

  const test = async () => {
    setTesting(true)
    setResult(null)
    try {
      setResult(await modelApi.test({}))
    } catch (e: any) {
      message.error(e?.message || '连通测试失败')
    } finally {
      setTesting(false)
    }
  }

  const online = cfg?.options?.online ?? {}
  return (
    <div>
      <Field label="模式">{cfg ? <Tag color="blue">{cfg.mode_label || cfg.mode}</Tag> : '—'}</Field>
      <Field label="向量模型（只读）">
        {cfg ? `${cfg.embedding?.model_id}（${cfg.embedding_dim} 维）` : '—'}
      </Field>
      <Field label="精排模型">{cfg?.rerank?.model_id || '—'}</Field>
      <Field label="对话模型">{agent?.name || cfg?.llm?.model_id || '—'}</Field>
      <Field label="端点" hint="三路模型共用一个网关地址（只读；改它去「现场 → 模型通道」）">
        {online.online_llm_base_url || '—'}
      </Field>
      <Field label="可选模型">
        <Space wrap size={4}>
          {(agent?.models ?? [])
            .filter((m) => m.id === agent?.default)
            .map((m) => (
              <Tag key={m.id} color="blue">
                {m.label || m.id}
              </Tag>
            ))}
          <Text type="secondary" style={{ fontSize: 12 }}>
            共 {agent?.models?.length ?? 0} 个可选（这一页只读；改连接去「现场」）
          </Text>
        </Space>
      </Field>
      <Field label="连通测试" hint="按当前模式真调一次三路模型">
        <Space>
          <Button type="primary" loading={testing} onClick={() => void test()}>
            测试
          </Button>
          {result?.results?.map((r: any) => (
            <Tag key={r.target} color={r.ok ? 'green' : 'red'}>
              {r.target} {r.ok ? '通' : '不通'}
            </Tag>
          ))}
        </Space>
        {result?.results?.some((r: any) => !r.ok) ? (
          <Alert
            style={{ marginTop: 8 }}
            type="warning"
            showIcon
            message="有模型不通"
            description={result.results
              .filter((r: any) => !r.ok)
              .map((r: any) => `${r.target}：${r.message}`)
              .join('；')}
          />
        ) : null}
      </Field>
    </div>
  )
}

function SourceTab() {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    systemApi
      .settings()
      .then(setData)
      .catch((e) => message.error(e?.message || '员工源读不出来'))
  }, [])

  const emp = data?.employee_source
  return (
    <div>
      <Field label="类型">{emp?.kind || 'MySQL'}</Field>
      <Field label="地址">{emp ? `${emp.host}:${emp.port}` : '—'}</Field>
      <Field label="库">{emp?.database || '—'}</Field>
      <Field label="表">{emp?.table || '—'}</Field>
      <Field label="连通状态" hint="账号密码由人力资源系统维护，这里只读">
        {emp ? (
          <Space>
            <Tag color={emp.ok ? 'green' : 'red'}>{emp.ok ? '正常' : '不通'}</Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {emp.detail}
            </Text>
          </Space>
        ) : (
          '—'
        )}
      </Field>
    </div>
  )
}

function StorageTab() {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    systemApi
      .settings()
      .then(setData)
      .catch((e) => message.error(e?.message || '存储信息读不出来'))
  }, [])

  const s = data?.storage
  return (
    <div>
      <Field label="上传目录" hint="文档原文件落在这里">
        <Text code>{s?.upload_dir || '—'}</Text>
      </Field>
      <Field label="产物目录" hint="智能体产出的文件落在这里">
        <Text code>{s?.output_dir || '—'}</Text>
      </Field>
      <Field label="单个上限">{s?.max_upload_mb ? `${s.max_upload_mb} MB` : '—'}</Field>
    </div>
  )
}

function LookTab() {
  const [p, setP] = useState<UiPrefs>(uiPrefs())
  const apply = (next: UiPrefs) => {
    setP(next)
    setUiPrefs(next)
  }
  return (
    <div>
      <Field label="侧栏">
        <Radio.Group
          value={p.sider}
          onChange={(e) => apply({ ...p, sider: e.target.value })}
          options={[
            { label: '深色', value: 'dark' },
            { label: '浅色', value: 'light' },
          ]}
          optionType="button"
        />
      </Field>
      <Field label="字号">
        <Radio.Group
          value={p.font}
          onChange={(e) => apply({ ...p, font: e.target.value })}
          options={[
            { label: '小 13', value: 13 },
            { label: '中 14', value: 14 },
            { label: '大 15', value: 15 },
          ]}
          optionType="button"
        />
      </Field>
      <Paragraph type="secondary" style={{ marginTop: 12, fontSize: 12 }}>
        外观只存在这台机器的浏览器里。
      </Paragraph>
    </div>
  )
}

export default function Settings() {
  const [tab, setTab] = useTab('model', ['model', 'source', 'storage', 'look'])
  return (
    <Page title="设置">
      <div style={{ background: '#fff', borderRadius: 8, padding: 16, maxWidth: 960 }}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            { key: 'model', label: '模型与端点', children: <ModelTab /> },
            { key: 'source', label: '员工源', children: <SourceTab /> },
            { key: 'storage', label: '存储', children: <StorageTab /> },
            { key: 'look', label: '外观', children: <LookTab /> },
          ]}
        />
      </div>
    </Page>
  )
}
