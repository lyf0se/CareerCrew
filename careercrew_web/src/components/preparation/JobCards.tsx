import { useState } from "react"
import { Link } from "react-router-dom"
import { BookmarkCheck, BookmarkPlus, ChevronDown, ChevronUp, ExternalLink } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { captureJobJd, collectOpportunity, isSafeHttpUrl } from "@/lib/preparation"
import { networkErrorText } from "@/lib/errors"
import type { JobOpportunity } from "@/types"

/**
 * 匹配回答下方的结构化岗位卡片（仅来自成功 search_jobs 工具的结构化结果，
 * 绝不解析 LLM 正文）。收藏动作把该岗位写入"岗位准备"，自动携带 JD 快照。
 *
 * 搜索列表页拿不到 JD 正文（榜单接口不返回），因此收藏时按岗位链接抓取一次详情页：
 * 数据库对 jd 有 NOT NULL 约束，必须先取到 JD 才能建岗位，取不到则引导手动粘贴。
 */
function JobCard({ job }: { job: JobOpportunity }) {
  const [expandJd, setExpandJd] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedId, setSavedId] = useState<string | null>(null)
  const [error, setError] = useState("")
  /** 搜索结果里没有 JD，收藏时抓到的正文在这里回显给用户确认 */
  const [capturedJd, setCapturedJd] = useState("")

  const jobUrl = job.url || ""
  const safeUrl = isSafeHttpUrl(jobUrl)
  const jd = job.jd || capturedJd

  const handleCollect = async () => {
    if (saving || savedId) return
    setSaving(true)
    setError("")
    try {
      let resolvedJd: string = job.jd || ""
      if (!resolvedJd.trim()) {
        if (!safeUrl) {
          throw new Error("该岗位缺少来源链接，无法自动采集 JD，请改用「岗位准备 → 手动录入岗位」")
        }
        const captured = await captureJobJd(jobUrl)
        if (captured.status !== "captured" || !captured.jd) {
          throw new Error(captured.message || "未能采集到 JD 正文，请稍后重试或手动粘贴")
        }
        resolvedJd = captured.jd
        setCapturedJd(captured.jd)
      }
      const saved = await collectOpportunity({ ...job, jd: resolvedJd })
      setSavedId(saved.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : networkErrorText(e, "收藏失败，请稍后重试"))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-[10px] border border-[var(--border-soft)] bg-card p-3.5 text-[13px]">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-[560] text-ink">{job.company}</span>
        <span className="text-ink">· {job.title}</span>
        {job.salary && <span className="text-ink-soft">{job.salary}</span>}
        {job.city && <span className="text-ink-soft">{job.city}</span>}
        {job.retrieval_mode_label && (
          <Badge variant="outline" className="text-[10.5px]">{job.retrieval_mode_label}</Badge>
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11.5px] text-ink-faint">
        {job.source_label && <span>来源：{job.source_label}</span>}
        {job.experience && <span>经验：{job.experience}</span>}
        {safeUrl && (
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="inline-flex items-center gap-0.5 hover:text-ink"
            onClick={(e) => e.stopPropagation()}
          >
            来源链接 <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>
      {jd ? (
        <div className="mt-2 text-[12.5px] leading-relaxed text-ink-soft">
          {!job.jd && <span className="text-[11.5px] text-emerald-600">已采集 JD 全文 · </span>}
          <p className={expandJd ? "whitespace-pre-wrap break-words" : "line-clamp-2 whitespace-pre-wrap break-words"}>
            {jd}
          </p>
          <button
            type="button"
            className="mt-1 inline-flex items-center gap-0.5 text-[11.5px] text-ink-faint hover:text-ink"
            onClick={() => setExpandJd((v) => !v)}
          >
            {expandJd ? <>收起 JD <ChevronUp className="h-3 w-3" /></> : <>展开 JD <ChevronDown className="h-3 w-3" /></>}
          </button>
        </div>
      ) : (
        <p className="mt-2 text-[12px] text-ink-faint">
          {safeUrl
            ? "搜索结果不含 JD 全文，点「收藏岗位」时会自动打开岗位详情页采集。"
            : "该岗位没有来源链接，无法自动采集 JD：请用「岗位准备 → 手动录入岗位」保存。"}
        </p>
      )}

      <div className="mt-2.5 flex items-center gap-2">
        {savedId ? (
          <>
            <span className="inline-flex items-center gap-1 text-[12px] text-emerald-600">
              <BookmarkCheck className="h-3.5 w-3.5" /> 已收藏
            </span>
            <Button asChild variant="outline" size="sm" className="h-[26px] text-[12px]">
              <Link to={`/preparation?opportunity=${encodeURIComponent(savedId)}`}>去准备</Link>
            </Button>
          </>
        ) : (
          <Button
            variant="outline"
            size="sm"
            className="h-[26px] text-[12px]"
            disabled={saving}
            onClick={handleCollect}
          >
            <BookmarkPlus className="h-3.5 w-3.5" />
            {saving ? (job.jd ? "收藏中…" : "采集 JD 中…") : "收藏岗位"}
          </Button>
        )}
        {error && <span className="text-[11.5px] text-destructive">{error}</span>}
      </div>
    </div>
  )
}

export function JobCards({ jobs }: { jobs: JobOpportunity[] }) {
  if (!jobs.length) return null
  return (
    <div className="mt-2 flex flex-col gap-2" data-testid="job-cards">
      {jobs.map((job, i) => (
        <JobCard key={`${job.source}-${job.url || job.company}-${i}`} job={job} />
      ))}
    </div>
  )
}
