"""N1 浏览器通道单元测试：解析/风控探测/回退链，全部用 Fake 页面对象（无真浏览器）。

CDP 真实链路的手动验收见 docs/TECH_DEBT_PLAN.md 附录（N1/N2 手动验收清单）。
"""
from __future__ import annotations

import importlib
import json

from careercrew_core.tools.browser.boss_search import (
    _decode_salary,
    _looks_blocked,
    parse_job_cards,
)
from careercrew_core.tools.browser.cdp import resolve_cdp_url
from careercrew_core.tools.browser.job_detail import (
    MAX_JD_CHARS,
    channel_for_url,
    clean_jd_text,
    extract_jd,
    extract_jd_with_retry,
    fetch_job_jd,
)
from careercrew_core.tools.browser.throttle import gauss_delay_ms
from careercrew_core.tools.internal.search_jobs import make_search_jobs_tool

# tools.internal.__init__ 把 `search_jobs` 名字遮蔽成 tool 对象，
# monkeypatch 字符串路径会解析到遮蔽后的属性——必须显式取 sys.modules 里的模块。
sj_mod = importlib.import_module("careercrew_core.tools.internal.search_jobs")
boss_mod = importlib.import_module("careercrew_core.tools.browser.boss_search")


class FakeEl:
    """ElementHandle 协议桩。"""

    def __init__(self, text: str = "", attrs: dict | None = None,
                 children: list[FakeEl] | None = None, tags: tuple = ()):
        self._text = text
        self._attrs = attrs or {}
        self._children = children or []
        self._tags = set(tags)

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)

    def query_selector(self, sel: str):
        # 组合选择器（新版 patterns 用 "a.job-name, .job-name" 形式）任一命中；递归整棵树
        for s in sel.split(","):
            s = s.strip()
            for child in self._iter_descendants():
                if child.matches(s):
                    return child
        return None

    def query_selector_all(self, sel: str):
        out = []
        for s in sel.split(","):
            s = s.strip()
            out.extend(c for c in self._iter_descendants() if c.matches(s))
        return out

    def _iter_descendants(self):
        for c in self._children:
            yield c
            yield from c._iter_descendants()

    def matches(self, sel: str) -> bool:
        return sel in self._tags


def _card(title="大模型应用工程师", area="北京·朝阳", salary="25-35K",
          company="字节跳动", href="/job_card/abc.html", tags=("3-5年", "本科")):
    return FakeEl(tags=("li.job-card-box", ".job-name", ".company-location", ".job-salary",
                        ".boss-name", "a.job-name", ".tag-list li"), children=[
        FakeEl(tags=(".job-info",), children=[
            FakeEl(tags=(".job-title",), children=[
                FakeEl(tags=(".job-name", "a.job-name"), text=title, attrs={"href": href}),
                FakeEl(tags=(".job-salary",), text=salary),
            ]),
            FakeEl(tags=(".tag-list",), children=[
                FakeEl(tags=(".tag-list li",), text=t) for t in tags
            ]),
        ]),
        FakeEl(tags=(".job-card-footer",), children=[
            FakeEl(tags=(".boss-info",), children=[
                FakeEl(tags=(".boss-name",), text=company),
            ]),
            FakeEl(tags=(".company-location",), text=area),
        ]),
    ])


def test_parse_job_cards_full_fields() -> None:
    jobs = parse_job_cards([_card()])
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "大模型应用工程师"
    assert j["company"] == "字节跳动"
    assert j["city"] == "北京·朝阳"
    assert j["salary"] == "25-35K"
    assert j["experience"] == "3-5年 | 本科"
    assert j["url"].endswith("/job_card/abc.html") and j["url"].startswith("https://www.zhipin.com")
    assert j["source"] == "boss"


def test_parse_job_cards_relative_href_and_dirty_drop() -> None:
    good = _card()
    dirty = FakeEl(tags=("li.job-card-box",))  # 无标题卡片 -> 丢弃
    jobs = parse_job_cards([good, dirty])
    assert len(jobs) == 1


def test_decode_boss_private_font_salary_digits() -> None:
    assert _decode_salary("\ue032\ue031-\ue032\ue036K") == "10-15K"
    assert _decode_salary("\ue032\ue031\ue031\ue031-\ue034\ue031\ue031\ue031元/月") == "1000-3000元/月"
    assert _decode_salary("25-35K") == "25-35K"


def test_unknown_private_salary_char_degrades_without_guessing() -> None:
    assert _decode_salary("\ue100-\ue101K") == "薪资请打开岗位链接查看"


def test_gauss_delay_clamped(monkeypatch) -> None:
    monkeypatch.setattr("random.gauss", lambda mu, sigma: -999.0)
    assert gauss_delay_ms() == 300          # 下界 clamp
    monkeypatch.setattr("random.gauss", lambda mu, sigma: 99_999.0)
    assert gauss_delay_ms() == 5000         # 上界 clamp


def test_looks_blocked_on_verification_page() -> None:
    class BlockedPage:
        def locator(self, sel):
            class L:
                def count(self): return 1
            return L()
        def query_selector(self, sel): return None

    assert _looks_blocked(BlockedPage()) is True


class FakeStore:
    """记录调用的 JobsStore 桩：库内永远未命中。"""

    def __init__(self):
        self.upserted = []

    def search(self, direction, top_k=8, max_age_days=7.0):
        return []

    def upsert(self, jobs, direction):
        self.upserted.extend(jobs)


def test_search_jobs_falls_back_to_mcp_when_boss_disabled(monkeypatch) -> None:
    calls = {"boss": 0, "mcp": 0}

    def fail_boss(*a, **k):
        calls["boss"] += 1
        raise AssertionError("Boss 未配置时不应触发")

    monkeypatch.setattr(
        sj_mod, "search_jobs_mcp",
        lambda d, top_k=8, timeout=180.0: (calls.__setitem__("mcp", calls["mcp"] + 1) or [
            {"title": t, "company": "C", "city": "北京", "salary": "20K",
             "experience": "", "jd": "", "url": "", "source": "liepin"}
            for t in ("Java 工程师",)
        ]),
    )
    tool = make_search_jobs_tool(FakeStore(), boss_cdp_url="")   # Boss 未配置
    out = json.loads(tool.invoke({"direction": "Java", "top_k": 3}))
    assert calls == {"boss": 0, "mcp": 1}
    assert out[0]["title"] == "Java 工程师"


def test_search_jobs_combines_boss_and_liepin(monkeypatch) -> None:
    store = FakeStore()
    mcp_calls = {"n": 0}

    def boss_ok(direction, top_k=8, cdp_url="", city="", pause=True):
        return [{"title": f"Boss大模型应用岗{i}", "company": "B", "city": "上海", "salary": "30K",
                 "experience": "", "jd": "", "url": f"https://www.zhipin.com/job{i}",
                 "source": "boss"} for i in range(top_k)]

    def mcp_ok(direction, top_k=8, timeout=180.0):
        mcp_calls["n"] += 1
        return [{"title": "猎聘大模型应用岗", "company": "L", "city": "上海", "salary": "20K",
                 "experience": "", "jd": "", "url": "https://www.liepin.com/job/1",
                 "source": "liepin"}]

    monkeypatch.setattr(boss_mod, "search_boss_jobs", boss_ok)
    monkeypatch.setattr(sj_mod, "search_jobs_mcp", mcp_ok)

    tool = make_search_jobs_tool(store, boss_cdp_url="http://127.0.0.1:9222")
    out = json.loads(tool.invoke({"direction": "大模型应用", "top_k": 2}))
    assert [job["title"] for job in out] == ["Boss大模型应用岗0", "猎聘大模型应用岗"]
    assert [job["source_label"] for job in out] == ["Boss直聘", "猎聘"]
    assert mcp_calls["n"] == 1
    assert [j["title"] for j in store.upserted] == [
        "Boss大模型应用岗0", "猎聘大模型应用岗",
    ]


def test_search_jobs_boss_failure_degrades_to_mcp(monkeypatch) -> None:
    def boss_fail(*a, **k):
        raise RuntimeError("安全验证")

    monkeypatch.setattr(boss_mod, "search_boss_jobs", boss_fail)
    monkeypatch.setattr(
        sj_mod, "search_jobs_mcp",
        lambda d, top_k=8, timeout=180.0: [{"title": "猎聘数据分析岗", "company": "L", "city": "深圳", "salary": "18K",
                             "experience": "", "jd": "", "url": "", "source": "liepin"}],
    )
    tool = make_search_jobs_tool(None, boss_cdp_url="http://127.0.0.1:9222")
    out = json.loads(tool.invoke({"direction": "数据分析"}))
    assert out[0]["title"] == "猎聘数据分析岗"


def test_resolve_liepin_city_code() -> None:
    from careercrew_core.tools.browser.liepin_search import _resolve_liepin_city_code

    assert _resolve_liepin_city_code("北京") == "010"
    assert _resolve_liepin_city_code("广州市") == "050"
    assert _resolve_liepin_city_code("深圳") == "060"
    assert _resolve_liepin_city_code("未知城市") == ""


def test_parse_liepin_job_cards() -> None:
    from careercrew_core.tools.browser.liepin_search import parse_liepin_job_cards

    card = FakeEl(
        tags=("div.job-detail-box",),
        text="招聘Java 后端工程师 【北京-海淀区】 15-20k 2-5年 本科 勋厚人力 人力资源服务",
        children=[
            FakeEl(
                tags=("a[data-nick='job-detail-job-info']", "a"),
                attrs={"href": "https://www.liepin.com/job/1984882287.shtml"},
                text="招聘Java 后端工程师 【北京-海淀区】 15-20k 2-5年 本科",
                children=[
                    FakeEl(tags=(".ellipsis-1",), text="招聘Java 后端工程师"),
                    FakeEl(tags=("span",), text="2-5年"),
                    FakeEl(tags=("span",), text="本科"),
                ],
            ),
            FakeEl(
                tags=("div[data-nick='job-detail-company-info']", "[data-nick='job-detail-company-info']"),
                children=[
                    FakeEl(tags=(".ellipsis-1",), text="勋厚人力"),
                ],
            ),
        ],
    )
    jobs = parse_liepin_job_cards([card])
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Java 后端工程师"
    assert j["company"] == "勋厚人力"
    assert j["city"] == "北京-海淀区"
    assert j["salary"] == "15-20k"
    assert j["source"] == "liepin"
    assert j["salary_k"] == {"min_k": 15.0, "max_k": 20.0, "months": None}
    assert "1984882287.shtml" in j["url"]



# ── JD 详情页抽取与清洗（收藏时按需补全快照） ──

_JD_BODY = (
    "岗位职责\n1、参与系统架构设计与技术选型，负责业务系统后端功能模块的设计、编码实现；\n"
    "2、参与跨境电商供应链信息化、自动化建设，包括 SRM、ERP、WMS 等；\n"
    "任职要求\n1、本科及以上学历，5 年 Java 实际项目开发经验；"
)


class FakeDetailPage:
    """详情页桩：选择器 → 节点映射，可给出兜底扫描块。"""

    def __init__(self, by_selector: dict | None = None, blocks: list | None = None):
        self._by_selector = by_selector or {}
        self._blocks = blocks or []
        self.waits = 0

    def query_selector(self, sel: str):
        return self._by_selector.get(sel)

    def query_selector_all(self, sel: str):
        return list(self._blocks)

    def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1


def test_clean_jd_strips_invisible_and_control_chars() -> None:
    raw = "岗位职责\u200b：\n\n\n\n  负责\u3000\u3000后端开发\x0c；\n任职要求\ufeff：\r\n\r\n1、本科"
    cleaned = clean_jd_text(raw)
    for junk in ("\u200b", "\ufeff", "\x0c", "\u3000", "\r"):
        assert junk not in cleaned
    assert "\n\n\n" not in cleaned
    assert cleaned.startswith("岗位职责：")


def test_cleaned_jd_passes_opportunity_validation() -> None:
    """清洗结果必须能直接入库：OpportunityInput 会拒收控制字符。"""
    from careercrew_core.preparation.models import OpportunityInput

    cleaned = clean_jd_text("岗位职责\x0c\u200b\n\n\n  负责\u3000后端\x1f开发" * 3)
    payload = OpportunityInput(company="某公司", title="后端工程师", jd=cleaned)
    assert payload.jd == cleaned


def test_jd_truncated_to_model_limit() -> None:
    from careercrew_core.preparation.models import OpportunityInput

    jd = clean_jd_text("测" * (MAX_JD_CHARS + 500))
    assert len(jd) == MAX_JD_CHARS
    OpportunityInput(company="某公司", title="后端工程师", jd=jd)   # 不抛异常即通过


def test_extract_jd_uses_first_selector_hit() -> None:
    page = FakeDetailPage(by_selector={".job-sec-text": FakeEl(text=_JD_BODY)})
    jd = extract_jd(page, {"jd_selectors": [".job-sec-text", ".other"], "jd_min_chars": 80})
    assert jd.startswith("岗位职责")
    assert "跨境电商" in jd


def test_extract_jd_skips_too_short_candidate() -> None:
    """容器命中但文本过短（如只抓到标题）时继续往下试，不能当成 JD。"""
    page = FakeDetailPage(by_selector={
        ".job-sec-text": FakeEl(text="后端工程师"),
        ".job-detail-section .text": FakeEl(text=_JD_BODY),
    })
    jd = extract_jd(page, {
        "jd_selectors": [".job-sec-text", ".job-detail-section .text"], "jd_min_chars": 80,
    })
    assert "任职要求" in jd


def test_extract_jd_falls_back_to_smallest_keyword_block() -> None:
    """类名全部失效时，按业务措辞定位，且取最小容器（避开包住整页的超大节点）。"""
    page = FakeDetailPage(blocks=[
        FakeEl(text="导航 登录 首页 " + _JD_BODY + " 相关推荐 " * 20),   # 大容器
        FakeEl(text=_JD_BODY),                                          # 最小达标容器
        FakeEl(text="公司简介 " + "介绍" * 200),                         # 无 JD 措辞
    ])
    jd = extract_jd(page, {
        "jd_selectors": [".gone"], "jd_keywords": ["岗位职责", "任职要求"],
        "jd_min_chars": 80, "jd_block_scan": "div, section, article, main",
    })
    assert jd == _JD_BODY


def test_extract_jd_returns_empty_when_nothing_matches() -> None:
    page = FakeDetailPage(blocks=[FakeEl(text="登录后查看")])
    assert extract_jd(page, {
        "jd_selectors": [".gone"], "jd_keywords": ["岗位职责"],
        "jd_min_chars": 80, "jd_block_scan": "div",
    }) == ""


def test_extract_jd_retries_before_giving_up() -> None:
    """正文常晚于 domcontentloaded 渲染：给几次机会，但不无限等。"""
    class LatePage(FakeDetailPage):
        def __init__(self) -> None:
            super().__init__()
            self.ready = False

        def query_selector(self, sel: str):
            return FakeEl(text=_JD_BODY) if self.ready else None

        def wait_for_timeout(self, ms: int) -> None:
            self.waits += 1
            self.ready = True

    page = LatePage()
    jd = extract_jd_with_retry(page, {"jd_selectors": [".job-sec-text"], "jd_min_chars": 80})
    assert jd == _JD_BODY
    assert page.waits == 1

    empty = FakeDetailPage()
    assert extract_jd_with_retry(empty, {"jd_selectors": [".gone"], "jd_min_chars": 80},
                                 attempts=3) == ""
    assert empty.waits == 2      # 最后一次不再空等


def test_channel_for_url_routes_known_sources_only() -> None:
    assert channel_for_url("https://www.zhipin.com/job_detail/x.html") == "boss"
    assert channel_for_url("https://m.zhipin.com/job/1") == "boss"
    assert channel_for_url("https://www.liepin.com/lptjob/1.shtml") == "liepin"
    assert channel_for_url("https://example.com/job/1") == ""
    assert channel_for_url("") == ""


def test_fetch_job_jd_unknown_source_makes_no_request() -> None:
    """未知来源直接返回空串，不打开浏览器（避免对无关链接发起采集）。"""
    assert fetch_job_jd("https://example.com/job/1", cdp_url="http://127.0.0.1:9222") == ""


# ── CDP 端点解析：Chrome 拒绝域名形式的 Host 头 ──

def test_resolve_cdp_url_replaces_dns_with_ip(monkeypatch) -> None:
    """容器访问宿主机只能用 host.docker.internal，但 Chrome 拒绝该域名的 Host 头。"""
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.168.65.254")
    assert resolve_cdp_url("http://host.docker.internal:9222") == "http://192.168.65.254:9222"


def test_resolve_cdp_url_keeps_ip_and_localhost(monkeypatch) -> None:
    def boom(host):        # 不应被调用
        raise AssertionError(f"无需解析: {host}")

    monkeypatch.setattr("socket.gethostbyname", boom)
    assert resolve_cdp_url("http://127.0.0.1:9222") == "http://127.0.0.1:9222"
    assert resolve_cdp_url("http://localhost:9222") == "http://localhost:9222"
    assert resolve_cdp_url("") == ""


def test_resolve_cdp_url_keeps_url_when_dns_fails(monkeypatch) -> None:
    def boom(host):
        raise OSError("nxdomain")

    monkeypatch.setattr("socket.gethostbyname", boom)
    assert resolve_cdp_url("http://nope.invalid:9222") == "http://nope.invalid:9222"
