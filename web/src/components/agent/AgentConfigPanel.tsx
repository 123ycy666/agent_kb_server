import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Checkbox, Divider, Input, Radio, Select, Space, Tag, Typography, message,
} from 'antd'
import { SaveOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { agentHttp } from '../../api/client'
import { specsApi } from '../../api'

type Cfg = {
  style: string
  tools: { name: string; on: boolean }[]
  skills: { mode: string; names: string[]; available: string[] }
  identity: string
}

const TOOL_LABEL: Record<string, string> = {
  read_file: '读文件',
  make_file: '出文件（docx/xlsx/pptx）',
  read_skill: '读技能正文',
  list_skills: '列技能目录',
  search_kb: '查资料（制度/办法）',
  query_data: '查业务库',
  db_schema: '看库结构',
  remember: '记一条',
  recall: '找记忆',
}

export default function AgentConfigPanel() {
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [style, setStyle] = useState('')
  const [on, setOn] = useState<Record<string, boolean>>({})
  const [mode, setMode] = useState('all')
  const [names, setNames] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [skillName, setSkillName] = useState('')
  const [whenToUse, setWhenToUse] = useState('')

  const uploadSkill = async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('space', 'spec')
    fd.append('name', skillName || '')
    fd.append('when_to_use', whenToUse || '')
    fd.append('applies', '')
    fd.append('product', '')
    fd.append('role_code', '')
    fd.append('global_scope', '0')
    try {
      const r: any = await specsApi.upload(fd)
      try {
        await specsApi.publish(r.spec_id)
        message.success(`已上传并发布《${r.name}》，下一轮生效`)
      } catch {
        message.warning(`已上传《${r.name}》，但还没发布；去知识库页补四项后发布`)
      }
      setSkillName('')
      setWhenToUse('')
      await load()
    } catch (e: any) {
      message.error(e?.message || '上传失败')
    }
  }

  const load = async () => {
    try {
      const r: any = await agentHttp.get('/api/agent/config')
      setCfg(r)
      setStyle(r.style || '')
      setOn(Object.fromEntries((r.tools || []).map((t: any) => [t.name, t.on])))
      setMode(r.skills?.mode || 'all')
      setNames(r.skills?.names || [])
    } catch (e: any) {
      message.error(e?.message || '配置读不出来')
    }
  }

  useEffect(() => { void load() }, [])

  const save = async () => {
    setSaving(true)
    try {
      const tools_off = Object.entries(on).filter(([, v]) => !v).map(([k]) => k)
      const r: any = await agentHttp.put('/api/agent/config', {
        style, tools_off, skills: { mode, names },
      })
      const bad = Object.values(r || {}).find((x: any) => x && x.error) as any
      if (bad) message.error(bad.error)
      else message.success('已保存，下一轮生效')
      await load()
    } catch (e: any) {
      message.error(e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (!cfg) return <Card size="small" loading />

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="这里的每一项保存后下一轮就生效；身份在「提示词」页改，《查数口径》是一篇技能，在知识库页改。"
      />

      <Card size="small" title="回答风格">
        <Input.TextArea
          value={style}
          onChange={(e) => setStyle(e.target.value)}
          autoSize={{ minRows: 4, maxRows: 12 }}
          placeholder="用大白话写你要什么样的回答，例如：先说结论，再列要点；不超过五句；不用表格；数字后面标出处。"
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          留空 = 中性默认，不额外要求。
        </Typography.Text>
      </Card>

      <Card size="small" title="工具开关">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {cfg.tools.map((t) => (
            <Checkbox
              key={t.name}
              checked={on[t.name] !== false}
              onChange={(e) => setOn((cur) => ({ ...cur, [t.name]: e.target.checked }))}
            >
              {TOOL_LABEL[t.name] || t.name}
              <Tag style={{ marginInlineStart: 6 }}>{t.name}</Tag>
            </Checkbox>
          ))}
        </div>
      </Card>

      <Card size="small" title="技能">
        <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
          <Radio value="all">全开（目录里有的都能用）</Radio>
          <Radio value="selected">只给指定的</Radio>
          <Radio value="off">全关（只靠通用能力）</Radio>
        </Radio.Group>
        {mode === 'selected' && (
          <div style={{ marginTop: 8 }}>
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              value={names}
              onChange={setNames}
              options={cfg.skills.available.map((n) => ({ label: n, value: n }))}
              placeholder="选要用到的技能"
            />
          </div>
        )}
        <Divider style={{ margin: '10px 0' }} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          新增或改技能正文在 <Link to="/specs">知识库</Link> 那页；《查数口径》也在那里改。
        </Typography.Text>
      </Card>

      <Card size="small" title="上传技能">
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Input value={skillName} onChange={(e) => setSkillName(e.target.value)}
            placeholder="技能名（留空就用文件里的名字）" />
          <Input value={whenToUse} onChange={(e) => setWhenToUse(e.target.value)}
            placeholder="什么时候用它（一句话，模型靠这句判断要不要读）" />
          <Space>
            <label style={{ cursor: 'pointer', color: '#1677ff' }}>
              选文件上传
              <input type="file" style={{ display: 'none' }} accept=".md,.txt,.docx"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) void uploadSkill(f)
                  e.target.value = ''
                }} />
            </label>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              上传后直接发布；没发布成功去知识库页补四项
            </Typography.Text>
          </Space>
        </Space>
      </Card>

      <Card size="small" title="身份（只读）">
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0, fontSize: 13 }}>
          {cfg.identity || '（还没配身份，用代码里的兜底）'}
        </Typography.Paragraph>
      </Card>

      <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>
        保存
      </Button>
    </Space>
  )
}
