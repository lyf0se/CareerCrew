// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"

import { JobCards } from "@/components/preparation/JobCards"
import type { JobOpportunity } from "@/types"

const apiFetch = vi.fn()

vi.mock("@/lib/auth", () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
}))

const JOB: JobOpportunity = {
  company: "测试公司",
  title: "Java开发",
  city: "深圳",
  salary: "20-30K",
  source: "boss",
  source_label: "Boss直聘",
  url: "https://example.com/j/1",
  jd: "负责接口开发" + "，涉及高并发".repeat(60),
}

beforeEach(() => {
  apiFetch.mockReset()
})

function renderCards(jobs: JobOpportunity[] = [JOB]) {
  return render(
    <MemoryRouter>
      <JobCards jobs={jobs} />
    </MemoryRouter>,
  )
}

describe("JobCards", () => {
  it("渲染岗位摘要与展开 JD", () => {
    renderCards()
    expect(screen.getByText("测试公司")).toBeTruthy()
    expect(screen.getByText(/Java开发/)).toBeTruthy()
    expect(screen.getByText(/Boss直聘/)).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: /展开 JD/ }))
    expect(screen.getByText(/收起 JD/)).toBeTruthy()
  })

  it("不安全的来源链接不渲染为可点击 a 标签", () => {
    renderCards([{ ...JOB, url: "javascript:alert(1)" }])
    expect(screen.queryByRole("link", { name: /来源链接/ })).toBeNull()
  })

  it("无来源链接的岗位：提示走手动录入，不自动采集", () => {
    renderCards([{ ...JOB, jd: "", url: "" }])
    expect(screen.getByText(/手动录入岗位/)).toBeTruthy()
  })

  it("无 JD 全文的岗位：收藏时先采集 JD，再带着 JD 建岗位", async () => {
    apiFetch.mockImplementation(async (url: string) => {
      if (url === "/api/preparation/capture-jd") {
        return {
          ok: true, status: 200,
          json: async () => ({ status: "captured", jd: "岗位职责：负责接口开发", message: "已采集 JD" }),
        }
      }
      return { ok: true, status: 201, json: async () => ({ id: "opp-1", company: "测试公司", title: "Java开发" }) }
    })
    renderCards([{ ...JOB, jd: "" }])

    const btn = screen.getByRole("button", { name: /收藏岗位/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(false)      // 缺 JD 不再让收藏入口不可用
    fireEvent.click(btn)

    await waitFor(() => expect(screen.getByText("已收藏")).toBeTruthy())
    expect(apiFetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/preparation/capture-jd",
      "/api/preparation/opportunities",
    ])
    const [, init] = apiFetch.mock.calls[1]
    expect(JSON.parse((init as RequestInit).body as string)).toMatchObject({
      jd: "岗位职责：负责接口开发",
    })
    expect(screen.getByText(/已采集 JD 全文/)).toBeTruthy()
  })

  it("采集不到 JD：展示原因且不创建岗位", async () => {
    apiFetch.mockImplementation(async () => ({
      ok: true, status: 200,
      json: async () => ({
        status: "failed", jd: "",
        message: "未能从详情页读到 JD 正文，请确认采集浏览器已登录 Boss直聘 且窗口保持打开",
      }),
    }))
    renderCards([{ ...JOB, jd: "" }])
    fireEvent.click(screen.getByRole("button", { name: /收藏岗位/ }))

    await waitFor(() => expect(screen.getByText(/未能从详情页读到 JD 正文/)).toBeTruthy())
    expect(apiFetch.mock.calls.map(([url]) => url)).toEqual(["/api/preparation/capture-jd"])
    expect(screen.queryByRole("link", { name: "去准备" })).toBeNull()
  })

  it("收藏岗位：POST 保存成功后显示已收藏与去准备入口", async () => {
    apiFetch.mockImplementation(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ id: "opp-1", company: "测试公司", title: "Java开发" }),
    }))
    renderCards()
    fireEvent.click(screen.getByRole("button", { name: /收藏岗位/ }))
    await waitFor(() => expect(screen.getByText("已收藏")).toBeTruthy())
    expect(screen.getByRole("link", { name: "去准备" }).getAttribute("href")).toBe(
      "/preparation?opportunity=opp-1",
    )
    const [url, init] = apiFetch.mock.calls[0]
    expect(url).toBe("/api/preparation/opportunities")
    expect(JSON.parse((init as RequestInit).body as string)).toMatchObject({
      company: "测试公司",
      title: "Java开发",
      jd: JOB.jd,
      url: "https://example.com/j/1",
    })
  })

  it("收藏失败：显示后端错误文案，且不跳转", async () => {
    apiFetch.mockImplementation(async () => ({
      ok: false,
      status: 422,
      json: async () => ({ detail: "岗位链接必须是有效的 HTTP 或 HTTPS 地址" }),
    }))
    renderCards()
    fireEvent.click(screen.getByRole("button", { name: /收藏岗位/ }))
    await waitFor(() =>
      expect(screen.getByText("岗位链接必须是有效的 HTTP 或 HTTPS 地址")).toBeTruthy())
    expect(screen.queryByRole("link", { name: "去准备" })).toBeNull()
  })
})
