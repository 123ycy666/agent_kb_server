import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Input, Radio, Select, Space, Switch, Tabs, Tag, Typography, message } from 'antd'
import { Link } from 'react-router-dom'
import { agentHttp } from '../../api/client'
import { useTab } from '../ui/useTab'

const { Text, Paragraph } = Typography

type Tool = { name: string; on: boolean; when_not?: string }
type Cfg = {
  style: string
  tools: Tool[]
  skills: { mode: string; names: string[]; available: string[] }
  identity: string
  model?: { name: string; max_turns: number; context_budget: number; compact_at: number }
}

const TOOL_LABEL: Record<string, string> = {
  read_file: '读文件',
  make_file: '出文件',
  read_skill: '读技能正文',
  list_skills: '列技能目录',
  search_kb: '查资料',
  query_data: '查业务库',
  db_schema: '看库结构',
  remember: '记一条',
  recall: '找记忆',
}

/** 设置页模板：标签左、内容右，标签宽 120。 */
function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
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

export default function AgentConfigPanel() {
  const [tab, setTab] = useTab('profile', ['profile', 'skills', 'tools', 'model'])
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [style, setStyle] = useState('')
  const [on, setOn] = useState<Record<string, boolean>>({})
  const [mode, setMode] = useState('all')
  const [names, setNames] = useState<string[]>([])
  const [saving, setSaving] = useState('')

  const load = async () => {
    try {
      const r: Cfg = await agentHttp.get('/api/agent/config')
      setCfg(r)
      setStyle(r.style || '')
      setOn(Object.fromEntries((r.tools || []).map((t) => [t.name, t.on])))
      setMode(r.skills?.mode || 'all')
      setNames(r.skills?.names || [])
    } catch (e: any) {
      message.error(e?.message || '配置读不出来')
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const save = async (part: 'style' | 'tools' | 'skills') => {
    setSaving(part)
    try {
      const body: Record<string, unknown> = {}
      if (part === 'style') body.style = style
      if (part === 'tools') body.tools_off = Object.entries(on).filter(([, v]) => !v).map(([k]) => k)
      if (part === 'skills') body.skills = { mode, names }
      const r: any = await agentHttp.put('/api/agent/config', body)
      const bad = Object.values(r || {}).find((x: any) => x && x.error) as any
      if (bad) message.error(bad.error)
      else message.success('已保存')
      await load()
    } catch (e: any) {
      message.error(e?.message || '保存失败')
    } finally {
      setSaving('')
    }
  }

  const SaveBar = ({ part }: { part: 'style' | 'tools' | 'skills' }) => (
    <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 12 }}>
      <Button type="primary" loading={saving === part} onClick={() => void save(part)}>
        保存
      </Button>
    </div>
  )

  if (!cfg) return <div style={{ padding: 24 }} />

  return (
    <Tabs
      activeKey={tab}
      onChange={setTab}
      items={[
        {
          key: 'profile',
          label: '身份与风格',
          children: (
            <div>
              <Field label="身份" hint="身份在提示词页改，改完下一轮生效">
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap' }} type={cfg.identity ? undefined : 'secondary'}>
                    {cfg.identity || '（还没配身份，用代码里的兜底）'}
                  </Paragraph>
                  <Link to="/prompts?tab=agent">去提示词页改</Link>
                </Space>
              </Field>
              <Field label="回答风格" hint={`留空 = 中性默认　${style.length}/2000`}>
                <Input.TextArea
                  value={style}
                  maxLength={2000}
                  onChange={(e) => setStyle(e.target.value)}
                  autoSize={{ minRows: 5, maxRows: 12 }}
                  placeholder="先说结论，再列要点；不超过五句；不用表格。"
                />
              </Field>
              <SaveBar part="style" />
            </div>
          ),
        },
        {
          key: 'skills',
          label: '技能',
          children: (
            <div>
              <Field label="读取范围" hint="改完下一轮生效">
                <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
                  <Space direction="vertical">
                    <Radio value="all">全开（目录里有的都能用）</Radio>
                    <Radio value="selected">只给指定的</Radio>
                    <Radio value="off">全关（只靠通用能力）</Radio>
                  </Space>
                </Radio.Group>
              </Field>
              {mode === 'selected' && (
                <Field label="指定技能">
                  <Select
                    mode="multiple"
                    style={{ width: '100%' }}
                    value={names}
                    onChange={setNames}
                    options={(cfg.skills.available ?? []).map((n) => ({ label: n, value: n }))}
                    placeholder="选要用到的技能"
                    aria-label="指定技能"
                  />
                </Field>
              )}
              <Field label="新增与正文" hint="新建、改正文、退役都在技能页">
                <Link to="/skills">去技能页</Link>
              </Field>
              <SaveBar part="skills" />
            </div>
          ),
        },
        {
          key: 'tools',
          label: '工具',
          children: (
            <div>
              {(cfg.tools ?? []).map((t) => (
                <Field key={t.name} label={TOOL_LABEL[t.name] || t.name}>
                  <Space direction="vertical" size={2} style={{ width: '100%' }}>
                    <Space size={8}>
                      <Switch
                        size="small"
                        checked={on[t.name] !== false}
                        onChange={(v) => setOn((cur) => ({ ...cur, [t.name]: v }))}
                        aria-label={`开关 ${TOOL_LABEL[t.name] || t.name}`}
                      />
                      <Tag style={{ marginInlineStart: 0 }}>{t.name}</Tag>
                    </Space>
                    {t.when_not ? (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {t.when_not}
                      </Text>
                    ) : null}
                  </Space>
                </Field>
              ))}
              <SaveBar part="tools" />
            </div>
          ),
        },
        {
          key: 'model',
          label: '模型',
          children: (
            <div>
              <Field label="模型">{cfg.model?.name || '—'}</Field>
              <Field label="步数上限">{String(cfg.model?.max_turns ?? '—')}</Field>
              <Field label="上下文预算">{String(cfg.model?.context_budget ?? '—')} token</Field>
              <Field label="压缩阈值">
                {cfg.model ? `${Math.round((cfg.model.compact_at ?? 0) * 100)}%` : '—'}
              </Field>
              <Field label="改这些" hint="模型与端点在设置页改；这几项是代码里的默认值">
                <Link to="/settings">去设置页</Link>
              </Field>
            </div>
          ),
        },
      ]}
    />
  )
}
