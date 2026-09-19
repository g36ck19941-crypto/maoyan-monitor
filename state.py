from pathlib import Path


class State:
    """记录已经通知过的场次 key，避免每次轮询都重复轰炸微信。

    notified：开售通知只发一次（去重）。
    ordered ：只有真正下单成功才算完成；占座失败必须允许下一轮继续重试，
              所以它单独存一份，绝不和 notified 混用。
    """

    def __init__(
        self,
        path: str = "./state/notified.txt",
        ordered_path: str = "./state/ordered.txt",
    ):
        self.path = Path(path)
        self.ordered_path = Path(ordered_path)
        self.notified: set[str] = set()
        self.ordered: set[str] = set()
        self.load()

    def load(self):
        if self.path.exists():
            self.notified = set(self.path.read_text(encoding="utf-8").splitlines())
        if self.ordered_path.exists():
            self.ordered = set(self.ordered_path.read_text(encoding="utf-8").splitlines())

    def save(self):
        self._write(self.path, self.notified)
        self._write(self.ordered_path, self.ordered)

    @staticmethod
    def _write(path: Path, items: set[str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(sorted(items)), encoding="utf-8")

    def already_notified(self, key: str) -> bool:
        return key in self.notified

    def mark_notified(self, key: str):
        self.notified.add(key)

    def already_ordered(self, key: str) -> bool:
        return key in self.ordered

    def mark_ordered(self, key: str):
        self.ordered.add(key)