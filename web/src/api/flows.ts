import { agentHttp } from './client'

/** 工作流框架接口：/api/flows（五段配置 → 编译成 SQL → 回填） */
const B = '/api/flows'

export interface FlowError {
  section: string
  slot: string
  message: string
}

export interface Cond {
  kind?: 'cond'
  ref: string
  op: string
  value?: unknown
}

export interface CondGroup {
  kind: 'group'
  op: 'AND' | 'OR'
  items: (Cond | CondGroup)[]
}

export interface Filter {
  op: 'AND' | 'OR'
  items: (Cond | CondGroup)[]
}

export interface SourceTable {
  table: string
  alias: string
  columns: string[]
}

export interface Join {
  left: string
  right: string
  type: 'inner' | 'left'
}

export interface WfSpec {
  source: {
    db: string
    tables: SourceTable[]
    joins: Join[]
    filter: Filter
    limit: number
  }
  calc: {
    grain: { name: string; ref: string }[]
    fields: { name: string; expr: string }[]
    row_policy: string
    order_ref: string
  }
  judge: {
    rules: { when: Cond[]; op?: 'AND' | 'OR'; then: { hit: boolean; tier: string } }[]
    default: { hit: boolean; tier: string }
  }
  write: {
    db: string
    table: string
    mode: 'update' | 'upsert' | 'insert'
    key: { target: string; from: string }[]
    map: { target: string; from?: string; value?: unknown }[]
    miss: string
  }
  note: string
}

export interface Option {
  value: string
  label: string
}

export interface Enums {
  cond_ops: Option[]
  row_policy: Option[]
  mode: Option[]
  join: Option[]
}

export interface ColumnMeta {
  name: string
  type: string
  nullable: boolean
  comment: string
}

export interface FlowSummary {
  source_tables: string[]
  target: string
  target_table: string
  target_columns: string[]
  mode: string
}

export interface RunBrief {
  id: number
  status: string
  at?: string | null
  selected_n: number
  hit_n: number
  written_n: number
  elapsed_ms: number
  dry_run: boolean
}

export interface FlowItem {
  id: number
  name: string
  enabled: boolean
  version: number
  updated_by?: string | null
  updated_at?: string | null
  summary: FlowSummary
  complete: boolean
  last_run?: RunBrief | null
  note: string
}

export interface FlowDetail extends FlowItem {
  spec: WfSpec
  errors: FlowError[]
  versions: { version: number; updated_by?: string | null; at?: string | null }[]
  templates: Enums
}

export interface FlowRun {
  id: number
  flow_id: number
  flow_name: string
  flow_version: number
  trigger: string
  status: string
  dry_run: boolean
  started_at?: string | null
  finished_at?: string | null
  elapsed_ms: number
  selected_n: number
  hit_n: number
  written_n: number
  sql_text?: string | null
  error?: string | null
}

export interface TriggerRule {
  unit: 'day' | 'week' | 'month'
  dow: number[]
  dom: number[]
  times: string[]
}

export interface TriggerItem {
  id: number
  name: string
  enabled: boolean
  times: string[]
  rule: TriggerRule
  rule_text: string
  note: string
  next: string[]
  flows: { id: number; name: string; enabled: boolean }[]
  last?: { id: number; status: string; at?: string | null; written_n: number } | null
}

export interface RunResult {
  run_id: number
  status: string
  dry: boolean
  selected_n: number
  hit_n: number
  written_n: number
  updated: number
  inserted: number
  elapsed_ms: number
  sql: string
  write_sql: string
  sample: Record<string, unknown>[]
}

export const flowApi = {
  metaDbs: () => agentHttp.get<any, Option[]>(`${B}/meta/dbs`),
  metaTables: (db: string, keyword = '') =>
    agentHttp.get<any, { db: string; items: { name: string; comment: string; column_count: number; key: string[] }[] }>(
      `${B}/meta/tables`, { params: { db, keyword } },
    ),
  metaColumns: (db: string, table: string) =>
    agentHttp.get<any, { columns: ColumnMeta[]; key: string[]; comment: string }>(
      `${B}/meta/columns`, { params: { db, table } },
    ),
  metaRefresh: (db?: string) => agentHttp.post(`${B}/meta/refresh`, null, { params: { db } }),

  list: () => agentHttp.get<any, { items: FlowItem[] }>(B),
  get: (id: number) => agentHttp.get<any, FlowDetail>(`${B}/${id}`),
  create: (body: Record<string, unknown>) => agentHttp.post<any, FlowDetail>(B, body),
  save: (id: number, body: Record<string, unknown>) => agentHttp.put<any, FlowDetail>(`${B}/${id}`, body),
  remove: (id: number) => agentHttp.delete(`${B}/${id}`),
  duplicate: (id: number) => agentHttp.post<any, FlowDetail>(`${B}/${id}/duplicate`),
  compile: (id: number) =>
    agentHttp.post<any, { ok: boolean; errors: FlowError[]; select_preview?: string; write_preview?: string }>(
      `${B}/${id}/compile`,
    ),
  dryRun: (id: number) => agentHttp.post<any, RunResult>(`${B}/${id}/dry-run`),
  run: (id: number) => agentHttp.post<any, RunResult>(`${B}/${id}/run`),

  triggers: () => agentHttp.get<any, { items: TriggerItem[]; status: Record<string, unknown> }>(`${B}/triggers`),
  createTrigger: (body: Record<string, unknown>) => agentHttp.post<any, TriggerItem>(`${B}/triggers`, body),
  saveTrigger: (id: number, body: Record<string, unknown>) =>
    agentHttp.put<any, TriggerItem>(`${B}/triggers/${id}`, body),
  deleteTrigger: (id: number) => agentHttp.delete(`${B}/triggers/${id}`),
  runTrigger: (id: number) => agentHttp.post(`${B}/triggers/${id}/run`),

  runs: (params: Record<string, unknown> = {}) => agentHttp.get<any, { items: FlowRun[] }>(`${B}/runs`, { params }),
  runDetail: (id: number) => agentHttp.get<any, FlowRun>(`${B}/runs/${id}`),
}

export const blankSpec = (): WfSpec => ({
  source: { db: 'biz', tables: [], joins: [], filter: { op: 'AND', items: [] }, limit: 200000 },
  calc: { grain: [], fields: [], row_policy: 'first', order_ref: '' },
  judge: { rules: [], default: { hit: false, tier: '' } },
  write: { db: 'biz', table: '', mode: 'update', key: [], map: [], miss: 'keep' },
  note: '',
})
