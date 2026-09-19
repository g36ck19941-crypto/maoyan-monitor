from pathlib import Path

from playwright.sync_api import sync_playwright


class MaoyanClient:
    """
    Playwright 封装。
    使用 launch_persistent_context 保存登录态，
    以后每次运行都不需要重新扫码登录。
    """

    def __init__(self, user_data_dir: str = "./profile", headless: bool = False):
        self.user_data_dir = str(Path(user_data_dir).resolve())
        self.headless = headless
        self._playwright = None
        self.context = None
        self.page = None

    def start(self):
        self._playwright = sync_playwright().start()

        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def close(self):
        if self.context:
            self.context.close()
        if self._playwright:
            self._playwright.stop()
