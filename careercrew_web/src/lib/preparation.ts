import { apiFetch } from "@/lib/auth"
import { apiErrorText } from "@/lib/errors"
import type { JobOpportunity } from "@/types"

/**
 * 岗位准备 API（/api/preparation）客户端。
 * 所有列表/详情接口都以当前登录账号为 owner，服务端统一 404 隔离。
 */

export interface Opportunity {
  id: string
  company: string
  title: string
  jd: string
  city: string
  salary: string
  source: string
  url: string
  created_at: string
  updated_at: string
}

export interface OpportunityInput {
  company: string
  title: string
  jd: string
  city?: string
  salary?: string
  source?: string
  url?: string
}

export interface ResumeVersion {
  id: string
  opportunity_id: string
  label: string
  content: string
  original_content: string
  created_at: string
}

export interface PreparationSession {
  thread_id: string
  module: "resume" | "interview"
  opportunity_id: string
  resume_version_id: string
  company: string
  title: string
  jd: string
  resume_content: string
  resume_label: string
}

async function readData<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(await apiErrorText(resp))
  return resp.json() as Promise<T>
}

function post(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }
}

export async function listOpportunities(): Promise<Opportunity[]> {
  const rows = await readData<Opportunity[]>(await apiFetch("/api/preparation/opportunities"))
  return Array.isArray(rows) ? rows : []
}

export async function createOpportunity(input: OpportunityInput): Promise<Opportunity> {
  return readData(await apiFetch("/api/preparation/opportunities", post(input)))
}

/** JD 采集结果：抓取失败不抛错，用 status 表达结果，message 可直接展示。 */
export interface JdCaptureResult {
  status: "captured" | "unsupported" | "failed"
  jd: string
  message: string
}

/**
 * 按岗位链接预取 JD 正文（收藏前调用，或手动录入时一键填充）。
 * 数据库对 jd 有 NOT NULL 约束，所以取不到 JD 时不能建岗位——由调用方引导手动粘贴。
 */
export async function captureJobJd(url: string): Promise<JdCaptureResult> {
  return readData(await apiFetch("/api/preparation/capture-jd", post({ url })))
}

export async function updateOpportunity(id: string, input: OpportunityInput): Promise<Opportunity> {
  return readData(await apiFetch(`/api/preparation/opportunities/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }))
}

export async function deleteOpportunity(id: string): Promise<void> {
  await readData(await apiFetch(`/api/preparation/opportunities/${encodeURIComponent(id)}`, {
    method: "DELETE",
  }))
}

export async function listVersions(opportunityId: string): Promise<ResumeVersion[]> {
  const rows = await readData<ResumeVersion[]>(await apiFetch(
    `/api/preparation/opportunities/${encodeURIComponent(opportunityId)}/versions`))
  return Array.isArray(rows) ? rows : []
}

export async function createVersion(
  opportunityId: string,
  input: { label: string; content: string; original_content?: string },
): Promise<ResumeVersion> {
  return readData(await apiFetch(
    `/api/preparation/opportunities/${encodeURIComponent(opportunityId)}/versions`,
    post(input)))
}

/** 导出已保存版本：带认证的 blob 下载（不能直接 window.open，会丢 Authorization）。 */
export async function downloadVersion(
  opportunityId: string,
  versionId: string,
  format: "pdf" | "docx",
): Promise<Blob> {
  const resp = await apiFetch(
    `/api/preparation/opportunities/${encodeURIComponent(opportunityId)}/versions/${encodeURIComponent(versionId)}/export?format=${format}`)
  if (!resp.ok) throw new Error(await apiErrorText(resp))
  return resp.blob()
}

export async function createPrepSession(
  opportunityId: string,
  module: "resume" | "interview",
  resumeVersionId: string,
): Promise<PreparationSession> {
  return readData(await apiFetch(
    `/api/preparation/opportunities/${encodeURIComponent(opportunityId)}/sessions`,
    post({ module, resume_version_id: resumeVersionId })))
}

/** 按当前会话线程取准备上下文（不存在返回 null，页面据此隐藏岗位横幅）。 */
export async function getPrepSession(threadId: string): Promise<PreparationSession | null> {
  const resp = await apiFetch(`/api/preparation/sessions/${encodeURIComponent(threadId)}`)
  if (resp.status === 404) return null
  return readData(resp)
}

/** 一键收藏搜索结果：缺失字段原样提交，表单校验交给用户在准备页补齐。 */
export async function collectOpportunity(job: JobOpportunity): Promise<Opportunity> {
  return createOpportunity({
    company: job.company || "",
    title: job.title || "",
    jd: job.jd || "",
    city: job.city || "",
    salary: job.salary || "",
    source: job.source_label || job.source || "",
    url: job.url || "",
  })
}

/** 从历史消息 metadata 还原岗位卡片（旧记录无 metadata 自然为空数组）。 */
export function jobsFromMetadata(metadata: unknown): JobOpportunity[] {
  if (!metadata || typeof metadata !== "object") return []
  const raw = (metadata as Record<string, unknown>).jobs
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item): JobOpportunity[] => {
    if (!item || typeof item !== "object") return []
    const row = item as Record<string, unknown>
    const company = String(row.company ?? "").trim()
    const title = String(row.title ?? "").trim()
    if (!company || !title) return []
    return [{
      company,
      title,
      city: String(row.city ?? ""),
      salary: String(row.salary ?? ""),
      experience: String(row.experience ?? ""),
      source: String(row.source ?? ""),
      source_label: String(row.source_label ?? ""),
      retrieval_mode: String(row.retrieval_mode ?? ""),
      retrieval_mode_label: String(row.retrieval_mode_label ?? ""),
      matched_core_terms: Array.isArray(row.matched_core_terms)
        ? row.matched_core_terms.map(String)
        : [],
      url: String(row.url ?? ""),
      jd: String(row.jd ?? ""),
    }]
  })
}

/** 只放行 HTTP/HTTPS 绝对链接；卡片上的来源链接不安全就不渲染。 */
export function isSafeHttpUrl(url: string | undefined): boolean {
  const text = (url ?? "").trim()
  if (!text || /\s/.test(text)) return false
  try {
    const parsed = new URL(text)
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && Boolean(parsed.hostname)
  } catch {
    return false
  }
}

/** 标记投递所用简历版本（版本归因）；传空串清除。 */
export async function setAppliedVersion(opportunityId: string, versionId: string): Promise<void> {
  const resp = await apiFetch(
    `/api/career/opportunities/${encodeURIComponent(opportunityId)}/applied-version`,
    { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ applied_version_id: versionId }) })
  if (!resp.ok) throw new Error(await apiErrorText(resp))
}
