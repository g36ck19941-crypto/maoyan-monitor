"""排片探针：每轮把"目标片在该影院的排片状态"追加进 CSV。

目的不是实时告警，而是积累数据：等目标日期真的放票时，
这份 CSV 能回答"票是几点几分放出来的、日期栏几点开始出现那一天"。
只在状态发生变化或到达心跳间隔时写一行，避免每轮刷屏。
"""
import csv
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

FIELDS = [
    "ts",
    "cinema",
    "stage",
    "movie_found",
    "date_ok",
    "sessions",
    "keywords",
    "date_bar_items",
    "date_bar_last",
    "target_date_present",
    "error",
    "url",
]

# 用来判断"状态是否变化"的字段
SIGNATURE_FIELDS = (
    "stage",
    "movie_found",
    "date_ok",
    "sessions",
    "keywords",
    "date_bar_last",
    "target_date_present",
)


class ShowtimeProbe:
    def __init__(self, config: dict | None = None):
        cfg = (config or {}).get("probe") or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.path = Path(str(cfg.get("csv_path", "./state/showtimes.csv")))
        self.heartbeat_seconds = int(cfg.get("heartbeat_minutes", 10) or 10) * 60
        self._last_sig = None
        self._last_write = 0.0

    def date_bar_facts(self, page, target_label: str = "") -> dict:
        """顺手抓日期栏的事实：条目数、最后一项、目标日期在不在。"""
        facts = {"date_bar_items": "", "date_bar_last": "", "target_date_present": ""}
        if not self.enabled:
            return facts
        try:
            loc = page.locator(".date-item")
            n = loc.count()
            facts["date_bar_items"] = n
            texts = []
            for i in range(min(n, 40)):
                t = (loc.nth(i).inner_text() or "").replace("\n", " ").strip()
                if t:
                    texts.append(t)
            if texts:
                facts["date_bar_last"] = texts[-1][:40]
            if target_label:
                facts["target_date_present"] = any(target_label in t for t in texts)
        except Exception as e:
            logger.debug("日期栏探测失败: %s", e)
        return facts

    def record(self, row: dict):
        if not self.enabled:
            return
        sig = tuple(str(row.get(k, "")) for k in SIGNATURE_FIELDS)
        now = time.time()
        changed = sig != self._last_sig
        due = (now - self._last_write) >= self.heartbeat_seconds
        if self._last_sig is not None and not changed and not due:
            return
        self._last_sig = sig
        self._last_write = now
        self._append(row)

    def _append(self, row: dict):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self.path.exists()
            # utf-8-sig 让 Excel 直接双击打开不乱码
            with self.path.open("a", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
                if is_new:
                    writer.writeheader()
                writer.writerow({k: row.get(k, "") for k in FIELDS})
        except Exception as e:
            logger.warning("探针写入失败: %s", e)