from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiagnosticsConfig:
    screenshot_on_error: bool = True
    debug_dir: str = ""

    @classmethod
    def from_env(cls) -> "DiagnosticsConfig":
        v = (os.getenv("ONE_SCREENSHOT_ON_ERROR", "") or "").strip().lower()
        screenshot_on_error = v not in ("0", "false", "no", "off")

        d = (os.getenv("ONE_DEBUG_DIR", "") or "").strip()
        if not d:
            d = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "one_debug"))
        return cls(screenshot_on_error=screenshot_on_error, debug_dir=d)


class UiDiagnostics:
    """集中管理 screenshot/html dump 与简单 UI 文本探测。（Async API）"""

    def __init__(self, cfg: Optional[DiagnosticsConfig] = None) -> None:
        self.cfg = cfg or DiagnosticsConfig.from_env()
        if self.cfg.screenshot_on_error:
            try:
                os.makedirs(self.cfg.debug_dir, exist_ok=True)
            except Exception:
                pass

    async def dump_page(self, page: Optional[Page], prefix: str) -> None:
        if not self.cfg.screenshot_on_error or page is None:
            return
        try:
            png_path = os.path.join(self.cfg.debug_dir, prefix + ".png")
            html_path = os.path.join(self.cfg.debug_dir, prefix + ".html")
            try:
                await page.screenshot(path=png_path, full_page=True)
            except Exception:
                pass
            try:
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(await page.content())
            except Exception:
                pass
        except Exception:
            return

    async def find_first_visible_text(self, page: Page, patterns: List[str]) -> Optional[str]:
        for t in patterns:
            try:
                loc = page.locator(f'text="{t}"').first
                if (await loc.count()) and (await loc.is_visible()):
                    return t
            except Exception:
                continue
        return None

    async def first_visible(self, page: Page, selectors: List[str]):
        """给定一组 selector，返回第一个存在且可见的 Locator（否则 None）。

        注意：这里的 selectors 应是逐条的 CSS/Playwright selector 字符串，
        不要把整个 Python list 的 repr 传给 locator()（例如 '["#a", "#b"]'），
        否则会触发 querySelectorAll 的 SyntaxError。
        """
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if (await loc.count()) and (await loc.is_visible()):
                    return loc
            except Exception:
                continue
        return None