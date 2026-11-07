import { agentHttp } from './client'

/** 工作流框架接口：/api/workflows/*（不走 v1 前缀） */
const B = '/api/workflows'

export interface WfSection {
  key: string
  title: string
  hint: string
  example: string
}

export interface WfDoc {
  id: number
  name: string
  work_type: 'detect' | 'send'
  work_type_label: string
  alert_type: string
  enabled: boolean
  disabled_reason?: string | null
  scope_level: string
  schedule_id?: number | null
  doc: Record<string, string>
  version: number
  updated_by?: string
  updated_at?: string
  missing?: string[]
  complete?: boolean
  detached_from?: string | null
  sections?: WfSection[]
  versions?: { version: number; updated_by?: string; at?: string }[]
}

export interface WfSchedule {
  id: number
  name: string
  cron: string
  crons: string[]
  timezone: string
  enabled: boolean
  note?: string
  doc_count: number
  doc_ids: number[]
  doc_names: string[]
  next: string[]
  cron_error?: string
}

export interface WfRun {
  id: number
  doc_id: number
  doc_name: string
  doc_version: number
  work_type: string
  trigger: string
  status: string
  dry_run: boolean
  started_at?: string
  finished_at?: string
  data_as_of?: string
  checked_n: number
  hit_n: number
  new_n: number
  closed_n: number
  sent_n: number
  error?: string | null
  logs?: { seq: number; at?: string; kind: string; payload: Record<string, unknown> }[]
  doc_snapshot?: Record<string, string>
  sections?: { key: string; title: string }[]
}

export interface WfItem {
  id: number
  doc_id: number
  alert_type: string
  object_key: string
  object_name: string
  value?: number | null
  threshold?: number | null
  op?: string | null
  tier?: string | null
  detail: Record<string, unknown>
  evidence: Record<string, unknown>
  reliable: boolean
  status: string
  close_reason?: string | null
  first_seen?: string
  last_seen?: string
  sent_at?: string | null
  doc_name?: string
  unit_name?: string
  county_name?: string
  value_unit?: string
  notifications?: { id: number; level: string; scope_name: string; message?: string | null;
    recipient_login: string; recipient_name?: string | null; role_code?: string | null;
    sent_at?: string | null; read_at?: string | null }[]
  notifies?: { id: number; login: string; name?: string; channel: string; reason?: string; ok: boolean; sent_at?: string }[]
}

export const wfApi = {
  templates: (t: string) =>
    agentHttp.get<any, { work_type: string; label: string; sections: WfSection[] }>(`${B}/templates/${t}`),
  docs: (params: Record<string, unknown> = {}) =>
    agentHttp.get<any, { items: WfDoc[] }>(`${B}/docs`, { params }),
  doc: (id: number) => agentHttp.get<any, WfDoc>(`${B}/docs/${id}`),
  createDoc: (body: Record<string, unknown>) => agentHttp.post<any, WfDoc>(`${B}/docs`, body),
  saveDoc: (id: number, body: Record<string, unknown>) => agentHttp.put<any, WfDoc>(`${B}/docs/${id}`, body),
  deleteDoc: (id: number) => agentHttp.delete(`${B}/docs/${id}`),
  duplicateDoc: (id: number, name?: string) => agentHttp.post<any, WfDoc>(`${B}/docs/${id}/duplicate`, { name }),
  exportDoc: (id: number) => agentHttp.get<any, Blob>(`${B}/docs/${id}/export`, { responseType: 'blob' }),
  importDoc: (id: number, markdown: string) => agentHttp.post<any, WfDoc>(`${B}/docs/${id}/import`, { markdown }),
  version: (id: number, v: number) =>
    agentHttp.get<any, { version: number; doc: Record<string, string> }>(`${B}/docs/${id}/versions/${v}`),
  dryRun: (id: number, body: Record<string, unknown> = {}) =>
    agentHttp.post<any, Record<string, unknown>>(`${B}/docs/${id}/dry-run`, body),
  run: (id: number) => agentHttp.post<any, Record<string, unknown>>(`${B}/run`, { doc_id: id }),
  schedules: () => agentHttp.get<any, { items: WfSchedule[]; status: Record<string, unknown> }>(`${B}/schedules`),
  createSchedule: (body: Record<string, unknown>) => agentHttp.post<any, WfSchedule>(`${B}/schedules`, body),
  saveSchedule: (id: number, body: Record<string, unknown>) =>
    agentHttp.put<any, WfSchedule>(`${B}/schedules/${id}`, body),
  deleteSchedule: (id: number) => agentHttp.delete(`${B}/schedules/${id}`),
  runSchedule: (id: number) => agentHttp.post(`${B}/schedules/${id}/run`),
  dryRunSchedule: (id: number) => agentHttp.post(`${B}/schedules/${id}/dry-run`),
  runs: (params: Record<string, unknown> = {}) =>
    agentHttp.get<any, { total: number; items: WfRun[] }>(`${B}/runs`, { params }),
  runDetail: (id: number) => agentHttp.get<any, WfRun>(`${B}/runs/${id}`),
  items: (params: Record<string, unknown> = {}) =>
    agentHttp.get<any, { total: number; items: WfItem[] }>(`${B}/items`, { params }),
  item: (id: number) => agentHttp.get<any, WfItem>(`${B}/items/${id}`),
  closeItem: (id: number, reason: string) => agentHttp.post(`${B}/items/${id}/close`, { reason }),
  itemChat: (id: number) =>
    agentHttp.post<any, { session_id: string; chat_store: string }>(`${B}/items/${id}/chat`),
  notifications: (params: Record<string, unknown> = {}) =>
    agentHttp.get<any, { total: number; items: any[] }>(`${B}/notifications`, { params }),
  params: () => agentHttp.get<any, { items: { key: string; value: string; note?: string }[] }>(`${B}/params`),
  saveParams: (items: Record<string, string>) =>
    agentHttp.put<any, { items: any[] }>(`${B}/params`, { items }),
}
