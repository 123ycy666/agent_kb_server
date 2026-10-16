import { http } from './client'

// ══════════════════════════════════════════════════════════════
//  类型
// ══════════════════════════════════════════════════════════════

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface KnowledgeBase {
  id: number
  name: string
  description?: string
  embedding_model: string
  embedding_dim: number
  chunk_size: number
  chunk_overlap: number
  status: string
  /** 是否参与「智能问答」的默认检索范围（2026-10-15 新增）
   *
   * 用「库上打标记」而不是「配置里写 id 列表」：自增 id 会因删库/建库漂移
   * （删除不复用、新建拿更大值），写死的配置会悬空或失效。 */
  qa_enabled: boolean
  created_at: string
  updated_at: string
  document_count: number
  chunk_count: number
  visible_document_count: number
}

export interface DocItem {
  id: number
  kb_id: number
  root_id?: number
  version_no: number
  is_active: boolean
  filename: string
  file_ext: string
  file_size: number
  content_hash: string
  parse_status: string
  parse_error?: string
  has_conflict: boolean
  chunk_count: number
  /** 服务端落盘的绝对路径（排障用） */
  storage_path: string
  /** public = 所有人可见可检索；private = 仅上传者本人（管理员管理视图可见） */
  visibility: 'public' | 'private'
  owner_id?: number
  uploaded_by_app?: string
  created_at: string
  updated_at: string
  /** 自由标签，如 ['行政', '预算'] */
  tags: string[]
  conflict_count: number
}

/** 标签词表条目。标签本身是自由字符串，此表只用于「下拉复用 + 使用统计」。 */
export interface Tag {
  id: number
  name: string
  description?: string | null
  usage_count: number
  created_at: string
}

/** 通用操作响应。
 *
 * ⚠️ `warnings` 不是可有可无的装饰：删除文档时后端会把「自动回滚复活了哪个旧版本」
 *    「其向量模型已过期需重建」放在这里，必须展示给用户。
 */
export interface OkResult {
  ok: boolean
  message: string
  warnings: string[]
}

/** 上传响应。
 *
 * ⚠️ 后端上传已改为**异步受理**：POST 立刻返回 202 + task_id，
 *    doc_id 与冲突数要等后台任务跑完才写入 task.result。
 *    要拿最终结果请用 `docApi.uploadAndWait`（内部已封装轮询）。
 */
export interface UploadResult {
  task_id: string
  status: string
  message: string
  doc_id?: number
  chunk_count?: number
  /** >0 表示该文档已被挂起（暂不参与检索），需到「冲突管理」处理 */
  conflict_count: number
  /** 与之冲突的已有文档名 */
  conflict_docs: string[]
  warnings: string[]
  /** status=failed 时的失败原因 */
  error_msg?: string | null
  /** 冲突检测状态：checking=后台判定中（文档已入库可检索）/ done / failed */
  conflict_status?: 'checking' | 'done' | 'failed'
}

export interface Role {
  id: number
  name: string
  description?: string
  is_builtin: boolean
  created_at: string
  permission_codes: string[]
  user_count: number
}

export interface Permission {
  id: number
  code: string
  description?: string
  group_name: string
}

export interface AppUser {
  id: number
  username: string
  display_name?: string
  email?: string
  status: string
  external_id?: string
  created_at?: string
  role_ids: number[]
  role_names: string[]
  // ── t_employee 员工字段（HR 权威，只读展示）──────────────
  employee_id?: number
  actual_name?: string
  phone?: string
  department_id?: number
  administrator_flag?: boolean
  disabled_flag?: boolean
  deleted_flag?: boolean
  /** 是否已有本地投影（登录过）；false = 尚未登录 */
  projected?: boolean
}

export interface ConflictItem {
  id: number
  kb_id: number
  new_doc_id: number
  old_doc_id: number
  conflict_type: string
  relation: string
  similarity_score?: number
  rerank_score?: number
  reason?: string
  new_snippet?: string
  old_snippet?: string
  resolution?: string
  status: string
  created_at: string
  new_doc_name?: string
  old_doc_name?: string
  old_doc_version?: number
}

export interface AuditLog {
  id: number
  actor_type: string
  actor_id?: number
  actor_name?: string
  actor_app_id?: string
  action: string
  resource_type?: string
  resource_id?: number
  resource_name?: string
  detail?: Record<string, unknown>
  result: string
  ip?: string
  created_at: string
}

export interface TaskItem {
  id: string
  task_type: string
  ref_id?: number
  kb_id?: number
  status: string
  progress: number
  stage?: string
  error_msg?: string
  result?: Record<string, any>
  created_by_app?: string
  created_at: string
  started_at?: string
  finished_at?: string
}

export interface SearchResult {
  chunk_id: number
  doc_id: number
  doc_name: string
  kb_id: number
  kb_name?: string
  version_no: number
  heading_path?: string
  content: string
  /** parent-child：命中小块所属的父块上下文（同章节聚合），用于更完整的问答上下文 */
  parent_content?: string
  /** vector | keyword | hybrid —— 该结果由哪一路（或两路）召回 */
  source: string
  vector_score?: number
  keyword_score?: number
  fused_score?: number
  rerank_score?: number
  has_conflict: boolean
  tags: string[]
}

export interface FilterNotice {
  message: string
  suggestion?: string
  relaxable_filters: string[]
  baseline_total: number
}

export interface SearchResponse {
  query: string
  total: number
  results: SearchResult[]
  took_ms: number
  recall_count: number
  filter_notice?: FilterNotice
  degraded: boolean
  degraded_reason?: string
}

export interface ProviderConfig {
  mode: string
  mode_label: string
  embedding: { kind: string; mode: string; model_id: string; ready: boolean; detail: string }
  rerank: { kind: string; mode: string; model_id: string; ready: boolean; detail: string }
  llm: { kind: string; mode: string; model_id: string; ready: boolean; detail: string }
  embedding_dim: number
  options: Record<string, any>
  notice: string
}

export interface ProviderTestResult {
  target: string
  ok: boolean
  provider: string
  model: string
  message: string
  latency_ms: number
}

// ══════════════════════════════════════════════════════════════
//  认证
// ══════════════════════════════════════════════════════════════

export const authApi = {
  login: (username: string, password: string) =>
    http.post<any, { access_token: string; user: any; roles: string[]; permissions: string[] }>(
      '/auth/login',
      { username, password }
    ),
  me: () => http.get<any, any>('/auth/me'),
}

// ══════════════════════════════════════════════════════════════
//  知识库
// ══════════════════════════════════════════════════════════════

export const kbApi = {
  list: (page = 1, pageSize = 50) =>
    http.get<any, Page<KnowledgeBase>>('/kb', { params: { page, page_size: pageSize } }),
  get: (id: number) => http.get<any, KnowledgeBase>(`/kb/${id}`),
  create: (data: Partial<KnowledgeBase>) => http.post<any, KnowledgeBase>('/kb', data),
  update: (id: number, data: Partial<KnowledgeBase>) =>
    http.patch<any, KnowledgeBase>(`/kb/${id}`, data),
  remove: (id: number) => http.delete<any, any>(`/kb/${id}`),
  stats: (id: number) => http.get<any, any>(`/kb/${id}/stats`),
  changeEmbeddingModel: (id: number, data: { embedding_model: string; embedding_dim: number; rebuild: boolean }) =>
    http.post<any, any>(`/kb/${id}/embedding-model`, data),
  /** 重建索引：切换向量模型后必做，否则检索会静默失效 */
  reindex: (id: number, onlyStale = true) =>
    http.post<any, any>(`/kb/${id}/reindex`, null, {
      params: { only_stale: onlyStale },
    }),
}

// ══════════════════════════════════════════════════════════════
//  文档
// ══════════════════════════════════════════════════════════════

export const docApi = {
  list: (kbId: number, params: Record<string, unknown> = {}) =>
    http.get<any, Page<DocItem>>(`/kb/${kbId}/documents`, { params }),
  get: (id: number) => http.get<any, DocItem>(`/documents/${id}`),
  chunks: (id: number, limit = 50) =>
    http.get<any, any[]>(`/documents/${id}/chunks`, { params: { limit } }),
  versions: (id: number) => http.get<any, any[]>(`/documents/${id}/versions`),
  remove: (id: number) => http.delete<any, OkResult>(`/documents/${id}`),
  /** 把某个历史版本恢复为当前活跃版本（版本历史里点「恢复」用） */
  restore: (id: number) => http.post<any, OkResult>(`/documents/${id}/restore`),
  /** 覆盖式设置标签；传空数组表示清空 */
  setTags: (id: number, tags: string[]) =>
    http.post<any, DocItem>(`/documents/${id}/tags`, { tags }),
  removeTag: (id: number, name: string) =>
    http.delete<any, DocItem>(`/documents/${id}/tags/${encodeURIComponent(name)}`),
  /** 切换公开 / 私有（仅上传者本人或管理员可操作） */
  setVisibility: (id: number, visibility: 'public' | 'private') =>
    http.patch<any, DocItem>(`/documents/${id}/visibility`, { visibility }),
  /**
   * 下载原文件。
   * ⚠️ 必须用 blob 拉取后触发下载 —— 原生 <a href> 带不上 Authorization 头，
   *    会直接 401。这是「带鉴权下载」的通用写法。
   */
  download: async (id: number, filename: string) => {
    const blob = await http.get<any, Blob>(`/documents/${id}/download`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },
  /**
   * 预览原文件（内联返回）。
   *
   * ⚠️ 与下载同理：**必须用 blob 拉取** —— `<iframe src>` / `<img src>` 带不上
   *    Authorization 头，直链会 401。后端 `/content` 返回 `inline`，不触发下载。
   *
   * @returns objectURL 与撤销函数；用完务必调用 revoke()
   */
  preview: async (id: number): Promise<{ url: string; revoke: () => void }> => {
    const blob = await http.get<any, Blob>(`/documents/${id}/content`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(blob)
    return { url, revoke: () => URL.revokeObjectURL(url) }
  },
  upload: (kbId: number, file: File, isPublic: boolean, tags: string[]) => {
    const form = new FormData()
    form.append('file', file)
    form.append('is_public', isPublic ? 'true' : 'false')
    form.append('tags', JSON.stringify(tags))
    return http.post<any, UploadResult>(`/kb/${kbId}/documents`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /** 上传并**等待后台任务完成**，返回最终结果。
   *
   * ⚠️ 为什么必须有它：上传接口现在只返回 202 + task_id（立即返回，实测 25ms）。
   *    直接用 `upload` 拿到的 conflict_count 恒为 0、doc_id 为 undefined，
   *    会让「检出冲突」这类提示静默失效 —— 功能看似正常，实际从未生效。
   *
   * @param onTick 每次轮询回调（可传进度，用于展示）
   */
  uploadAndWait: async (
    kbId: number,
    file: File,
    isPublic: boolean,
    tags: string[],
    onTick?: (pct: number) => void,
  ): Promise<UploadResult> => {
    const accepted = await docApi.upload(kbId, file, isPublic, tags)
    const deadline = Date.now() + 10 * 60 * 1000

    // ⚠️ 两段式（2026-10-15）：后端只要「解析 + 向量化入库」完成就置 done，
    //    文档此刻**已可检索**；冲突检测要真调 LLM（数十秒），在后台继续跑，
    //    结论写回**同一条任务**（result.conflict_status）。
    //    这里最多额外等 conflictWaitMs 让冲突结论搭同一趟车回来；等不到就先把
    //    「已入库」结果交给用户 —— 不再让上传弹窗卡在冲突判定上。
    const conflictWaitMs = 45 * 1000
    let doneAt: number | null = null

    const pack = (t: any, cs: UploadResult['conflict_status']): UploadResult => {
      const result = (t.result ?? {}) as Record<string, any>
      return {
        task_id: accepted.task_id,
        status: t.status,
        message: accepted.message,
        doc_id: result.doc_id,
        chunk_count: result.chunk_count,
        conflict_count: result.conflict_count ?? 0,
        conflict_docs: result.conflict_docs ?? [],
        warnings: result.note ? [String(result.note)] : [],
        error_msg: t.error_msg,
        conflict_status: cs,
      }
    }

    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 700))
      const t = await taskApi.get(accepted.task_id)
      onTick?.(t.progress ?? 0)

      if (t.status === 'failed') return pack(t, 'failed')
      if (t.status !== 'done') continue

      const cs = (((t.result ?? {}) as Record<string, any>).conflict_status ??
        'done') as UploadResult['conflict_status']
      if (cs !== 'checking') return pack(t, cs)

      if (doneAt === null) doneAt = Date.now()
      if (Date.now() - doneAt > conflictWaitMs) return pack(t, 'checking')
    }
    throw new Error('任务超时未完成，请到「任务」列表查看')
  },
}

// ══════════════════════════════════════════════════════════════
//  标签
// ══════════════════════════════════════════════════════════════

export const tagApi = {
  list: (keyword?: string) =>
    http.get<any, Page<Tag>>('/tags', { params: { keyword, page_size: 300 } }),
  create: (data: { name: string; description?: string }) =>
    http.post<any, Tag>('/tags', data),
  update: (id: number, data: { description?: string }) =>
    http.patch<any, Tag>(`/tags/${id}`, data),
  /** 重命名：会同步改写所有文档与切片上的标签值 */
  rename: (oldName: string, newName: string) =>
    http.post<any, any>('/tags/rename', { old_name: oldName, new_name: newName }),
  /** 删除：会从所有文档上摘除该标签 */
  remove: (name: string) =>
    http.delete<any, any>(`/tags/${encodeURIComponent(name)}`),
  /** 检索页的标签筛选可选项 */
  options: () =>
    http.get<any, { options: { name: string; usage_count: number }[] }>('/tags/options'),
}

// ══════════════════════════════════════════════════════════════
//  检索
// ══════════════════════════════════════════════════════════════

export const searchApi = {
  search: (data: {
    query: string
    kb_ids: number[]
    /** 标签过滤（全部命中），如 ['行政', '预算'] */
    tag_filters?: string[]
    top_k?: number
    rerank?: boolean
  }) => http.post<any, SearchResponse>('/search', data),
}

// ══════════════════════════════════════════════════════════════
//  冲突
// ══════════════════════════════════════════════════════════════

export const conflictApi = {
  list: (params: Record<string, unknown> = {}) =>
    http.get<any, Page<ConflictItem>>('/conflicts', { params }),
  stats: (kbId?: number) =>
    http.get<any, any>('/conflicts/stats', { params: { kb_id: kbId } }),
  detect: (docId: number) =>
    http.post<any, any>('/conflicts/detect', { doc_id: docId }),
  resolve: (id: number, resolution: string, note?: string) =>
    http.post<any, any>(`/conflicts/${id}/resolve`, { resolution, note }),
  /**
   * 全库一致性扫描（管理员）。
   * ⚠️ 会覆盖全部文档（含你的不可见文档），用于补上日常检测的盲区。
   */
  fullScan: (kbId: number, onlyUnscanned = true) =>
    http.post<any, any>('/conflicts/scan', {
      kb_id: kbId,
      only_unscanned: onlyUnscanned,
    }),
}

// ══════════════════════════════════════════════════════════════
//  任务
// ══════════════════════════════════════════════════════════════

export const taskApi = {
  list: (params: Record<string, unknown> = {}) =>
    http.get<any, Page<TaskItem>>('/tasks', { params }),
  get: (id: string) => http.get<any, TaskItem>(`/tasks/${id}`),
}

// ══════════════════════════════════════════════════════════════
//  权限
// ══════════════════════════════════════════════════════════════

// ⚠️ 本系统只有 admin / user 两种固定角色，不提供「创建 / 修改 / 删除角色」接口。
// ⚠️ 2026-10-14 统一用户表后用户管理收敛为只读：员工由 HR 系统维护，
//    createUser / updateUser 已随之后端一并移除（改角色请到 HR 系统调整 administrator_flag）。
export const rbacApi = {
  permissions: () => http.get<any, Permission[]>('/permissions'),
  roles: () => http.get<any, Role[]>('/roles'),
  users: () => http.get<any, AppUser[]>('/users'),
}

// ══════════════════════════════════════════════════════════════
//  审计
// ══════════════════════════════════════════════════════════════

export const auditApi = {
  list: (params: Record<string, unknown> = {}) =>
    http.get<any, Page<AuditLog>>('/audit-logs', { params }),
  stats: () => http.get<any, any>('/audit-logs/stats'),
  actions: () => http.get<any, any[]>('/audit-logs/actions'),
}

// ══════════════════════════════════════════════════════════════
//  模型
// ══════════════════════════════════════════════════════════════

export const modelApi = {
  config: () => http.get<any, ProviderConfig>('/models/config'),
  test: (data: Record<string, unknown>) =>
    http.post<any, { mode: string; results: ProviderTestResult[]; all_ok: boolean }>(
      '/models/test',
      data
    ),
  switchMode: (mode: string) => http.post<any, ProviderConfig>('/models/switch', { mode }),
}

// ══════════════════════════════════════════════════════════════
//  系统
// ══════════════════════════════════════════════════════════════

export const systemApi = {
  health: () => http.get<any, any>('/system/health'),
  formats: () => http.get<any, any>('/system/formats'),
  info: () => http.get<any, any>('/system/info'),
}
