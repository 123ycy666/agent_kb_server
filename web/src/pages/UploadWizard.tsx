import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Input,
  Result,
  Select,
  Space,
  Steps,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'
import dayjs from 'dayjs'
import { catalogApi, docApi, kbApi } from '../api'
import type { KnowledgeBase } from '../api'

const { Text, Paragraph } = Typography

const SECRECY = [
  { value: 'public', label: '公开' },
  { value: 'internal', label: '内部' },
  { value: 'sensitive', label: '敏感' },
]

/** 上传六步：选库 → 选目录 → 填元数据 → 打标 → 预览 → 提交（进待发布）。 */
export default function UploadWizard({ onDone }: { onDone?: () => void }) {
  const [step, setStep] = useState(0)
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [cats, setCats] = useState<any[]>([])
  const [kinds, setKinds] = useState<any[]>([])
  const [topics, setTopics] = useState<any[]>([])
  const [files, setFiles] = useState<UploadFile[]>([])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<any[]>([])

  const [form, setForm] = useState<any>({
    kb_id: null as number | null,
    catalog_id: null as number | null,
    doc_kind: '',
    issuer: '',
    doc_no: '',
    signed_on: null as string | null,
    effective_from: null as string | null,
    secrecy: 'internal',
    topics: [] as string[],
  })

  useEffect(() => {
    kbApi.list(1, 50).then((r) => {
      const items = r.items ?? []
      setBases(items)
      setForm((f: any) => ({ ...f, kb_id: f.kb_id ?? items[0]?.id ?? null }))
    })
    catalogApi.tree().then((r) => setCats((r.items ?? []).filter((c: any) => c.parent_id && c.enabled)))
    catalogApi.terms('文种').then((r) => setKinds((r.items ?? []).filter((t: any) => t.enabled)))
    catalogApi.terms('主题').then((r) => setTopics((r.items ?? []).filter((t: any) => t.enabled)))
  }, [])

  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }))

  const check1 = () => !!form.kb_id
  const check2 = () => !!form.catalog_id
  const check3 = () =>
    !!form.doc_kind && !!form.issuer.trim() && !!form.doc_no.trim() && !!form.signed_on
  const check4 = () => form.topics.length <= 2
  const check5 = () => files.length > 0

  const canNext = [check1, check2, check3, () => check4() && check5(), () => true][step]

  const submit = async () => {
    if (!files.length) return message.warning('先选文件')
    if (!form.catalog_id || !form.doc_kind) return message.warning('目录和文种必填')
    setBusy(true)
    const results: any[] = []
    try {
      for (const f of files) {
        const raw = f.originFileObj as File
        if (!raw) continue
        const tags = [form.doc_kind, ...form.topics].filter(Boolean)
        const up: any = await docApi.upload(form.kb_id as number, raw, true, tags)
        const docId = up?.doc_id || up?.id
        if (!docId) {
          results.push({ file: f.name, ok: false, why: '上传没返回 doc_id' })
          continue
        }
        await catalogApi.saveDoc(docId, {
          catalog_id: form.catalog_id,
          doc_kind: form.doc_kind,
          issuer: form.issuer,
          doc_no: form.doc_no,
          signed_on: form.signed_on,
          effective_from: form.effective_from || form.signed_on,
          secrecy: form.secrecy,
          status: 'draft',
        })
        results.push({ file: f.name, ok: true, doc_id: docId })
      }
      setDone(results)
      setStep(5)
      message.success('已入库，处于「待发布」；发布后 60 秒内可被检索')
      onDone?.()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const reset = () => {
    setStep(0)
    setFiles([])
    setDone([])
    setForm((f: any) => ({ ...f, catalog_id: null, doc_kind: '', doc_no: '', issuer: '', topics: [] }))
  }

  return (
    <Card size="small">
      <Steps
        size="small"
        current={step}
        style={{ marginBottom: 18 }}
        items={[
          { title: '选库' },
          { title: '选目录' },
          { title: '元数据' },
          { title: '标签与文件' },
          { title: '确认' },
          { title: '完成' },
        ]}
      />

      {step === 0 ? (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text strong>这份材料放哪个库？</Text>
          <Select
            style={{ width: 320 }}
            value={form.kb_id ?? undefined}
            onChange={(v) => set('kb_id', v)}
            options={bases.map((b) => ({ value: b.id, label: `${b.name}（#${b.id}）` }))}
          />
          <Text type="secondary">说明书库进注入目录；资料库只参与检索。这里传的是资料。</Text>
        </Space>
      ) : null}

      {step === 1 ? (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text strong>放哪个目录？（一篇只放一个主目录）</Text>
          <Select
            style={{ width: 360 }}
            showSearch
            value={form.catalog_id ?? undefined}
            onChange={(v) => set('catalog_id', v)}
            options={cats.map((c) => ({ value: c.id, label: c.path }))}
          />
          <Text type="secondary">目录即权限边界；找不到合适目录就去「目录与词表」里先建。</Text>
        </Space>
      ) : null}

      {step === 2 ? (
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <div>
            <Text strong>文种（必填）</Text>
            <Select
              style={{ width: 240 }}
              value={form.doc_kind || undefined}
              onChange={(v) => set('doc_kind', v)}
              options={kinds.map((t) => ({ value: t.term, label: t.term }))}
            />
          </div>
          <div>
            <Text strong>发文单位（必填）</Text>
            <Input
              style={{ width: 360 }}
              value={form.issuer}
              placeholder="如 综合供电公司"
              onChange={(e) => set('issuer', e.target.value)}
            />
          </div>
          <div>
            <Text strong>文号（必填，没有就填 —）</Text>
            <Input
              style={{ width: 360 }}
              value={form.doc_no}
              placeholder="如 国网办〔2024〕12号"
              onChange={(e) => set('doc_no', e.target.value)}
            />
          </div>
          <Space size={12}>
            <div>
              <Text strong>成文日期（必填）</Text>
              <DatePicker
                value={form.signed_on ? dayjs(form.signed_on) : null}
                onChange={(d) => set('signed_on', d ? d.format('YYYY-MM-DD') : null)}
              />
            </div>
            <div>
              <Text strong>生效日期</Text>
              <DatePicker
                value={form.effective_from ? dayjs(form.effective_from) : null}
                onChange={(d) => set('effective_from', d ? d.format('YYYY-MM-DD') : null)}
              />
            </div>
          </Space>
          <div>
            <Text strong>密级</Text>
            <Select
              style={{ width: 200 }}
              value={form.secrecy}
              onChange={(v) => set('secrecy', v)}
              options={SECRECY}
            />
          </div>
        </Space>
      ) : null}

      {step === 3 ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <Text strong>主题标签（最多 2 个，跨目录才用）</Text>
            <Select
              mode="multiple"
              style={{ width: 420 }}
              value={form.topics}
              onChange={(v: string[]) => set('topics', v.slice(0, 2))}
              options={topics.map((t) => ({ value: t.term, label: t.term }))}
            />
          </div>
          <div>
            <Text strong>文件（md / docx / pdf / txt）</Text>
            <Upload.Dragger
              multiple
              beforeUpload={() => false}
              fileList={files}
              onChange={({ fileList }) => setFiles(fileList)}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点这里选文件，或拖进来</p>
            </Upload.Dragger>
          </div>
        </Space>
      ) : null}

      {step === 4 ? (
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="库">
              {bases.find((b) => b.id === form.kb_id)?.name || '—'}
            </Descriptions.Item>
            <Descriptions.Item label="目录">
              {cats.find((c) => c.id === form.catalog_id)?.path || '—'}
            </Descriptions.Item>
            <Descriptions.Item label="文种">{form.doc_kind || '—'}</Descriptions.Item>
            <Descriptions.Item label="文号">{form.doc_no || '—'}</Descriptions.Item>
            <Descriptions.Item label="发文单位">{form.issuer || '—'}</Descriptions.Item>
            <Descriptions.Item label="密级">
              {SECRECY.find((s) => s.value === form.secrecy)?.label}
            </Descriptions.Item>
            <Descriptions.Item label="成文日期">{form.signed_on || '—'}</Descriptions.Item>
            <Descriptions.Item label="生效日期">{form.effective_from || '—'}</Descriptions.Item>
            <Descriptions.Item label="标签" span={2}>
              {[form.doc_kind, ...form.topics].filter(Boolean).map((t: string) => (
                <Tag key={t}>{t}</Tag>
              ))}
            </Descriptions.Item>
            <Descriptions.Item label="文件" span={2}>
              {files.map((f) => f.name).join('、') || '—'}
            </Descriptions.Item>
          </Descriptions>
          <Alert
            type="info"
            showIcon
            message="提交后进「待发布」，不会立刻被检索"
            description="到「资料库」点发布，60 秒内进入检索范围。"
          />
        </Space>
      ) : null}

      {step === 5 ? (
        <Result
          status="success"
          title="已入库，处于待发布"
          subTitle={done.map((d) => `${d.file}${d.ok ? '' : '（失败：' + d.why + '）'}`).join('；')}
          extra={<Button onClick={reset}>再传一份</Button>}
        />
      ) : null}

      {step < 5 ? (
        <Space style={{ marginTop: 16 }}>
          <Button disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
            上一步
          </Button>
          {step < 4 ? (
            <Button type="primary" disabled={!canNext()} onClick={() => setStep((s) => s + 1)}>
              下一步
            </Button>
          ) : (
            <Button type="primary" loading={busy} onClick={() => void submit()}>
              提交入库
            </Button>
          )}
          {!canNext() ? (
            <Text type="secondary">
              这一步还有必填项没填
            </Text>
          ) : null}
        </Space>
      ) : null}

      <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}>
        目录管归属与权限，标签管跨目录关联；发布后才会进检索范围。
      </Paragraph>
    </Card>
  )
}
