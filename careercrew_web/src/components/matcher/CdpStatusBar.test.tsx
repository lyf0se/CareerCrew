// @vitest-environment jsdom
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { describe, it, expect, vi, beforeEach } from "vitest"
import { CdpStatusBar } from "./CdpStatusBar"

describe("CdpStatusBar", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("renders connected state when CDP port is alive", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        connected: true,
        cdp_url: "http://127.0.0.1:9222",
        boss_opened: true,
        liepin_opened: false,
        command: "powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1",
      }),
    }))

    render(<CdpStatusBar variant="card" />)

    await waitFor(() => {
      expect(screen.getByText("Chrome 实时采集器：已就绪")).toBeTruthy()
    })
    expect(screen.getByText("Boss直聘 ✓ 标签页已打开")).toBeTruthy()
    expect(screen.getByText("猎聘 待打开")).toBeTruthy()
  })

  it("renders disconnected state with launch and copy buttons", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        connected: false,
        cdp_url: "http://127.0.0.1:9222",
        boss_opened: false,
        liepin_opened: false,
        command: "powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1",
      }),
    }))

    render(<CdpStatusBar variant="card" />)

    await waitFor(() => {
      expect(screen.getByText("Boss直聘与猎聘实时采集器：未启动")).toBeTruthy()
    })
    expect(screen.getByRole("button", { name: /一键启动 Chrome/i })).toBeTruthy()
    expect(screen.getByRole("button", { name: /复制命令/i })).toBeTruthy()
  })

  it("handles copy command to clipboard", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: { writeText: writeTextMock },
    })

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        connected: false,
        cdp_url: "http://127.0.0.1:9222",
        boss_opened: false,
        liepin_opened: false,
        command: "powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1",
      }),
    }))

    const toastMock = vi.fn()
    render(<CdpStatusBar variant="banner" onToast={toastMock} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /复制命令/i })).toBeTruthy()
    })

    fireEvent.click(screen.getByRole("button", { name: /复制命令/i }))
    expect(writeTextMock).toHaveBeenCalledWith("powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1")
  })

  // 回归：容器部署时后端会以 403 拒绝该接口（客户端 IP 是网桥网关而非 loopback）。
  // 之前是 `if (!status) return null`，整块功能连"一键启动"一起凭空消失、无从诊断。
  it("接口 403 时渲染不可用原因与设置引导，而不是整块消失", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ detail: "仅支持本机访问" }),
    }))

    render(<CdpStatusBar variant="card" />)

    await waitFor(() => {
      expect(screen.getByText("实时采集器不可用")).toBeTruthy()
    })
    expect(screen.getByText(/local_guard/)).toBeTruthy()
    expect(screen.getByRole("button", { name: /查看设置引导/ })).toBeTruthy()
  })

  it("接口不可用时顶部横条同样给出原因", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 403, json: async () => ({ detail: "仅支持本机访问" }),
    }))

    render(<CdpStatusBar variant="banner" />)

    await waitFor(() => {
      expect(screen.getByText(/实时采集器不可用/)).toBeTruthy()
    })
    expect(screen.getByRole("button", { name: /设置引导/ })).toBeTruthy()
  })

  // 后端在 Linux 容器里跑，用户坐在 Windows 上：命令必须按客户端平台挑，
  // 否则会把 google-chrome 的启动命令发给 Windows 用户（实测踩到过）。
  it("有 setup 时按客户端平台挑命令，再退到后端默认平台", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText: writeTextMock } })
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
      configurable: true,
    })

    const windowsCommand = '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222'
    const linuxCommand = "google-chrome --remote-debugging-port=9222"
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        connected: false,
        cdp_url: "http://host.docker.internal:9222",
        boss_opened: false,
        liepin_opened: false,
        command: "powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1",
        setup: {
          port: 9222,
          commands: { windows: windowsCommand, linux: linuxCommand },
          user_data_dirs: { windows: "C:\\ChromeDevData", linux: "~/.careercrew-chrome" },
          default_platform: "linux",
          steps: ["建快捷方式", "登录一次"],
        },
      }),
    }))

    render(<CdpStatusBar variant="card" />)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /弹窗配置/i })).toBeTruthy()
    })

    fireEvent.click(screen.getByRole("button", { name: /弹窗配置/i }))
    fireEvent.click(screen.getByRole("button", { name: /复制启动命令/i }))

    expect(writeTextMock).toHaveBeenCalledWith(windowsCommand)
  })
})
