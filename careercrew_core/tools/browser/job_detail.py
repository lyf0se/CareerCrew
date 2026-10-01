"""JD 正文抽取与清洗：详情页 DOM → 可直接入库的 JD 快照。

设计取舍：
- 站点知识（选择器、业务措辞关键词、长度阈值）全部来自 patterns.py，本模块
  只负责"按优先级取第一个达标的候选 → 类名全失效时按业务措辞兜底 → 清洗"
  这段通用逻辑，Boss 与猎聘共用，不各写一遍。
- 抽取失败一律返回空串，绝不抛异常——调用方据此降级（JD 留空可重试），
  不能因为抓不到正文而阻断"收藏岗位"这个主流程。
"""
from __future__ import annotations

import re

# 与 OpportunityInput.jd（careercrew_core/preparation/models.py）及
# jobs_extract.MAX_JD_CHARS 对齐：入库前的最后一道长度闸。
MAX_JD_CHARS = 30000

# 零宽字符与双向控制符（U+200B–U+200F / U+202A–U+202E / U+2060 / U+FEFF）：
# 招聘站用它做溯源标记、干扰复制，肉眼不可见却会污染快照并影响后续 ATS 检查
# 与 LLM 输入。写成转义而非字面字符，避免源码里出现看不见的字符。
_INVISIBLE = re.compile("[\\u200b-\\u200f\\u202a-\\u202e\\u2060\\ufeff]")
# 其余 C0 控制符（保留 \t 与 \n）：OpportunityInput.safe_text 会直接拒收含控制符的
# 文本，必须在这里清掉，否则整条采集会因为页面上一个 \x0c 而失败。
_CONTROL = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]")
# 连续换行最多保留一个空行；行内连续空白（含 NBSP U+00A0、全角空格 U+3000）压成一个
_BLANK_LINES = re.compile("\\n{3,}")
_INLINE_SPACE = re.compile("[ \\t\\u00a0\\u3000]{2,}")


def clean_jd_text(raw: str, max_chars: int = MAX_JD_CHARS) -> str:
    """清洗页面正文：剔隐形字符与控制符、统一换行、压缩空白、按上限截断。"""
    text = _INVISIBLE.sub("", str(raw or ""))
    text = _CONTROL.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INLINE_SPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()[:max_chars]


def _element_text(element) -> str:
    if element is None:
        return ""
    try:
        return (element.inner_text() or "").strip()
    except Exception:
        return ""


def _longest_keyword_block(page, patterns: dict, min_chars: int) -> str:
    """类名选择器全部失效时的兜底：含 JD 措辞、且长度达标的最小容器。

    取"最小"而非"最大"是为了避开 body 这类把整页包住的超大容器。
    """
    keywords = patterns.get("jd_keywords") or []
    scan = patterns.get("jd_block_scan") or ""
    if not keywords or not scan:
        return ""
    try:
        blocks = page.query_selector_all(scan)
    except Exception:
        return ""

    best = ""
    for element in blocks or []:
        text = _element_text(element)
        if len(text) < min_chars or len(text) > MAX_JD_CHARS:
            continue
        if not any(keyword in text for keyword in keywords):
            continue
        if not best or len(text) < len(best):
            best = text
    return best


def extract_jd(page, patterns: dict) -> str:
    """从详情页抽出 JD 正文；抽不到返回空串。

    page 只需实现 Playwright 的 query_selector / query_selector_all 协议，
    因此测试可用 Fake 页面对象驱动，无需真浏览器。
    """
    min_chars = int(patterns.get("jd_min_chars", 80))
    for selector in patterns.get("jd_selectors") or []:
        try:
            element = page.query_selector(selector)
        except Exception:
            continue
        text = _element_text(element)
        if len(text) >= min_chars:
            return clean_jd_text(text)

    return clean_jd_text(_longest_keyword_block(page, patterns, min_chars))


def extract_jd_with_retry(page, patterns: dict, attempts: int = 3,
                          interval_ms: int = 800) -> str:
    """详情页正文常晚于 domcontentloaded 渲染，给几次机会再放弃。"""
    for index in range(max(1, attempts)):
        jd = extract_jd(page, patterns)
        if jd:
            return jd
        if index == attempts - 1:
            break
        try:
            page.wait_for_timeout(interval_ms)
        except Exception:
            break
    return ""


def channel_for_url(url: str) -> str:
    """按域名判定岗位来源渠道；未知来源返回空串（不做网络请求）。"""
    host = (url or "").strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].split(":", 1)[0]
    if host.endswith("zhipin.com"):
        return "boss"
    if host.endswith("liepin.com"):
        return "liepin"
    return ""


def fetch_job_jd(url: str, cdp_url: str = "") -> str:
    """按 URL 域名路由到对应渠道抓 JD 正文；未知渠道返回空串。

    这里在函数内惰性导入 boss_search / liepin_search：那两个模块依赖本模块的
    抽取逻辑，模块级导入会成环。
    """
    channel = channel_for_url(url)
    if channel == "boss":
        from careercrew_core.tools.browser.boss_search import fetch_boss_job_detail
        return fetch_boss_job_detail(url, cdp_url=cdp_url)
    if channel == "liepin":
        from careercrew_core.tools.browser.liepin_search import fetch_liepin_job_detail
        return fetch_liepin_job_detail(url, cdp_url=cdp_url)
    return ""
