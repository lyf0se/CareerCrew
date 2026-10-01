import { useEffect, useState } from "react"
import { Play, Copy, Check, RefreshCw, CheckCircle2, AlertCircle, ExternalLink, X, Terminal, Globe } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { apiFetch } from "@/lib/auth"
import type { CdpStatus } from "./CdpStatusBar"

/**
 * 后端状态接口不可达时（403 等）的兜底文案：此时拿不到后端生成的 setup，
 * 但用户仍需要看到"怎么在本机把采集浏览器起来"。正常路径以后端 setup 为准。
 */
const DEFAULT_SETUP_STEPS = [
  "在本机新建一个 Chrome 快捷方式（Windows：桌面右键 → 新建 → 快捷方式），位置填下面的启动命令",
  "用这个快捷方式打开 Chrome，在弹出的窗口里登录 Boss直聘 与 猎聘（只需一次，登录态会保存在专用数据目录）",
  "让这个窗口保持开着，回到本应用即可自动采集岗位与 JD",
]

/** 状态接口不可达时也至少给出一条可用的启动命令（与 README 的说明一致）。 */
const FALLBACK_SETUP_COMMANDS: Record<string, string> = {
  windows: '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\\ChromeDevData https://www.zhipin.com https://www.liepin.com',
  macos: '"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir=~/ChromeDevData https://www.zhipin.com https://www.liepin.com',
  linux: "google-chrome --remote-debugging-port=9222 --user-data-dir=~/.careercrew-chrome https://www.zhipin.com https://www.liepin.com",
}

/** 按用户浏览器所在平台挑命令——后端可能在 Linux 容器里，不能拿后端平台当准。 */
function clientPlatform(): string {
  const ua = (navigator.userAgent || "").toLowerCase()
  if (ua.includes("windows")) return "windows"
  if (ua.includes("mac os") || ua.includes("macintosh")) return "macos"
  return "linux"
}

interface CdpLaunchDialogProps {
  open: boolean
  onClose: () => void
  status: CdpStatus | null
  /** 状态接口不可用时的原因（403 等），与"未启动"是两回事，必须分开说明 */
  unavailableReason?: string
  onStatusUpdate: (status: CdpStatus) => void
  onToast?: (msg: string) => void
}

export function CdpLaunchDialog({
  open,
  onClose,
  status,
  unavailableReason,
  onStatusUpdate,
  onToast,
}: CdpLaunchDialogProps) {
  const [copied, setCopied] = useState(false)
  const [checking, setChecking] = useState(false)
  const [launching, setLaunching] = useState(false)
  /** 后端不在宿主机（容器部署）时一键唤起必然失败，直接切到手动设置说明 */
  const [manualOnly, setManualOnly] = useState(false)

  const checkStatus = async () => {
    setChecking(true)
    try {
      const res = await apiFetch("/api/browser/cdp-status")
      if (res.ok) {
        const data = (await res.json()) as CdpStatus
        onStatusUpdate(data)
      }
    } catch {
      // 保持旧状态
    } finally {
      setChecking(false)
    }
  }

  const handleLaunch = async () => {
    setLaunching(true)
    try {
      const res = await apiFetch("/api/browser/launch-cdp", { method: "POST" })
      const data = await res.json()
      onToast?.(data.message || "已尝试启动 Chrome 采集器")
      // 容器部署下后端无法代启宿主机浏览器：不再空轮询，直接给出本机设置步骤
      if (data.status === "manual_required" || data.status === "error") {
        setManualOnly(true)
        setLaunching(false)
        return
      }
      // 轮询几次检测端口
      let count = 0
      const timer = setInterval(async () => {
        count += 1
        try {
          const checkRes = await apiFetch("/api/browser/cdp-status")
          if (checkRes.ok) {
            const checkData = (await checkRes.json()) as CdpStatus
            onStatusUpdate(checkData)
            if (checkData.connected || count >= 5) {
              clearInterval(timer)
              setLaunching(false)
            }
          }
        } catch {
          if (count >= 5) {
            clearInterval(timer)
            setLaunching(false)
          }
        }
      }, 1500)
    } catch {
      onToast?.("启动请求失败，请改用下面的本机设置步骤")
      setManualOnly(true)
      setLaunching(false)
    }
  }

  const setup = status?.setup
  // 优先"本机启动采集浏览器"的完整命令（容器部署下唯一可行的方式），
  // 平台以客户端为准：后端在容器里时用它的平台会给出错误的命令。
  const platform = clientPlatform()
  const setupCommand =
    setup?.commands?.[platform]
    ?? setup?.commands?.[setup?.default_platform ?? ""]
    ?? FALLBACK_SETUP_COMMANDS[platform]
    ?? FALLBACK_SETUP_COMMANDS.linux
  const copyTarget = setupCommand || status?.command || "powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1"

  const handleCopy = () => {
    navigator.clipboard.writeText(copyTarget).then(() => {
      setCopied(true)
      onToast?.("已复制启动命令到剪贴板")
      setTimeout(() => setCopied(false), 2000)
    })
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, onClose])

  if (!open) return null

  const isConnected = Boolean(status?.connected)

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="stream-fade-in relative flex w-full max-w-[560px] flex-col rounded-[14px] border border-border bg-card p-6 shadow-2xl">
        {/* 顶部标题与关闭 */}
        <div className="flex items-start justify-between gap-3 border-b border-border/60 pb-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Globe className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-foreground">Chrome CDP 调试采集器</h3>
              <p className="text-xs text-muted-foreground">接管已登录的本地 Chrome，防封且支持 Boss直聘 与 猎聘 实时抓取</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 状态徽章与详情 */}
        <div className="my-4 flex flex-col gap-3">
          <div className={`flex items-center justify-between rounded-lg border p-3 ${isConnected ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"}`}>
            <div className="flex items-center gap-2">
              {isConnected ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <AlertCircle className="h-4 w-4 text-amber-500" />}
              <span className="text-xs font-semibold">{isConnected ? "调试服务已连通 (9222 端口可用)" : "调试服务未连通 (127.0.0.1:9222)"}</span>
            </div>
            <Button size="sm" variant="ghost" className="h-6 gap-1 px-2 text-[11px]" onClick={checkStatus} disabled={checking}>
              <RefreshCw className={`h-3 w-3 ${checking ? "animate-spin" : ""}`} />
              检测连接
            </Button>
          </div>

          {isConnected ? (
            <div className="flex flex-col gap-3 rounded-lg border border-border/80 bg-muted/20 p-4">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">已开启标签页检测：</span>
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className={status?.boss_opened ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}>
                    Boss直聘 {status?.boss_opened ? "✓ 已打开" : "待打开"}
                  </Badge>
                  <Badge variant="outline" className={status?.liepin_opened ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}>
                    猎聘 {status?.liepin_opened ? "✓ 已打开" : "待打开"}
                  </Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                提示：请确保在打开的 Chrome 中已完成账号登录，保持该 Chrome 窗口开启，直接在职位匹配页输入求职方向即可实时检索！
              </p>
              <div className="flex items-center gap-2 pt-1">
                <Button size="sm" variant="outline" className="h-7 gap-1.5 text-xs" asChild>
                  <a href="https://www.zhipin.com" target="_blank" rel="noreferrer">
                    打开 Boss直聘 <ExternalLink className="h-3 w-3" />
                  </a>
                </Button>
                <Button size="sm" variant="outline" className="h-7 gap-1.5 text-xs" asChild>
                  <a href="https://www.liepin.com" target="_blank" rel="noreferrer">
                    打开 猎聘网 <ExternalLink className="h-3 w-3" />
                  </a>
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-3.5">
              {(unavailableReason || manualOnly) && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-[11px] leading-relaxed text-amber-700 dark:text-amber-300">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{unavailableReason || "后端不在宿主机上运行（容器部署），无法代你启动浏览器；按下面的步骤在本机启动即可。"}</span>
                </div>
              )}

              <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/40 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-foreground">一次性设置：启动采集专用 Chrome</span>
                  <Button size="sm" variant="outline" className="h-6 gap-1 px-2 text-[11px]" onClick={handleCopy}>
                    {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                    {copied ? "已复制" : "复制启动命令"}
                  </Button>
                </div>
                <ol className="flex flex-col gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
                  {(setup?.steps ?? DEFAULT_SETUP_STEPS).map((step, index) => (
                    <li key={step} className="flex gap-1.5">
                      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-medium text-primary">
                        {index + 1}
                      </span>
                      <span>{step}</span>
                    </li>
                  ))}
                </ol>
                <div className="flex items-center gap-2 rounded border border-border/60 bg-background px-2.5 py-1.5 font-mono text-[11px] text-muted-foreground">
                  <Terminal className="h-3.5 w-3.5 shrink-0 text-primary/70" />
                  <span className="truncate" title={copyTarget}>{copyTarget}</span>
                </div>
              </div>

              {!manualOnly && (
                <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-muted/40 p-3">
                  <div className="flex flex-col gap-0.5">
                    <span className="text-xs font-medium text-foreground">后端与浏览器在同一台机器上？</span>
                    <span className="text-[11px] text-muted-foreground">可直接由后端唤起带调试端口的 Chrome（容器部署不适用）</span>
                  </div>
                  <Button size="sm" variant="default" className="h-8 shrink-0 gap-1.5 px-3 text-xs" onClick={handleLaunch} disabled={launching}>
                    {launching ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5 fill-current" />}
                    {launching ? "正在唤起…" : "一键启动 Chrome"}
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 底部操作 */}
        <div className="mt-2 flex items-center justify-end gap-2 border-t border-border/60 pt-3">
          <Button size="sm" variant="outline" className="h-8 px-4 text-xs" onClick={onClose}>
            {isConnected ? "完成并开始匹配" : "关闭"}
          </Button>
        </div>
      </div>
    </div>
  )
}
