import re
from datetime import date, datetime, timedelta

from playwright.sync_api import Page


def _to_minutes(time_str: str) -> int:
    # 兼容中文全角冒号，例如 20：00
    time_str = (time_str or "").replace("：", ":")
    h, m = time_str.split(":")
    return int(h) * 60 + int(m)


def _safe_minutes(time_str: str) -> int | None:
    try:
        return _to_minutes(time_str)
    except Exception:
        return None


def _extract_time(text: str) -> str | None:
    # 兼容页面/配置里的全角冒号
    text = (text or "").replace("：", ":")
    matches = re.findall(r"(\d{1,2}):(\d{2})", text)
    for h, m in matches:
        hh, mm = int(h), int(m)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm}"
    return None


def _button_candidates(page: Page):
    """只找 a/button/[role=button] 这类可点击元素。"""
    texts = ["选座购票", "立即购票"]

    for frame in page.frames:
        for text in texts:
            locator = frame.locator("a, button, [role='button']").filter(
                has_text=text
            )
            for i in range(locator.count()):
                yield locator.nth(i)


def _buy_btn_candidates(page: Page):
    """
    猫眼网页版实际的“选座购票”通常是：
      <span class="buy-btn normal" data-act="show-click" data-index="1">选座购票</span>
    不是 a/button，所以要单独按 class/data-act 找。
    """
    for frame in page.frames:
        locator = frame.locator(
            "[class*='buy-btn'], [data-act='show-click']"
        ).filter(has_text="选座购票")
        for i in range(locator.count()):
            yield locator.nth(i)


def _text_candidates(page: Page):
    """兜底：直接找包含“选座购票/立即购票”的元素。"""
    texts = ["选座购票", "立即购票"]

    for frame in page.frames:
        for text in texts:
            locator = frame.get_by_text(text, exact=False)
            for i in range(locator.count()):
                candidate = locator.nth(i)
                tag = candidate.evaluate("(el) => el.tagName.toLowerCase()")
                if tag in ("html", "body"):
                    continue
                yield candidate


def _clickable_ancestor(locator):
    """找到当前元素最近的可点击祖先/自身。"""
    clickable = locator.locator(
        "xpath=ancestor-or-self::*[self::a or self::button or @onclick or @role='button' or contains(@class,'buy') or contains(@class,'btn') or contains(@class,'choose') or contains(@class,'select') or contains(@class,'ticket')][1]"
    )
    if clickable.count() > 0:
        return clickable.first
    return locator


def _container_text(locator, max_levels: int = 6) -> str:
    """向上收集容器文本，用于拿到场次时间、影厅信息。"""
    return locator.evaluate(
        """(el) => {
            let cur = el;
            const texts = [];
            for (let i = 0; i < %d && cur; i++) {
                const t = (cur.innerText || '').trim();
                if (t && texts.indexOf(t) === -1) {
                    texts.push(t);
                }
                cur = cur.parentElement;
            }
            return texts.join(' | ');
        }""" % max_levels
    )


def _click_popup_buy_link(page: Page) -> bool:
    """
    点击弹出卡片里的“直接选座购票”。

    猫眼网页版真实弹窗 HTML：
      <a class="modal-buy-btn normal"
         data-act="modal-show-click"
         href="/xseats/...?movieId=...&cinemaId=...">
        直接选座购票
      </a>
    """
    # 1. 优先按真实 class / data-act 找弹窗购票链接
    for selector in [".modal-buy-btn", "[data-act='modal-show-click']"]:
        locator = page.locator(selector).filter(has_text="直接选座购票")
        for i in range(locator.count()):
            candidate = locator.nth(i)
            if candidate.is_visible():
                candidate.click(force=True)
                return True

    # 2. 兜底：找“直接选座购票”
    for keyword in ["直接选座购票", "立即选座购票", "直接选座"]:
        locator = page.get_by_text(keyword, exact=False)
        for i in range(locator.count()):
            candidate = locator.nth(i)
            if candidate.is_visible():
                _clickable_ancestor(candidate).click(force=True)
                return True

    # 3. 最后兜底：在“扫码/下载APP”弹层里找“选座购票”
    markers = ["扫码", "下载APP", "下载 App", "APP购票"]
    for marker in markers:
        for candidate in _text_candidates(page):
            if not candidate.is_visible():
                continue
            container = _container_text(candidate)
            if marker.lower() in container.lower() and "选座购票" in container:
                _clickable_ancestor(candidate).click(force=True)
                return True

    return False


def _parse_date(date_str: str):
    """把 YYYY-MM-DD 或 today/tomorrow 转成 date 对象。"""
    date_str = (date_str or "").strip().lower()
    if not date_str:
        return None
    if date_str in ("today", "今天"):
        return date.today()
    if date_str in ("tomorrow", "明天"):
        return date.today() + timedelta(days=1)
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        pass
    try:
        return datetime.strptime(date_str, "%Y/%m/%d").date()
    except ValueError:
        return None


def dismiss_modal(page: Page, timeout: int = 1500) -> bool:
    """
    自动关闭猫眼页面上的提示弹窗。

    例如选择非当天日期时会出现：
      <div class="modal-container"> 您选择的是某月某天 ... </div>
    它会挡住座位点击，需要先点掉。
    """
    try:
        page.wait_for_selector(".modal-container", state="visible", timeout=timeout)
    except Exception:
        pass

    modal_selectors = [
        ".modal-container",
        ".modal",
        ".dialog",
        ".popup",
        "[class*='modal']",
    ]

    for selector in modal_selectors:
        modals = page.locator(selector)
        for i in range(modals.count()):
            modal = modals.nth(i)
            if not modal.is_visible():
                continue

            # 1. 常见确认按钮文字
            for text in ["知道了", "我知道了", "确定", "继续选座", "继续", "好的", "关闭", "取消"]:
                btn = modal.get_by_text(text, exact=False).first
                try:
                    if btn.count() > 0 and btn.is_visible():
                        _clickable_ancestor(btn).click(force=True)
                        page.wait_for_timeout(300)
                        return True
                except Exception:
                    continue

            # 2. 关闭图标
            close = modal.locator("[class*='close'], [class*='icon-close']").first
            try:
                if close.count() > 0 and close.is_visible():
                    close.click(force=True)
                    page.wait_for_timeout(300)
                    return True
            except Exception:
                continue

    return False


def select_date(page: Page, date_str: str) -> bool:
    """点击猫眼场次页上的日期 Tab（今天/明天/9月2 等）。"""
    target = _parse_date(date_str)
    if target is None:
        return False

    today = date.today()
    labels = []
    if target == today:
        labels.append("今天")
    elif target == today + timedelta(days=1):
        labels.append("明天")

    labels.append(f"{target.month}月{target.day}")
    labels.append(f"{target.month}月{target.day:02d}")

    for label in labels:
        for frame in page.frames:
            for exact in (True, False):
                locator = frame.get_by_text(label, exact=exact)
                for i in range(locator.count()):
                    candidate = locator.nth(i)
                    if candidate.is_visible():
                        _clickable_ancestor(candidate).click(force=True)
                        page.wait_for_timeout(800)
                        # 非当天日期可能弹出确认提示，自动点掉
                        dismiss_modal(page)
                        return True
    return False


def debug_candidates(page: Page) -> dict:
    """输出候选购票按钮的诊断信息，方便定位问题。"""
    buttons = list(_button_candidates(page))
    buy_btns = list(_buy_btn_candidates(page))
    texts = list(_text_candidates(page))
    return {
        "button_total": len(buttons),
        "button_visible": sum(1 for c in buttons if c.is_visible()),
        "buy_btn_total": len(buy_btns),
        "buy_btn_visible": sum(1 for c in buy_btns if c.is_visible()),
        "text_total": len(texts),
        "text_visible": sum(1 for c in texts if c.is_visible()),
        "frames": [f.url for f in page.frames],
    }


def select_showtime(
    page: Page,
    config: dict,
    date_str: str = "",
    attempt_index: int = 0,
) -> bool:
    """
    根据 config 中的日期、时间段和影厅关键词，自动选择一个场次并点击“选座购票”。

    猫眼网页版完整流程：
      1. 点击场次右侧“选座购票”弹出下载 App 卡片
      2. 再点击卡片里的“直接选座购票”进入选座页

    参数：
      date_str:     可选，例如 "2026-09-02"、"today"、"tomorrow"。
      attempt_index:同一天内尝试第几个符合条件的场次，0 表示第一个。

    返回：
      True  = 已成功点击到真实购票入口，或当前页面不需要选场次
      False = 没有找到可用的场次/没有点到真实购票入口
    """
    # 0. 如果指定了日期，先切换日期 Tab
    if date_str:
        if not select_date(page, date_str):
            return False

    # 1. 找到场次列表里的“选座购票”（跳过表头）
    # 优先顺序：真正的 a/button → 猫眼 buy-btn span → 文本兜底
    candidates = [c for c in _button_candidates(page) if c.is_visible()]
    if not candidates:
        candidates = [c for c in _buy_btn_candidates(page) if c.is_visible()]
    if not candidates:
        candidates = [c for c in _text_candidates(page) if c.is_visible()]

    if not candidates:
        # 页面上完全没有可见的“选座购票/立即购票”，可能已经在选座页
        has_any = any(True for _ in _button_candidates(page)) or any(
            True for _ in _text_candidates(page)
        )
        return not has_any

    preferred_start = (config.get("preferred_start_time") or "").strip()
    preferred_end = (config.get("preferred_end_time") or "").strip()
    hall_keyword = (config.get("preferred_hall_keyword") or "").strip()

    # 没有偏好时，跳过表头，收集所有带放映时间的场次
    if not preferred_start and not preferred_end and not hall_keyword:
        matched = []
        for candidate in candidates:
            text = _container_text(candidate)
            if _extract_time(text) is not None:
                matched.append(candidate)
        if not matched:
            matched = list(candidates)
    else:
        matched = []
        for candidate in candidates:
            text = _container_text(candidate)
            show_time = _extract_time(text)
            hall_text = text

            if preferred_start or preferred_end:
                if show_time is None:
                    continue
                minutes = _to_minutes(show_time)
                start_min = _safe_minutes(preferred_start)
                end_min = _safe_minutes(preferred_end)
                if start_min is not None and minutes < start_min:
                    continue
                if end_min is not None and minutes > end_min:
                    continue

            if hall_keyword and hall_keyword.lower() not in hall_text.lower():
                continue

            matched.append(candidate)

    # 1.5 如果页面同时有多部电影，优先只保留属于目标电影的那一批场次
    movie_name = (config.get("movie_name") or "").strip()
    if movie_name:
        movie_matched = [
            candidate for candidate in matched
            if movie_name in _container_text(candidate)
        ]
        if movie_matched:
            matched = movie_matched

    # 1.6 按 attempt_index 选择第几个场次；没有更多场次则返回 False
    if attempt_index >= len(matched):
        return False
    row_candidate = matched[attempt_index]

    # 2. 点击该场次右侧的“选座购票”，会弹出 App 下载卡片
    before_url = page.url
    _clickable_ancestor(row_candidate).click(force=True)

    # 3. 如果弹出了新页面/已跳转，直接算成功
    try:
        page.wait_for_timeout(500)
        if len(page.context.pages) > 1 or page.url != before_url:
            return True
    except Exception:
        pass

    # 4. 等待弹窗里的“直接选座购票”出现，然后点击它
    try:
        page.wait_for_selector(
            ".modal-buy-btn, [data-act='modal-show-click']",
            state="visible",
            timeout=5000,
        )
    except Exception:
        pass

    return _click_popup_buy_link(page)
