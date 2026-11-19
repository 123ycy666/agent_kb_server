export type ToolStatus = 'running' | 'complete' | 'incomplete'

export type TextPart = { kind: 'text'; text: string }

export type ToolPart = {
  kind: 'tool'
  toolCallId: string
  name: string
  argsText: string
  result?: string
  isError?: boolean
  status: ToolStatus
  startedAt: number
  ms?: number
}

export type FilePart = {
  kind: 'file'
  fileId: string
  name: string
  size: number
  mime: string
  url: string
}

export type Part = TextPart | ToolPart | FilePart

export type Msg = {
  key: string
  role: 'user' | 'assistant'
  text?: string
  files?: FilePart[]
  parts: Part[]
  status: 'running' | 'complete' | 'stopped' | 'error'
  error?: string
  usage?: number
  ms?: number
}

export const TOOL_LABEL: Record<string, string> = {
  read_file: '读文件',
  make_file: '出文件',
  read_skill: '读技能',
  list_skills: '技能目录',
  search_kb: '查资料',
  query_data: '查数',
  db_schema: '看库结构',
  remember: '记一条',
  recall: '找记忆',
}

export const MIME: Record<string, string> = {
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  pdf: 'application/pdf',
}

export function textOf(payload: any): string {
  if (!payload) return ''
  if (typeof payload === 'string') return payload
  if (typeof payload.text === 'string') return payload.text
  const c = payload.content
  if (typeof c === 'string') return c
  if (Array.isArray(c)) return c.map((x: any) => x?.text || '').join('')
  return ''
}

export function sizeText(n: number): string {
  if (!n) return ''
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}

export function msText(ms?: number): string {
  if (!ms || ms < 0) return ''
  if (ms < 1000) return '<1s'
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's'
  return Math.floor(ms / 60000) + 'm ' + Math.round((ms % 60000) / 1000) + 's'
}
