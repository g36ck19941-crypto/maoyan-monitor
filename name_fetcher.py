import re

from playwright.sync_api import Page, sync_playwright


def _clean_title(title: str) -> str:
    if not title:
        return ""
    # 去掉 " - 猫眼电影" / " | 猫眼电影" / " 猫眼电影" 等尾巴
    title = re.split(r"\s*[-_|–—]\s*猫眼电影", title)[0].strip()
    title = re.sub(r"\s*[-_|–—]\s*猫眼电影$", "", title).strip()
    return title.strip()


def fetch_movie_name(page: Page, movie_url: str) -> str:
    page.goto(movie_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1000)

    # 优先取 og:title
    try:
        og = page.locator("meta[property='og:title']").first
        if og.count() > 0:
            content = og.get_attribute("content")
            if content and content.strip():
                return content.strip()
    except Exception:
        pass

    return _clean_title(page.title())


def fetch_cinema_name(page: Page, cinema_url: str) -> str:
    page.goto(cinema_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1000)

    # 优先取 og:title
    try:
        og = page.locator("meta[property='og:title']").first
        if og.count() > 0:
            content = og.get_attribute("content")
            if content and content.strip():
                return content.strip()
    except Exception:
        pass

    # 页面面包屑可能有：猫眼电影 > 影院 > 万达影城（...）
    try:
        body = page.inner_text("body")
        match = re.search(r"猫眼电影\s*>\s*影院\s*>\s*(.+)", body)
        if match:
            name = match.group(1).splitlines()[0].strip()
            if name:
                return name
    except Exception:
        pass

    return _clean_title(page.title())


def fetch_names(
    movie_url: str,
    cinema_urls: list[str],
    profile_dir: str | None = None,
) -> tuple[str, list[str]]:
    """
    使用真实浏览器批量获取电影名和影院名。

    优先使用已登录的 profile，避免猫眼 403。
    如果 profile 正被监控进程占用，会抛出异常，需要先停止监控。
    """
    with sync_playwright() as p:
        if profile_dir:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
                viewport={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            browser = None
        else:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
            )
            context = browser.new_context(
                locale="zh-CN",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

        try:
            movie_name = fetch_movie_name(page, movie_url) if movie_url else ""
            cinema_names = []
            for url in cinema_urls:
                if url:
                    cinema_names.append(fetch_cinema_name(page, url))
                else:
                    cinema_names.append("")
        finally:
            context.close()
            if browser:
                browser.close()
    return movie_name, cinema_names
