"""
获取指定影院/影厅的座位图。

用法：
  python seat_map.py "https://www.maoyan.com/cinema/1485?poi=79809&movieId=1462628" [可选profile目录]

会根据 config.yaml 中的时间段和影厅关键词自动选择场次，
进入座位图后打印座位矩阵，并保存截图到 logs/seat_map.png。
"""
import sys
from pathlib import Path

import yaml

from maoyan_client import MaoyanClient
from showtime_selector import select_showtime


def load_config():
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def collect_seats(page):
    """收集座位数据，返回 {(row, col): class}。"""
    seats = {}

    # 兼容多种猫眼座位 DOM 结构
    selectors = [
        "[data-row][data-col]",
        "[data-rowindex][data-colindex]",
        "[data-row-id][data-no]",
        "[data-row-id][data-column-id]",
    ]

    for frame in page.frames:
        for selector in selectors:
            locs = frame.locator(selector)
            for i in range(locs.count()):
                el = locs.nth(i)

                # 行：优先取第一个数字形式的行号
                row = None
                for attr in ("data-row", "data-rowindex", "data-row-id"):
                    val = el.get_attribute(attr)
                    if val and val.isdigit():
                        row = val
                        break

                # 列：猫眼真实座位里 data-no 是 "1-7-5" 这种非数字，
                # 而 data-column-id 才是真正的列号 5，所以优先取数字列号
                col = None
                for attr in ("data-col", "data-colindex", "data-column-id", "data-no"):
                    val = el.get_attribute(attr)
                    if val and val.isdigit():
                        col = val
                        break

                if not row or not col:
                    continue

                class_name = el.get_attribute("class") or ""
                seats[(int(row), int(col))] = class_name

    return seats


def is_available(class_name: str) -> bool:
    disabled_markers = ["disable", "disabled", "sold", "occupied", "lock", "sell"]
    lower = class_name.lower()
    return not any(marker in lower for marker in disabled_markers)


def print_seat_map(seats: dict):
    if not seats:
        print("没有找到可解析的座位元素，可能需要进一步调试。")
        return

    rows = sorted({r for r, c in seats})
    cols = sorted({c for r, c in seats})

    print("\n座位图（. = 可选, X = 已占/不可选）")
    header = "排\\列 " + " ".join(f"{c:>3}" for c in cols)
    print(header)

    for r in rows:
        line = f"{r:>3}  "
        for c in cols:
            cls = seats.get((r, c), "")
            if (r, c) in seats and is_available(cls):
                line += "  . "
            else:
                line += "  X "
        print(line)

    available = [(r, c) for (r, c), cls in seats.items() if is_available(cls)]
    print("\n可选座位数量:", len(available))
    print("可选座位示例（前50个）:", available[:50])


def main():
    if len(sys.argv) < 2:
        print("用法: python seat_map.py \"https://www.maoyan.com/cinemas/12345?movieId=6789\" [可选profile目录]")
        sys.exit(1)

    url = sys.argv[1]
    profile_dir = sys.argv[2] if len(sys.argv) > 2 else None

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
        print("打开页面:", url)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        print("按偏好选择场次...")
        ok = select_showtime(page, config, date_str=config.get("preferred_date") or "")
        if not ok:
            print("没有符合时间/影厅偏好的场次，请检查 config.yaml。")
            return

        page.wait_for_timeout(3000)

        if len(page.context.pages) > 1:
            page = page.context.pages[-1]
            page.wait_for_timeout(1000)

        print("当前 URL:", page.url)

        # 保存截图
        Path("logs").mkdir(exist_ok=True)
        page.screenshot(path="logs/seat_map.png", full_page=True)
        print("截图已保存: logs/seat_map.png")

        seats = collect_seats(page)
        print_seat_map(seats)

    finally:
        client.close()


if __name__ == "__main__":
    main()
