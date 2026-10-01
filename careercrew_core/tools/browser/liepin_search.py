"""猎聘搜索后端：CDP 接管已登录 Chrome 抓取猎聘岗位列表。

解析与浏览器操作分离：parse_liepin_job_cards 只吃 ElementHandle 协议对象
（真实 handle / 测试桩均可），单测无需真浏览器。
输出 dict 与 JobsStore.upsert 对齐（source="liepin"），url 留给详情页跳转。
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote_plus

from careercrew_core.tools.browser.cdp import open_cdp_page
from careercrew_core.tools.browser.job_detail import extract_jd_with_retry
from careercrew_core.tools.browser.patterns import LIEPIN_CITY_CODES, LIEPIN_PATTERNS
from careercrew_core.tools.browser.throttle import human_pause
from careercrew_core.tools.jobs.salary_parser import parse_salary_range

logger = logging.getLogger(__name__)

_SALARY_REGEX = re.compile(
    r"(\d+(?:\.\d+)?\s*[-~]\s*\d+(?:\.\d+)?\s*[kK万](?:·\d{1,2}薪)?|\d+[-~]\d+元/[天月]|面议)",
    re.IGNORECASE,
)
_CITY_REGEX = re.compile(r"【\s*([^】]+?)\s*】")


def _resolve_liepin_city_code(city: str) -> str:
    """根据城市名解析猎聘 3 位 dqs 城市代码；未知或空返回空串。"""
    if not city:
        return ""
    normalized = city.strip().removesuffix("市")
    for name, code in LIEPIN_CITY_CODES.items():
        if name.removesuffix("市") == normalized:
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


def parse_liepin_job_cards(cards: list[Any]) -> list[dict]:
    """把猎聘岗位卡片或 anchor 元素列表解析为 JobsStore 行。"""
    f = LIEPIN_PATTERNS["fields"]
    jobs: list[dict] = []
    seen_identities: set[tuple[str, str, str]] = set()

    for card in cards:
        card_text = ""
        try:
            card_text = (card.inner_text() or "").strip()
        except Exception:
            pass

        # 1) 标题提取：先在 job-info 容器内查 .ellipsis-1；若未命中则查 card 的 .ellipsis-1
        title = ""
        job_info_box = card.query_selector("a[data-nick='job-detail-job-info'], [data-nick='job-detail-job-info']")
        if job_info_box:
            sub_title = job_info_box.query_selector(".ellipsis-1, div.ellipsis-1")
            if sub_title:
                title = re.sub(r"^招聘\s*", "", (sub_title.inner_text() or "").split("\n")[0]).strip()
        if not title:
            t_el = card.query_selector(".ellipsis-1")
            if t_el:
                title = re.sub(r"^招聘\s*", "", (t_el.inner_text() or "").split("\n")[0]).strip()
        if not title:
            raw_title = _text(card, f["title"])
            if not raw_title and card_text:
                first_line = card_text.split("\n")[0]
                raw_title = first_line.replace("【", " ").strip()
            title = re.sub(r"^招聘\s*", "", raw_title).strip()

        # 2) 链接提取：优先查找包含详情链接的 a 标签
        link_el = card.query_selector(f["link"])
        if link_el is None:
            link_el = card if card.get_attribute("href") else card.query_selector("a")
        url = (link_el.get_attribute("href") or "").strip() if link_el else ""
        if url.startswith("/"):
            url = f"https://www.liepin.com{url}"

        # 3) 公司名称提取：先在 company-info 容器内查找 .ellipsis-1，支持两段式 DOM 与平铺 DOM
        company = ""
        comp_box = card.query_selector("div[data-nick='job-detail-company-info'], [data-nick='job-detail-company-info'], .company-name, .company-info")
        if comp_box:
            sub_comp = comp_box.query_selector(".ellipsis-1")
            if sub_comp:
                company = (sub_comp.inner_text() or "").split("\n")[0].split("·")[0].strip()
            else:
                comp_lines = [l.strip() for l in (comp_box.inner_text() or "").split("\n") if l.strip()]
                if comp_lines:
                    company = comp_lines[0].split("·")[0].strip()
        if not company:
            company = _text(card, f["company"])
        # 如果当前 card 只是内部链接节点，尝试向父级容器获取公司信息
        if not company:
            try:
                parent = card.evaluate_handle('el => el.closest(".job-detail-box, .job-card-pc-container") || el.parentElement')
                if parent:
                    p_comp = parent.query_selector("div[data-nick='job-detail-company-info'] .ellipsis-1, div[data-nick='job-detail-company-info'], [data-nick='job-detail-company-info'], .company-name")
                    if p_comp:
                        company = (p_comp.inner_text() or "").split("\n")[0].split("·")[0].strip()
            except Exception:
                pass

        # 4) 薪资提取：优先正则从卡片文本提取
        salary = ""
        m_sal = _SALARY_REGEX.search(card_text)
        if m_sal:
            salary = m_sal.group(1).strip()

        # 5) 城市提取：优先从地区选择器或正则提取（支持 广州-海珠区、北京·朝阳区 等格式）
        city = ""
        area_el = card.query_selector("a[data-nick='job-detail-job-info'] span.ellipsis-1, .job-dq-box, [class*='job-dq']")
        if area_el:
            city = (area_el.inner_text() or "").strip()
        if not city:
            m_city = _CITY_REGEX.search(card_text)
            if m_city:
                city = m_city.group(1).strip()
        if not city:
            m_cn_city = re.search(r"([\u4e00-\u9fa5]{2,6}(?:[-·][\u4e00-\u9fa5]{2,6})?)", card_text)
            if m_cn_city:
                city = m_cn_city.group(1).strip()

        # 6) 经验/学历标签提取
        exp_parts = []
        tags = card.query_selector_all("span")
        for tag in tags:
            try:
                t = (tag.inner_text() or "").strip()
                if re.search(r"(?:\d+[-~]?\d*年|经验不限|在校|应届|本科|大专|硕士|博士|实习)", t):
                    if t not in exp_parts and t != city and t != salary:
                        exp_parts.append(t)
            except Exception:
                continue

        raw = card_text or " ".join((title, company, city, salary))
        identity = (title, company, city)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)

        jobs.append({
            "title": title,
            "company": company,
            "city": city,
            "salary": salary,
            "salary_k": parse_salary_range(salary),
            "experience": " | ".join(exp_parts),
            "jd": "",                      # 列表页无 JD 正文；收藏时由详情页抓取补全
            "raw": raw[:500],
            "url": url,
            "source": "liepin",
        })

    return [j for j in jobs if j["title"]]


def _looks_blocked(page: Any) -> bool:
    """快速判定当前页面是否被猎聘风控拦截。"""
    for marker in LIEPIN_PATTERNS["block_markers"]:
        try:
            if marker.startswith("text="):
                if page.locator(marker).count() > 0:
                    return True
            elif page.query_selector(marker) is not None:
                return True
        except Exception:
            continue
    return False


def search_liepin_jobs(
    direction: str,
    top_k: int = 8,
    cdp_url: str = "",
    city: str = "",
    pause: bool = True,
) -> list[dict]:
    """猎聘搜索岗位；渠道不可用（未配置/风控页/超时）抛异常由上层降级。"""
    city_code = _resolve_liepin_city_code(city)
    city_param = f"&dqs={city_code}" if city_code else ""

    # 当已通过 dqs 限定城市时，从关键词中剥离城市名，避免猎聘双重过滤导致0召回
    clean_query = direction.strip()
    if city_code and city:
        for c_name in (city, city.rstrip("市")):
            if c_name and c_name in clean_query:
                clean_query = clean_query.replace(c_name, "").strip()
    if not clean_query:
        clean_query = direction.strip()

    with open_cdp_page(cdp_url) as page:
        url = LIEPIN_PATTERNS["search_url"].format(
            query=quote_plus(clean_query),
            city_param=city_param,
        )
        logger.info("liepin search navigating to %s", url)
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        if pause:
            human_pause()
        else:
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

        if _looks_blocked(page):
            raise RuntimeError("猎聘命中安全验证，请在浏览器中手动完成验证后重试")

        try:
            page.evaluate("window.scrollBy(0, 1200)")
        except Exception:
            pass

        try:
            page.wait_for_selector(LIEPIN_PATTERNS["wait_selector"], timeout=15000)
        except Exception:
            logger.warning("liepin wait_for_selector timed out on %s", url)

        if _looks_blocked(page):
            raise RuntimeError("猎聘命中安全验证，请在浏览器中手动完成验证后重试")

        cards = page.query_selector_all(LIEPIN_PATTERNS["job_card"])
        if not cards:
            cards = page.query_selector_all(LIEPIN_PATTERNS["card_anchor"])

        raw_limit = max(top_k * 3, 30)
        jobs = parse_liepin_job_cards(list(cards)[:raw_limit])
        logger.info("liepin search %r -> %d jobs (from %d cards)", direction, len(jobs), len(cards))
        return jobs


def parse_liepin_detail(page: Any) -> str:
    """从已打开的猎聘岗位详情页抽 JD 正文（抽不到返回空串）。"""
    return extract_jd_with_retry(page, LIEPIN_PATTERNS)


def fetch_liepin_job_detail(job_url: str, cdp_url: str = "") -> str:
    """打开猎聘岗位详情页抓 JD 正文；语义同 fetch_boss_job_detail。"""
    url = (job_url or "").strip()
    if not url:
        raise ValueError("job_url 为空：无法抓取 JD")

    with open_cdp_page(cdp_url) as page:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        human_pause()
        if _looks_blocked(page):
            raise RuntimeError("猎聘命中安全验证，请在浏览器中手动完成验证后重试")
        jd = parse_liepin_detail(page)
        logger.info("liepin detail %s -> %d chars", url, len(jd))
        return jd
