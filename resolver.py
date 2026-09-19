import re
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import Page


def extract_movie_id(movie_url: str) -> str | None:
    match = re.search(r"/films/(\d+)", movie_url or "")
    return match.group(1) if match else None


def extract_cinema_id(cinema_url: str) -> str | None:
    # 猫眼链接可能是 /cinema/123 或 /cinemas/123，都支持
    match = re.search(r"/cinemas?/(\d+)", cinema_url or "")
    return match.group(1) if match else None


def build_combined_url(movie_url: str, cinema_url: str) -> str:
    """根据电影 URL 和影院 URL，直接拼出“某电影在某影院”的场次页 URL。

    例如：
      cinema_url = https://www.maoyan.com/cinema/1485?poi=79809
      movie_id   = 1462628
      结果：
      https://www.maoyan.com/cinema/1485?poi=79809&movieId=1462628
    """
    movie_id = extract_movie_id(movie_url)
    cinema_id = extract_cinema_id(cinema_url)

    if not movie_id or not cinema_id:
        raise ValueError("无法从 movie_url/cinema_url 中解析电影 ID 和影院 ID")

    parsed = urlparse(cinema_url if cinema_url.startswith("http") else "https://" + cinema_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["movieId"] = [movie_id]

    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(query, doseq=True)}"


def resolve_target_url(page: Page, config: dict) -> str | None:
    """
    返回“某电影在某影院”的场次页 URL。

    现在不再需要去页面里“找链接”，直接根据 movie_url + cinema_url 拼出来：
      https://www.maoyan.com/cinema/1485?poi=79809&movieId=1462628

    如果还没有排片，这个页面可能显示“暂无场次”，脚本继续轮询即可。
    """
    direct_url = (config.get("url") or "").strip()
    if direct_url and direct_url != "auto":
        return direct_url

    movie_url = (config.get("movie_url") or "").strip()
    cinema_url = (config.get("cinema_url") or "").strip()
    movie_id = extract_movie_id(movie_url)
    cinema_id = extract_cinema_id(cinema_url)

    if not movie_id or not cinema_id:
        missing = []
        if not movie_url:
            missing.append("movie_url")
        if not cinema_url:
            missing.append("cinema_url")
        if not missing and not movie_id:
            missing.append("movie_url（无法解析电影ID）")
        if not missing and not cinema_id:
            missing.append("cinema_url（无法解析影院ID）")
        raise ValueError(
            "配置不完整：" + "、".join(missing) +
            "。请填写 movie_url 和 cinema_url，或直接填写 url。"
        )

    return build_combined_url(movie_url, cinema_url)


def _find_from_cinema_page(page: Page, movie_id: str, config: dict) -> str | None:
    """
    打开影院页，找包含该 movieId 的场次链接。
    例如：
      https://www.maoyan.com/cinemas/12345?movieId=6789
    """
    cinema_url = (config.get("cinema_url") or "").strip()
    page.goto(cinema_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1000)

    # 优先按链接特征找，最稳：href 里带 movieId=xxx
    locator = page.locator(f'a[href*="movieId={movie_id}"]').first
    if locator.count() > 0 and locator.is_visible():
        href = locator.get_attribute("href")
        if href:
            return page.urljoin(href)

    # 兜底：按电影名找链接
    movie_name = config.get("movie_name", "")
    if movie_name:
        link = page.locator(f"a:has-text('{movie_name}')").first
        if link.count() > 0 and link.is_visible():
            href = link.get_attribute("href")
            if href and "movieId" in href:
                return page.urljoin(href)

    return None


def _find_from_movie_page(page: Page, cinema_id: str, config: dict) -> str | None:
    """
    打开电影页，找包含该影院 ID 的场次链接。
    例如：
      https://www.maoyan.com/cinemas/12345?movieId=6789
    """
    movie_url = (config.get("movie_url") or "").strip()
    page.goto(movie_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1000)

    # 优先按链接特征找：href 里带 /cinema/xxx 或 /cinemas/xxx
    for path in ("/cinemas/", "/cinema/"):
        locator = page.locator(f'a[href*="{path}{cinema_id}"]').first
        if locator.count() > 0 and locator.is_visible():
            href = locator.get_attribute("href")
            if href:
                return page.urljoin(href)

    # 兜底：按影院名找链接
    cinema_name = config.get("cinema_name", "")
    if cinema_name:
        link = page.locator(f"a:has-text('{cinema_name}')").first
        if link.count() > 0 and link.is_visible():
            href = link.get_attribute("href")
            if href and ("/cinema/" in href or "/cinemas/" in href):
                return page.urljoin(href)

    return None
