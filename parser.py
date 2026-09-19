from playwright.sync_api import Page


def parse_available_sessions(page: Page, config: dict) -> list[dict]:
    """
    解析当前页面是否存在可购买 / 预售 / 点映场次。

    注意：猫眼页面结构可能变化，这里的定位方式是基础框架。
    实际使用时要根据页面 DOM 调整 selectors。
    """
    results = []

    # 方法 1：直接看页面里有没有“选座购票 / 预售 / 点映”按钮
    keywords = config.get("keywords", [])

    for keyword in keywords:
        locator = page.get_by_text(keyword, exact=False).first

        try:
            if locator.is_visible(timeout=1000):
                session_text = locator.inner_text()[:200]
                results.append(
                    {
                        "keyword": keyword,
                        "text": session_text,
                    }
                )
        except Exception:
            continue

    # 方法 2：直接读取整页文本兜底
    if not results:
        body_text = page.inner_text("body")
        for keyword in keywords:
            if keyword in body_text:
                results.append({"keyword": keyword, "text": "页面文本命中"})
                break

    return results
