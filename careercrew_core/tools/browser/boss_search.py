"""Boss直聘搜索后端（N1）：CDP 接管已登录 Chrome 抓取岗位列表。

解析与浏览器操作分离：parse_job_cards 只吃 ElementHandle 协议对象
（真实 handle / 测试桩均可），单测无需真浏览器。
输出 dict 与 JobsStore.upsert 对齐（source="boss"），url 留给详情页跳转。
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from careercrew_core.tools.browser.cdp import open_boss_page
from careercrew_core.tools.browser.job_detail import extract_jd_with_retry
from careercrew_core.tools.browser.patterns import BOSS_CITY_CODES, BOSS_PATTERNS
from careercrew_core.tools.browser.throttle import human_pause

logger = logging.getLogger(__name__)
_BOSS_DIGIT_START = 0xE031
_BOSS_DIGIT_END = 0xE03A


def _resolve_boss_city_code(city: str) -> str:
    """根据城市名解析 Boss直聘 9 位城市编码。未识别/全国时返回空字符串。"""
    c = (city or "").strip()
    if not c or c in ("全国", "不限"):
        return ""
    if c.isdigit():
        return c
    for name, code in BOSS_CITY_CODES.items():
        if name and name in c:
            return code
    return ""



def _text(card: Any, selector: str) -> str:
    """取卡片内字段文本；选择器未命中返回空串（改版容错）。"""
    el = card.query_selector(selector)
    if el is None:
        return ""
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        return ""


def _decode_salary(text: str) -> str:
    """解码 Boss 列表页私有字体数字；未知私有字符不交给模型猜测。"""
    decoded: list[str] = []
    unknown_private_char = False
    for char in text:
        codepoint = ord(char)
        if _BOSS_DIGIT_START <= codepoint <= _BOSS_DIGIT_END:
            decoded.append(str(codepoint - _BOSS_DIGIT_START))
        else:
            decoded.append(char)
            if 0xE000 <= codepoint <= 0xF8FF:
                unknown_private_char = True
    return "薪资请打开岗位链接查看" if unknown_private_char else "".join(decoded)


def parse_job_cards(cards: list[Any]) -> list[dict]:
    """把岗位卡片元素列表解析为 JobsStore 行。"""
    f = BOSS_PATTERNS["fields"]
    jobs: list[dict] = []
    for card in cards:
        title = _text(card, f["title"])
        link_el = card.query_selector(f["link"])
        url = (link_el.get_attribute("href") or "").strip() if link_el else ""
        # 相对路径补全
        if url.startswith("/"):
            url = f"https://www.zhipin.com{url}"
        exp_parts = [t.strip() for t in _all_texts(card, f["experience"]) if t.strip()]
        jobs.append({
            "title": title,
            "company": _text(card, f["company"]),
            "city": _text(card, f["area"]),
            "salary": _decode_salary(_text(card, f["salary"])),
            "experience": " | ".join(exp_parts),
            "jd": "",                      # 列表页无 JD 正文；收藏时由详情页抓取补全
            "url": url,
            "source": "boss",
        })
    return [j for j in jobs if j["title"]]  # 无标题的脏卡片丢弃


def _all_texts(card: Any, selector: str) -> list[str]:
    out = []
    for el in card.query_selector_all(selector):
        try:
            out.append((el.inner_text() or "").strip())
        except Exception:
            continue
    return out


def _looks_blocked(page: Any) -> bool:
    """风控验证页探测：命中特征即放弃本渠道（上层降级猎聘 MCP）。"""
    for marker in BOSS_PATTERNS["block_markers"]:
        try:
            if marker.startswith("text="):
                if page.locator(marker).count() > 0:
                    return True
            elif page.query_selector(marker) is not None:
                return True
        except Exception:
            continue
    return False


def search_boss_jobs(
    direction: str, top_k: int = 8, cdp_url: str = "", city: str = "",
    pause: bool = True,
) -> list[dict]:
    """Boss直聘搜索岗位；渠道不可用（未配置/风控页/超时）抛异常由上层降级。"""
    city_code = _resolve_boss_city_code(city)
    with open_boss_page(cdp_url) as page:
        url = BOSS_PATTERNS["search_url"].format(
            query=quote_plus(direction.strip()), city=city_code
        )
        logger.info("boss search navigating to %s", url)
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        if pause:
            human_pause()
        else:
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

        if _looks_blocked(page):
            raise RuntimeError("Boss直聘命中安全验证，请手动通过验证后重试")

        try:
            page.wait_for_selector(BOSS_PATTERNS["wait_selector"], timeout=15000)
        except Exception:
            logger.warning("boss wait_for_selector timed out on %s", url)

        if _looks_blocked(page):
            raise RuntimeError("Boss直聘命中安全验证，请手动通过验证后重试")

        cards = page.query_selector_all(BOSS_PATTERNS["job_card"])
        raw_limit = max(top_k * 3, 30)
        jobs = parse_job_cards(list(cards)[:raw_limit])
        logger.info("boss search %r -> %d jobs (from %d cards)", direction, len(jobs), len(cards))
        return jobs


def parse_boss_detail(page: Any) -> str:
    """从已打开的 Boss 岗位详情页抽 JD 正文（抽不到返回空串）。"""
    return extract_jd_with_retry(page, BOSS_PATTERNS)


def fetch_boss_job_detail(job_url: str, cdp_url: str = "") -> str:
    """打开 Boss 岗位详情页抓 JD 正文。

    风控验证页显式抛错（错误信息可直接给用户看）；抽不到正文返回空串，
    由调用方决定"JD 留空可重试"而不阻断收藏。
    """
    url = (job_url or "").strip()
    if not url:
        raise ValueError("job_url 为空：无法抓取 JD")

    with open_boss_page(cdp_url) as page:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        human_pause()
        if _looks_blocked(page):
            raise RuntimeError("Boss直聘命中安全验证，请手动通过验证后重试")
        jd = parse_boss_detail(page)
        logger.info("boss detail %s -> %d chars", url, len(jd))
        return jd
