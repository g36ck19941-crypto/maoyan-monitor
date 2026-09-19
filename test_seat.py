"""
自动占座调试脚本。

用途：
  在目标电影真正开售前，用同一家影院“已经开售/预售的其他电影”来调试座位定位。
  这样等目标电影一开售，自动占座代码已经是调好的，不会临时抓瞎。

用法：
  python test_seat.py "https://www.maoyan.com/cinemas/12345?movieId=6789"
"""
import sys

import yaml

from maoyan_client import MaoyanClient
from showtime_selector import (
    _clickable_ancestor,
    _container_text,
    _text_candidates,
    debug_candidates,
    select_showtime,
)


def load_config():
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def debug_seat_page(url: str, profile_dir: str | None = None):
    config = load_config()

    if profile_dir is None:
        profile_dir = config.get("user_data_dir", "./profile")

    client = MaoyanClient(
        user_data_dir=profile_dir,
        headless=config.get("headless", False),
    )
    client.start()
    page = client.page

    try:
        print("=" * 60)
        print("1. 打开测试场次页：", url)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        body_text = page.inner_text("body")
        if "选座购票" in body_text or "立即购票" in body_text:
            print(">>> 页面存在购票按钮，可以继续测试座位定位")
        else:
            print(">>> 页面没有找到购票按钮，请确认这是一个已经开售的场次页")
            return

        # 2. 按时间段/影厅偏好选择场次并点击“选座购票”
        print(">>> 尝试按偏好选择场次...")
        selected = select_showtime(page, config, date_str=config.get("preferred_date") or "")
        print(">>> 场次选择结果:", "成功/无需选择" if selected else "没有符合偏好的场次")

        if not selected:
            print("当前页面有场次按钮，但没有符合你设置的 time/hall 偏好的场次。")
            print("请检查 config.yaml 里的 preferred_start_time / preferred_end_time / preferred_hall_keyword。")
            print("\n>>> 诊断信息：")
            print(debug_candidates(page))
            print("\n>>> 当前页面文本片段：")
            print(page.inner_text("body")[:1500])

            print("\n>>> 可见“选座购票”元素 HTML（前5个）：")
            visible_texts = [c for c in _text_candidates(page) if c.is_visible()]
            for idx, candidate in enumerate(visible_texts[:5]):
                print(f"\n--- 第 {idx + 1} 个 ---")
                print("元素 HTML:")
                print(candidate.evaluate("(el) => el.outerHTML")[:500])
                anc = _clickable_ancestor(candidate)
                print("最近可点击祖先 HTML:")
                print(anc.evaluate("(el) => el.outerHTML")[:500])
                print("容器文本:")
                print(_container_text(candidate)[:500])
            return

        page.wait_for_timeout(3000)

        # 2.1 如果点击后打开了新标签页/弹窗，自动切换过去
        all_pages = client.context.pages
        print(f">>> 当前打开的页面数: {len(all_pages)}")
        for i, p in enumerate(all_pages):
            print(f"    page[{i}]: {p.url}")

        if len(all_pages) > 1:
            page = all_pages[-1]
            page.wait_for_timeout(1000)

        print(">>> 当前 URL：", page.url)

        # 2.2 打印当前页面可见文本片段，方便判断是否卡在“选场次”步骤
        body_text = page.inner_text("body")[:1500]
        print("\n>>> 当前页面文本片段：")
        print(body_text.replace("\n\n", "\n"))
        print("=" * 60)

        # 2.3 检查 iframe，座位图可能在 iframe 里
        print("\n>>> iframe/frame 数量:", len(page.frames))
        for frame in page.frames:
            print("    frame:", frame.url)

        # 3. 查找常见座位元素（主页面 + iframe 都查）
        selectors = [
            "[data-row]",
            "[data-col]",
            "[data-rowindex]",
            "[data-colindex]",
            ".seat",
            ".seat-item",
            "[class*='seat']",
        ]

        for selector in selectors:
            total_count = 0
            sample_html = None
            sample_frame = None

            for frame in page.frames:
                locators = frame.locator(selector)
                count = locators.count()
                total_count += count

                if count > 0 and sample_html is None:
                    sample_html = locators.first.evaluate("(el) => el.outerHTML")
                    sample_frame = frame.url

            print(f"\n选择器 {selector} 数量(含iframe): {total_count}")

            if sample_html:
                print(f"第一个元素来自 frame: {sample_frame}")
                print(sample_html[:500])

        # 4. 尝试定位 config 里的最佳座位
        best_row, best_col = config.get("best_seat", [7, 5])
        print("\n" + "=" * 60)
        print(f"尝试定位最佳座位: 第 {best_row} 排 {best_col} 座")

        found = False
        for frame in page.frames:
            for selector in [
                f'[data-row="{best_row}"][data-col="{best_col}"]',
                f'[data-rowindex="{best_row}"][data-colindex="{best_col}"]',
                f'[data-row-id="{best_row}"][data-no="{best_col}"]',
                f'[data-row-id="{best_row}"][data-column-id="{best_col}"]',
            ]:
                locator = frame.locator(selector).first
                if locator.count() > 0:
                    found = True
                    print("命中选择器:", selector)
                    print("所在 frame:", frame.url)
                    print(locator.evaluate("(el) => el.outerHTML")[:500])
                    break
            if found:
                break

        if not found:
            print("没有命中现有选择器。")
            print("如果当前页面有多个场次/时间，请先手动选择一个场次，再重新运行本脚本。")
            print("请把上面的页面文本和 HTML 发给我，我来帮你调整。")

        print("\n调试结束。请把上面输出的内容发给我。")

    finally:
        client.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_seat.py \"https://www.maoyan.com/cinemas/12345?movieId=6789\" [可选profile目录]")
        sys.exit(1)

    url = sys.argv[1]
    profile_dir = sys.argv[2] if len(sys.argv) > 2 else None
    debug_seat_page(url, profile_dir)
