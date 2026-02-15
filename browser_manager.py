from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserConfig:
    accept_language: str = "en-US,en;q=0.9"
    locale: str = "en-US"
    timezone_id: str = "America/New_York"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    viewport_width: int = 1920
    viewport_height: int = 1080
    proxy_url: str = ""
    slow_mo_ms: int = 0
    headless: Optional[bool] = None


class BrowserManager:
    """
    Playwright Async API 浏览器生命周期管理器。
    """

    def __init__(self, verification_id: str, cfg: BrowserConfig):
        self.verification_id = verification_id
        self.cfg = cfg

        self._pw = None
        self.browser = None
        self.context = None
        self.http_client = None

    def _session_state_path(self) -> Path:
        root = (os.getenv("ONE_SESSION_STATE_DIR", "") or "").strip() or os.path.join(
            os.path.dirname(__file__), ".one_state"
        )
        d = Path(root)
        d.mkdir(parents=True, exist_ok=True)
        safe_vid = re.sub(r"[^0-9A-Za-z_-]+", "_", self.verification_id or "unknown")[:80]
        return d / f"sheerid_{safe_vid}.json"

    async def ensure(self) -> None:
        if self.context is not None and self.http_client is not None:
            return

        self._pw = await async_playwright().start()

        env_headless = (os.getenv("ONE_BROWSER_HEADLESS", "") or "").strip()
        if self.cfg.headless is not None:
            headless = self.cfg.headless
        elif env_headless == "1":
            headless = True
        elif env_headless == "0":
            headless = False
        else:
            # 保持旧逻辑：脚本直接运行时偏向有头，服务/导入时偏向无头
            try:
                import __main__

                main_file = getattr(__main__, "__file__", None) or ""
                headless = "sheerid_verifier" not in str(main_file)
            except Exception:
                headless = True

        slow_mo = int(os.getenv("ONE_BROWSER_SLOWMO_MS", str(self.cfg.slow_mo_ms)) or 0)
        self.browser = await self._pw.chromium.launch(headless=headless, slow_mo=slow_mo)

        viewport = {"width": self.cfg.viewport_width, "height": self.cfg.viewport_height}
        behavior = (os.getenv("ONE_BEHAVIOR_PROFILE", "balanced") or "balanced").strip().lower()
        if behavior == "cautious":
            viewport = {"width": 1536, "height": 864}
        elif behavior == "fast":
            viewport = {"width": 1366, "height": 768}

        context_options = {
            "user_agent": self.cfg.user_agent,
            "locale": self.cfg.locale,
            "viewport": viewport,
            "timezone_id": self.cfg.timezone_id,
        }
        if self.cfg.proxy_url:
            context_options["proxy"] = {"server": self.cfg.proxy_url}

        state_path = self._session_state_path()
        if (os.getenv("ONE_SESSION_STATE_DISABLE", "") or "").strip().lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            if state_path.is_file():
                context_options["storage_state"] = str(state_path)

        self.context = await self.browser.new_context(**context_options)
        await self.context.set_extra_http_headers({"Accept-Language": self.cfg.accept_language, "DNT": "1"})
        self.http_client = self.context.request

    async def close(self) -> None:
        try:
            if self.context is not None:
                if (os.getenv("ONE_SESSION_STATE_DISABLE", "") or "").strip().lower() not in (
                    "1",
                    "true",
                    "yes",
                    "on",
                ):
                    try:
                        await self.context.storage_state(path=str(self._session_state_path()))
                    except Exception:
                        pass
                try:
                    await self.context.close()
                except Exception:
                    pass

            if self.browser is not None:
                try:
                    await self.browser.close()
                except Exception:
                    pass

            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
        except Exception:
            pass