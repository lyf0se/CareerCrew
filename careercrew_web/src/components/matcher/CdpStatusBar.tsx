import { useEffect, useState } from "react"
import { Play, Copy, Check, RefreshCw, AlertCircle, CheckCircle2, Loader2, Terminal, Settings } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { apiFetch } from "@/lib/auth"
import { CdpLaunchDialog } from "./CdpLaunchDialog"

/**
 * 采集专用 Chrome 的一次性设置指引。
 * commands 按平台给出多份：后端可能在 Linux 容器里、而用户坐在 Windows 上，
 * 由前端按客户端平台挑选，避免给错命令。
 */
export interface CollectorSetup {
  port: number
  commands: Record<string, string>
  user_data_dirs: Record<string, string>
  default_platform: string
  steps: string[]
}

export interface CdpStatus {
  connected: boolean
  cdp_url: string
  boss_opened: boolean
  liepin_opened: boolean
  tab_count: number
  command: string
  bat_path: string
  message: string
  setup?: CollectorSetup
}

/** 采集器接口不可用时的原因说明（403 多出现在容器部署未放宽来源校验的情况）。 */
function unavailableReason(httpStatus: number): string {
  if (httpStatus === 403) {
    return "后端拒绝了采集器请求（仅支持本机访问）。容器部署需要把 tools.browser.local_guard 设为 container，并把发布端口绑定到 127.0.0.1"
  }
  if (httpStatus === 401) return "登录状态已失效，请重新登录后再试"
  return `采集器接口返回 ${httpStatus}，暂时无法检测采集器状态`
}

interface CdpStatusBarProps {
  /** 紧凑条模式（顶部浮动），或卡片模式（EmptyState 主体） */
  variant?: "banner" | "card"
  onToast?: (msg: string) => void
}

export function CdpStatusBar({ variant = "card", onToast }: CdpStatusBarProps) {
  const [status, setStatus] = useState<CdpStatus | null>(null)
  const [loadError, setLoadError] = useState("")
  const [loading, setLoading] = useState(false)
  const [launching, setLaunching] = useState(false)
  const [copied, setCopied] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)

  const fetchStatus = async () => {
    setLoading(true)
    try {
      const res = await apiFetch("/api/browser/cdp-status")
      if (res.ok) {
        const data = (await res.json()) as CdpStatus
        setStatus(data)
        setLoadError("")
      } else {
        setStatus(null)
        setLoadError(unavailableReason(res.status))
      }
    } catch {
      setStatus(null)
      setLoadError("无法连接后端，采集器状态未知")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStatus()
  }, [])

  const handleLaunch = async () => {
    setDialogOpen(true)
    setLaunching(true)
    try {
      const res = await apiFetch("/api/browser/launch-cdp", { method: "POST" })
      const data = await res.json()
      onToast?.(data.message || "已尝试启动 Chrome 采集器")
      // 连续轮询 5 次，直到连通
      let attempts = 0
      const timer = setInterval(async () => {
        attempts += 1
        try {
          const checkRes = await apiFetch("/api/browser/cdp-status")
          if (checkRes.ok) {
            const checkData = (await checkRes.json()) as CdpStatus
            setStatus(checkData)
            if (checkData.connected || attempts >= 5) {
              clearInterval(timer)
              setLaunching(false)
            }
          }
        } catch {
          if (attempts >= 5) {
            clearInterval(timer)
            setLaunching(false)
          }
        }
      }, 1500)
    } catch {
      onToast?.("启动请求失败，请手动运行脚本")
      setLaunching(false)
    }
  }

  const handleCopy = () => {
    const cmd = status?.command || "powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1"
    navigator.clipboard.writeText(cmd).then(() => {
      setCopied(true)
      onToast?.("已复制启动命令到剪贴板")
      setTimeout(() => setCopied(false), 2000)
    })
  }

  // 状态未拿到时不能静默 return null：整块功能凭空消失会让用户完全无从判断原因。
  // 这里明确渲染"不可用 + 原因 + 设置引导"，把失败暴露出来。
  if (!status) {
    const reason = loadError || (loading ? "正在检测采集器状态…" : "采集器状态未知")
    const unavailableDialog = (
      <CdpLaunchDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        status={status}
        unavailableReason={loadError}
        onStatusUpdate={setStatus}
        onToast={onToast}
      />
    )

    if (variant === "banner") {
      return (
        <>
          <div className="flex items-center justify-between border-b border-border/50 bg-amber-500/5 px-4 py-2 text-xs">
            <div className="flex min-w-0 items-center gap-2">
              <AlertCircle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
              <span className="truncate text-muted-foreground">实时采集器不可用：{reason}</span>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <Button size="sm" variant="ghost" className="h-6 gap-1 px-2 text-xs" onClick={() => setDialogOpen(true)}>
                <Settings className="h-3 w-3" />
                设置引导
              </Button>
              <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-muted-foreground" onClick={fetchStatus} disabled={loading} title="重新检测">
                <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
              </Button>
            </div>
          </div>
          {unavailableDialog}
        </>
      )
    }

    return (
      <>
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardContent className="p-4 sm:p-5">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
                  <AlertCircle className="h-5 w-5" />
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-foreground">实时采集器不可用</h4>
                  <p className="text-xs text-muted-foreground">{reason}</p>
                </div>
              </div>
              <Button size="sm" variant="ghost" className="h-7 w-7 shrink-0 p-0 text-muted-foreground" onClick={fetchStatus} disabled={loading} title="重新检测">
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              </Button>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button size="sm" variant="outline" className="h-7 gap-1.5 text-xs" onClick={() => setDialogOpen(true)}>
                <Settings className="h-3.5 w-3.5" />
                查看设置引导
              </Button>
              <span className="text-[11px] text-muted-foreground">
                修复前仍可手动录入岗位，或在岗位准备里粘贴 JD
              </span>
            </div>
          </CardContent>
        </Card>
        {unavailableDialog}
      </>
    )
  }

  // 1) 紧凑横条模式（顶部常驻）
  if (variant === "banner") {
    return (
      <>
        <div className="flex items-center justify-between border-b border-border/50 bg-muted/40 px-4 py-2 text-xs">
          <div className="flex items-center gap-2">
            {status.connected ? (
              <>
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                </span>
                <span className="font-medium text-foreground/90">Chrome 实时采集器已就绪</span>
                <span className="text-muted-foreground">({status.cdp_url})</span>
                <Badge variant="outline" className={`h-4 text-[10px] ${status.boss_opened ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}`}>
                  Boss直聘 {status.boss_opened ? "已连接" : "待打开"}
                </Badge>
                <Badge variant="outline" className={`h-4 text-[10px] ${status.liepin_opened ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}`}>
                  猎聘 {status.liepin_opened ? "已连接" : "待打开"}
                </Badge>
              </>
            ) : (
              <>
                <AlertCircle className="h-3.5 w-3.5 text-amber-500" />
                <span className="text-muted-foreground">实时采集器未启动 (端口 9222)</span>
              </>
            )}
          </div>

          <div className="flex items-center gap-1.5">
            {!status.connected && (
              <>
                <Button size="sm" variant="default" className="h-6 gap-1 px-2.5 text-xs" onClick={handleLaunch} disabled={launching}>
                  {launching ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3 fill-current" />}
                  一键启动
                </Button>
                <Button size="sm" variant="outline" className="h-6 gap-1 px-2 text-xs" onClick={handleCopy}>
                  {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                  复制命令
                </Button>
              </>
            )}
            <Button size="sm" variant="ghost" className="h-6 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground" onClick={() => setDialogOpen(true)} title="打开采集器配置弹窗">
              <Settings className="h-3 w-3" />
              配置
            </Button>
            <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground" onClick={fetchStatus} disabled={loading}>
              <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        <CdpLaunchDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          status={status}
          onStatusUpdate={setStatus}
          onToast={onToast}
        />
      </>
    )
  }

  // 2) 卡片模式（EmptyState 主体区域）
  return (
    <>
      <Card className={`overflow-hidden border transition-all ${status.connected ? "border-emerald-500/30 bg-emerald-500/5 dark:bg-emerald-950/10" : "border-border/80 bg-card shadow-sm"}`}>
        <CardContent className="p-4 sm:p-5">
          <div className="flex flex-col gap-3">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2">
                {status.connected ? (
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                    <CheckCircle2 className="h-5 w-5" />
                  </div>
                ) : (
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
                    <Terminal className="h-5 w-5" />
                  </div>
                )}
                <div>
                  <h4 className="text-sm font-semibold text-foreground">
                    {status.connected ? "Chrome 实时采集器：已就绪" : "Boss直聘与猎聘实时采集器：未启动"}
                  </h4>
                  <p className="text-xs text-muted-foreground">
                    {status.connected
                      ? "已通过 CDP 成功接管已登录的 Chrome 浏览器，支持双平台秒级实时抓取与去重。"
                      : "由于平台反爬限制，实时抓取需连接您本地已登录的 Chrome 浏览器。未启动时将优先读取历史岗位库。"}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-1">
                <Button size="sm" variant="ghost" className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground" onClick={() => setDialogOpen(true)} title="打开采集器配置弹窗">
                  <Settings className="h-3.5 w-3.5" />
                  弹窗配置
                </Button>
                <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground" onClick={fetchStatus} disabled={loading} title="刷新连接状态">
                  <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                </Button>
              </div>
            </div>

            {status.connected ? (
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <span className="text-xs text-muted-foreground">已连接调试端口：<code className="rounded bg-muted px-1.5 py-0.5 text-foreground">{status.cdp_url}</code></span>
                <Badge variant="outline" className={`text-xs ${status.boss_opened ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}`}>
                  Boss直聘 {status.boss_opened ? "✓ 标签页已打开" : "待打开"}
                </Badge>
                <Badge variant="outline" className={`text-xs ${status.liepin_opened ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}`}>
                  猎聘 {status.liepin_opened ? "✓ 标签页已打开" : "待打开"}
                </Badge>
              </div>
            ) : (
              <div className="flex flex-col gap-3 rounded-lg border border-border/60 bg-muted/30 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                    <span className="text-foreground/70">$</span>
                    <span className="truncate max-w-[280px] sm:max-w-md">{status.command}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="outline" className="h-7 gap-1 px-2.5 text-xs" onClick={handleCopy}>
                      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                      {copied ? "已复制" : "复制命令"}
                    </Button>
                    <Button size="sm" variant="default" className="h-7 gap-1.5 px-3 text-xs shadow-sm" onClick={handleLaunch} disabled={launching}>
                      {launching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5 fill-current" />}
                      一键启动 Chrome
                    </Button>
                  </div>
                </div>

                <div className="flex items-center gap-3 border-t border-border/40 pt-2 text-[11px] text-muted-foreground">
                  <span>使用提示：</span>
                  <span>① 点击一键启动打开引导弹窗</span>
                  <span>·</span>
                  <span>② 在 Chrome 中分别登录 Boss直聘 与 猎聘</span>
                  <span>·</span>
                  <span>③ 保持打开即可直接在此检索</span>
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <CdpLaunchDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        status={status}
        onStatusUpdate={setStatus}
        onToast={onToast}
      />
    </>
  )
}
