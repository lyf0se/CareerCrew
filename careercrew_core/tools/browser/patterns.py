"""site-patterns：站点 URL 与 DOM 选择器集中定义（BossHunter 约定）。

反爬站点的前端会不定期改版——所有选择器只允许出现在本文件，
改版时只需更新此处，采集/解析逻辑不动。字段名与 JobsStore.upsert 对齐。
"""
from __future__ import annotations

BOSS_PATTERNS: dict = {
    # 搜索页（city 为空时用全国检索；城市代码见 zhipin 城市表，如北京 101010100）
    # 直接访问 /web/geek/jobs 避免 /job 重定向带来的异步竞态
    "search_url": "https://www.zhipin.com/web/geek/jobs?query={query}&city={city}",
    # 岗位卡片容器：2026 改版为 .job-card-wrap / li.job-card-box；兼容旧版 wrapper
    "job_card": ".job-card-wrap, .job-card-box, li.job-card-box, li.job-card-wrapper, div.job-card-wrapper",
    "wait_selector": ".job-card-wrap, li.job-card-box .job-name, li.job-card-wrapper .job-name, a.job-name",
    "fields": {
        "title": "a.job-name, .job-name",
        "area": ".company-location, .job-area",       # 新版「广州·黄埔区·大沙」
        "salary": ".job-salary, .salary",
        "company": ".boss-name, .company-name a",     # 新版公司名在 boss-info 内
        "link": "a.job-name, a.job-card-left",        # 详情页 href（相对路径）
        # 经验/学历等标签（拼为 experience 字段）
        "experience": ".tag-list li, .filter-labels li",
    },
    # JD 正文容器：按优先级尝试，取第一个文本长度达标的候选
    "jd_selectors": [
        ".job-sec-text",
        ".job-detail-section .job-sec-text",
        ".job-detail-section .text",
        ".job-detail .text",
        ".job-detail-section",
        ".detail-content .text",
        "[class*='job-sec-text']",
    ],
    # 选择器全部失效时的兜底：找"含 JD 关键词且文本足够长"的最小容器。
    # 站点改版只改类名不改业务措辞，这条比类名选择器更耐改版。
    "jd_keywords": [
        "岗位职责", "职位描述", "工作职责", "任职要求",
        "任职资格", "职位要求", "岗位要求", "工作内容",
    ],
    "jd_min_chars": 80,
    # 兜底扫描时的候选块级元素（在全页范围内找最小达标容器）
    "jd_block_scan": "div, section, article, main",
    # 未登录/风控验证页特征：命中即判定渠道不可用而非空结果
    "block_markers": [".nc_iconfont.btn_slide", "#wrap .btn-next", "text=安全验证", ".geetest_panel"],
}

# Boss直聘 9 位城市编码映射（未配置时全国检索）
BOSS_CITY_CODES: dict[str, str] = {
    "全国": "",
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "南京": "101190100",
    "武汉": "101200100",
    "西安": "101110100",
    "厦门": "101230200",
    "长沙": "101250100",
    "苏州": "101190400",
    "天津": "101030100",
    "重庆": "101040100",
    "郑州": "101180100",
    "青岛": "101120200",
    "合肥": "101220100",
    "福州": "101230100",
    "济南": "101120100",
    "大连": "101070200",
    "宁波": "101210400",
    "东莞": "101281600",
    "佛山": "101280800",
    "无锡": "101190200",
    "昆明": "101290100",
    "南昌": "101240100",
    "沈阳": "101070100",
    "长春": "101060100",
    "哈尔滨": "101050100",
    "石家庄": "101090100",
    "太原": "101100100",
    "南宁": "101300100",
    "海口": "101310100",
    "贵阳": "101260100",
    "兰州": "101160100",
    "银川": "101170100",
    "西宁": "101150100",
    "呼和浩特": "101080100",
    "乌鲁木齐": "101130100",
}

# 猎聘 3 位城市编码映射（猎聘前端忽略 city 参数，只认 dqs 编码）
LIEPIN_CITY_CODES: dict[str, str] = {
    "北京": "010",
    "上海": "020",
    "广州": "050",
    "深圳": "060",
    "杭州": "070",
    "成都": "080",
    "南京": "090",
    "苏州": "100",
    "武汉": "110",
    "天津": "130",
    "西安": "270",
    "重庆": "040",
    "长沙": "190",
    "郑州": "170",
    "青岛": "200",
    "厦门": "120",
    "合肥": "140",
    "济南": "150",
    "大连": "210",
    "福州": "240",
}

LIEPIN_PATTERNS: dict = {
    # 搜索页：dqs 参数为城市代码
    "search_url": "https://www.liepin.com/zhaopin/?key={query}{city_param}",
    "job_card": ".job-card-pc-container, div.job-detail-box, .job-list-item",
    "wait_selector": "a[data-nick='job-detail-job-info'], .job-card-pc-container, .job-detail-box",
    "card_anchor": "a[data-nick='job-detail-job-info']",
    "fields": {
        "title": "a[data-nick='job-detail-job-info'] div.ellipsis-1, a[data-nick='job-detail-job-info'] .ellipsis-1, .ellipsis-1",
        "company": "div[data-nick='job-detail-company-info'] .ellipsis-1, div[data-nick='job-detail-company-info'], .company-name",
        "area": "a[data-nick='job-detail-job-info'] span.ellipsis-1, .job-dq-box, [class*='job-dq']",
        "link": "a[data-nick='job-detail-job-info'], a[href*='/job/'], a[href*='/lptjob/']",
    },
    # JD 正文容器：按优先级尝试，取第一个文本长度达标的候选
    "jd_selectors": [
        ".job-intro-container .paragraph",
        ".job-intro-container",
        ".job-description",
        "#job-intro",
        ".job-detail-box .paragraph",
        "[class*='job-intro']",
    ],
    # 选择器全部失效时的兜底（同 BOSS_PATTERNS 的说明）
    "jd_keywords": [
        "岗位职责", "职位描述", "工作职责", "任职要求",
        "任职资格", "职位要求", "岗位要求", "工作内容",
    ],
    "jd_min_chars": 80,
    # 兜底扫描时的候选块级元素（在全页范围内找最小达标容器）
    "jd_block_scan": "div, section, article, main",
    # 风控验证页特征：命中即判定渠道进入验证墙
    "block_markers": [
        "text=安全中心-验证码",
        "text=行为异常",
        "text=请进行安全验证",
        "text=请完成安全验证",
        "text=安全验证",
        ".geetest_panel",
    ],
}

# 消息页（N2 投递打招呼 / E 批次 HR 回复监听复用）
BOSS_MESSAGE_PATTERNS: dict = {
    "message_url": "https://www.zhipin.com/web/geek/chat",
    "send_box": "#chat-input",
    "send_btn": ".btn-send",
    "msg_bubble_sent": ".message-system .my-message, .msg-item.self .text",
}

