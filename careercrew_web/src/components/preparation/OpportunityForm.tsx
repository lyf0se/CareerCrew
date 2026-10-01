import { useState } from "react"
import { Download } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import type { Opportunity, OpportunityInput } from "@/lib/preparation"

/**
 * 岗位录入/编辑表单（手动录入与编辑共用）。
 * 必填：公司、岗位名称、JD；其余可选。服务端是长度与链接安全的最终校验方。
 */
export function OpportunityForm({
  initial,
  draft,
  saving,
  error,
  onCaptureJd,
  onSubmit,
  onCancel,
}: {
  initial?: Opportunity | null
  /** 采集器预填（url/title/jd 等草稿字段，不带回显完整岗位） */
  draft?: Partial<OpportunityInput> | null
  saving: boolean
  error?: string
  /** 提供后显示「从链接采集 JD」：按岗位链接抓详情页正文并回填（Boss/猎聘可用） */
  onCaptureJd?: (url: string) => Promise<string>
  onSubmit: (input: OpportunityInput) => void
  onCancel: () => void
}) {
  const [company, setCompany] = useState(draft?.company ?? initial?.company ?? "")
  const [title, setTitle] = useState(draft?.title ?? initial?.title ?? "")
  const [jd, setJd] = useState(draft?.jd ?? initial?.jd ?? "")
  const [city, setCity] = useState(draft?.city ?? initial?.city ?? "")
  const [salary, setSalary] = useState(draft?.salary ?? initial?.salary ?? "")
  const [source, setSource] = useState(draft?.source ?? initial?.source ?? "")
  const [url, setUrl] = useState(draft?.url ?? initial?.url ?? "")
  const [touched, setTouched] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [captureError, setCaptureError] = useState("")

  const companyOk = company.trim().length > 0
  const titleOk = title.trim().length > 0
  const jdOk = jd.trim().length > 0
  const valid = companyOk && titleOk && jdOk

  const handleSubmit = () => {
    setTouched(true)
    if (!valid || saving) return
    onSubmit({
      company: company.trim(),
      title: title.trim(),
      jd: jd.trim(),
      city: city.trim(),
      salary: salary.trim(),
      source: source.trim(),
      url: url.trim(),
    })
  }

  const handleCaptureJd = async () => {
    if (!onCaptureJd || capturing || !url.trim()) return
    setCapturing(true)
    setCaptureError("")
    try {
      setJd(await onCaptureJd(url.trim()))
    } catch (e) {
      setCaptureError(e instanceof Error ? e.message : "采集失败，请稍后重试或手动粘贴 JD")
    } finally {
      setCapturing(false)
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        handleSubmit()
      }}
      noValidate
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-[12.5px] text-ink-soft">
          公司 <span className="text-destructive">*</span>
          <Input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="例如：字节跳动" maxLength={200} />
          {touched && !companyOk && <span className="text-[11px] text-destructive">公司名称不能为空</span>}
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-ink-soft">
          岗位名称 <span className="text-destructive">*</span>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：大模型应用工程师" maxLength={200} />
          {touched && !titleOk && <span className="text-[11px] text-destructive">岗位名称不能为空</span>}
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-ink-soft">
          城市
          <Input value={city} onChange={(e) => setCity(e.target.value)} placeholder="例如：深圳" maxLength={200} />
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-ink-soft">
          薪资
          <Input value={salary} onChange={(e) => setSalary(e.target.value)} placeholder="例如：25-40K" maxLength={200} />
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-ink-soft">
          来源
          <Input value={source} onChange={(e) => setSource(e.target.value)} placeholder="例如：Boss直聘 / 内推" maxLength={100} />
        </label>
        <label className="flex flex-col gap-1 text-[12.5px] text-ink-soft">
          岗位链接（HTTP/HTTPS）
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" maxLength={2000} />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-[12.5px] text-ink-soft">
        <span className="flex items-center justify-between gap-2">
          <span>岗位描述 JD <span className="text-destructive">*</span></span>
          {onCaptureJd && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-[11px]"
              onClick={handleCaptureJd}
              disabled={capturing || !url.trim()}
              title={url.trim() ? "按岗位链接打开详情页抓取 JD 正文" : "请先填写岗位链接"}
            >
              <Download className="h-3 w-3" />
              {capturing ? "采集中…" : "从链接采集 JD"}
            </Button>
          )}
        </span>
        <Textarea
          value={jd}
          onChange={(e) => setJd(e.target.value)}
          placeholder="粘贴完整 JD 文本"
          className="min-h-[140px]"
        />
        <span className="text-[11px] text-ink-faint">{jd.length}/30000</span>
        {captureError && <span className="text-[11px] text-destructive">{captureError}</span>}
        {touched && !jdOk && <span className="text-[11px] text-destructive">JD 不能为空</span>}
      </label>
      {error && <p className="text-[12px] text-destructive">{error}</p>}
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={saving}>
          {saving ? "保存中…" : "保存岗位"}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          取消
        </Button>
      </div>
    </form>
  )
}
