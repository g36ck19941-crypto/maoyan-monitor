from datetime import date, datetime, timedelta

from playwright.sync_api import Page

from showtime_selector import dismiss_modal, select_showtime


def spiral_candidates(center_row: int, center_col: int, max_radius: int = 5):
    """
    以最佳座位为中心，按螺旋顺序生成候选座位。

    顺序示例：
    (7,5) → (7,6) → (7,4) → (6,5) → (8,5) → ...
    """
    yield center_row, center_col

    for radius in range(1, max_radius + 1):
        # 上边一行
        for dc in range(-radius, radius + 1):
            yield center_row - radius, center_col + dc
        # 下边一行
        for dc in range(-radius, radius + 1):
            yield center_row + radius, center_col + dc
        # 左边一列
        for dr in range(-radius + 1, radius):
            yield center_row + dr, center_col - radius
        # 右边一列
        for dr in range(-radius + 1, radius):
            yield center_row + dr, center_col + radius


class SeatSelector:
    def __init__(self, page: Page, config: dict):
        self.page = page
        self.config = config
        self.best_row, self.best_col = config["best_seat"]
        self.max_radius = config.get("max_radius", 5)

    def select(self) -> bool:
        """
        进入选座页并尝试占座。

        如果今天没有可用座位，会自动顺延到明天、后天……最多 max_search_dates 天。
        成功返回 True，失败返回 False。
        """
        # 0. 关键校验：当前页面必须包含目标电影名，避免误买影院正在上映的其他电影
        movie_name = (self.config.get("movie_name") or "").strip()
        if movie_name:
            try:
                page_text = self.page.inner_text("body")
            except Exception:
                page_text = ""
            if movie_name not in page_text:
                print(f"[seat_selector] 当前页面没有目标电影《{movie_name}》，取消自动占座")
                return False

        # 记录场次列表页，后续切换日期时要回到这里
        list_url = self.page.url

        # 从配置中的优先日期开始；没填就从今天开始
        max_dates = int(self.config.get("max_search_dates", 3) or 3)
        start_date = self._parse_start_date()
        if start_date is None:
            start_date = date.today()

        for offset in range(max_dates):
            current_date = start_date + timedelta(days=offset)
            date_str = current_date.strftime("%Y-%m-%d")
            attempt_index = 0

            while True:
                # 需要回到场次列表页的情况：
                # 1) 从下一天重新开始
                # 2) 同一天上一个场次选座失败，需要回列表尝试下一场
                if offset > 0 or attempt_index > 0:
                    self.page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                    self.page.wait_for_timeout(1000)

                print(f"[seat_selector] 尝试日期: {date_str}, 第 {attempt_index + 1} 个场次")
                if not select_showtime(
                    self.page,
                    self.config,
                    date_str=date_str,
                    attempt_index=attempt_index,
                ):
                    print(f"[seat_selector] {date_str} 没有更多可尝试的场次")
                    break

                self.page.wait_for_timeout(2000)

                # 如果点击后打开了新标签页，自动切换过去
                if len(self.page.context.pages) > 1:
                    self.page = self.page.context.pages[-1]
                    self.page.wait_for_timeout(1000)

                # 当前场次选座成功则结束
                if self._try_select_seat():
                    return True

                # 当前场次失败，马上尝试同一日期的下一个场次
                attempt_index += 1

            print(f"[seat_selector] {current_date} 全部场次/可接受座位均失败，尝试下一天...")

        return False

    def _parse_start_date(self):
        preferred = (self.config.get("preferred_date") or "").strip()
        if not preferred:
            return None
        try:
            return datetime.strptime(preferred, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _preferred_seats(self) -> list[tuple[int, int]]:
        """从配置 seats 中解析每张票的优先座位。"""
        result = []
        for item in self.config.get("seats") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    result.append((int(item[0]), int(item[1])))
                except (TypeError, ValueError):
                    continue
        return result

    def _anchor_seat(self, preferred: list[tuple[int, int]]) -> tuple[int, int]:
        """连座/搜索中心：优先用用户指定的第一个座位，否则用 best_seat。"""
        if preferred:
            return preferred[0]
        return self.best_row, self.best_col

    def _acceptable_radius(self) -> int:
        """用户可接受的座位半径；默认和 max_radius 一致。"""
        return int(self.config.get("acceptable_radius", self.max_radius) or self.max_radius)

    def _try_select_seat(self) -> bool:
        """按配置选择座位：支持 1 张或多张、必须连座/不强制连座、每张优先座位。"""
        # 先自动关掉可能存在的日期提示/弹窗，避免遮挡座位
        print("[seat_selector] 检查并关闭弹窗...")
        dismiss_modal(self.page, timeout=2000)

        ticket_count = max(1, int(self.config.get("ticket_count", 1) or 1))
        seat_rule = (self.config.get("seat_rule") or "any").strip().lower()
        preferred = self._preferred_seats()

        if ticket_count <= 1:
            # 单张：优先按用户指定的座位，再按最佳座位附近扩散
            if preferred:
                for row, col in preferred:
                    print(f"[seat_selector] 尝试指定座位: 第 {row} 排 {col} 座")
                    seat = self._find_seat(row, col)
                    if self._is_available(seat) and self._click_seat(seat):
                        return self._confirm_selection()

            for row, col in spiral_candidates(self.best_row, self.best_col, self._acceptable_radius()):
                print(f"[seat_selector] 尝试座位: 第 {row} 排 {col} 座")
                seat = self._find_seat(row, col)
                if self._is_available(seat) and self._click_seat(seat):
                    return self._confirm_selection()
            return False

        if seat_rule == "consecutive":
            print(f"[seat_selector] 需要连座，共 {ticket_count} 张")
            center = self._anchor_seat(preferred)
            return self._try_consecutive_block(ticket_count, center=center)

        # 不强制连座：优先选用户指定的多个座位，不够再用最佳座位附近补
        print(f"[seat_selector] 不强制连座，共 {ticket_count} 张")
        selected = []

        # 第一阶段：优先按用户指定的每张座位
        for row, col in preferred:
            if len(selected) >= ticket_count:
                break
            seat = self._find_seat(row, col)
            if (row, col) not in selected and self._is_available(seat) and self._click_seat(seat):
                selected.append((row, col))
                self.page.wait_for_timeout(300)

        # 第二阶段：如果还不够，从最佳座位附近扩散补足
        if len(selected) < ticket_count:
            for row, col in spiral_candidates(self.best_row, self.best_col, self._acceptable_radius()):
                if len(selected) >= ticket_count:
                    break
                if (row, col) in selected:
                    continue
                seat = self._find_seat(row, col)
                if self._is_available(seat) and self._click_seat(seat):
                    selected.append((row, col))
                    self.page.wait_for_timeout(300)

        if len(selected) >= ticket_count:
            return self._confirm_selection()
        return False

    def _is_available(self, seat) -> bool:
        if not seat:
            return False
        class_name = seat.get_attribute("class") or ""
        disabled_markers = ["disable", "disabled", "sold", "occupied", "lock"]
        return not any(marker in class_name.lower() for marker in disabled_markers)

    def _click_seat(self, seat) -> bool:
        """点击座位，被弹窗挡住时自动关弹窗重试一次。"""
        for attempt in range(2):
            try:
                seat.click(timeout=2000)
                return True
            except Exception as e:
                dismiss_modal(self.page)
                if attempt == 0:
                    continue
                print(f"[seat_selector] 点击座位失败: {e}")
        return False

    def _confirm_selection(self) -> bool:
        """点击确认选座，可选提交订单。"""
        try:
            confirm = self.page.get_by_text("确认选座").first
            if confirm.is_visible(timeout=1000):
                confirm.click(timeout=2000)

                if self.config.get("submit_order", True):
                    submit = self.page.get_by_text("提交订单").first
                    if submit.is_visible(timeout=1000):
                        submit.click(timeout=2000)

                return True
        except Exception as e:
            print(f"[seat_selector] 确认选座失败: {e}")
        return False

    def _try_consecutive_block(self, ticket_count: int, center: tuple[int, int] | None = None) -> bool:
        """在最佳座位/指定中心附近寻找连续座位块，优先连座且尽量靠近中心。"""
        center_row, center_col = center if center else (self.best_row, self.best_col)
        rows = self._near_values(center_row, self._acceptable_radius())
        cols = self._near_values(center_col, self._acceptable_radius())

        for row in rows:
            for start_col in cols:
                block = [(row, start_col + i) for i in range(ticket_count)]
                seats = [self._find_seat(r, c) for r, c in block]

                if all(self._is_available(s) for s in seats):
                    print(f"[seat_selector] 找到连座: {block}")
                    for seat in seats:
                        if not self._click_seat(seat):
                            return False
                        self.page.wait_for_timeout(300)
                    return self._confirm_selection()
        return False

    def _near_values(self, center: int, radius: int):
        """生成从 center 开始向两边扩散的值序列。"""
        values = [center]
        for i in range(1, radius + 1):
            values.append(center - i)
            values.append(center + i)
        return values

    def _find_seat(self, row: int, col: int):
        """
        根据排/列定位座位。

        猫眼网页版不同时期可能用不同属性，常见有：
        - data-row / data-col
        - data-rowindex / data-colindex
        - 通过 class 或 title 匹配

        这里提供多个 fallback。
        """
        selectors = [
            f'[data-row="{row}"][data-col="{col}"]',
            f'[data-rowindex="{row}"][data-colindex="{col}"]',
            # 猫眼 xseats 页面实际座位结构
            f'[data-row-id="{row}"][data-no="{col}"]',
            f'[data-row-id="{row}"][data-column-id="{col}"]',
        ]

        # 主页面和 iframe 都查一遍
        for frame in self.page.frames:
            for selector in selectors:
                locator = frame.locator(selector).first
                if locator.count() > 0:
                    return locator

        # 如果座位图是 Canvas，上面方法无法定位，
        # 需要改为“截图 + 坐标映射 + page.mouse.click(x, y)”。
        return None
