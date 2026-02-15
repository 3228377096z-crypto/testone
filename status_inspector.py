from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from playwright.async_api import BrowserContext, Page

from . import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UiCheckResult:
    ok: bool
    hit: str = ""
    reason: str = ""


class StatusInspector:
    """
    收敛 SheerID 的 “pending/error 时打开 verify 页扫描提示词 + dump” 逻辑（Async API）。
    """

    def __init__(
        self,
        *,
        ui,
        wait_network_idle_soft,
        debug_enabled_fn,
    ) -> None:
        self._ui = ui
        self._wait_network_idle_soft = wait_network_idle_soft
        self._debug_enabled = debug_enabled_fn

    @staticmethod
    def default_patterns() -> list[str]:
        return [
            "could not confirm",
            "document is insufficient",
            "add document",
            "try again",
            "unable to verify",
            "needs additional documentation",
            "无法确认",
            "无法確認",
            "文件不足",
            "文件不充分",
            "材料不足",
            "添加文件",
            "新增文件",
            "补充材料",
            "請補充",
            "tidak dapat mengonfirmasi",
            "Dokumen tidak memadai",
            "Tambahkan dokumen",
            "coba lagi",
        ]

    async def scan_text_patterns(self, page: Page, patterns: Iterable[str]) -> UiCheckResult:
        for t in patterns:
            s = str(t or "").strip()
            if not s:
                continue
            try:
                loc = page.locator(f'text="{s}"').first
                if (await loc.count()) and (await loc.is_visible()):
                    return UiCheckResult(ok=False, hit=s, reason="pattern_hit")
            except Exception:
                if self._debug_enabled():
                    logger.debug("StatusInspector.scan_text_patterns error on pattern=%r", s)
                continue
        return UiCheckResult(ok=True)

    async def inspect_verify_page(
        self,
        *,
        context: BrowserContext,
        verification_id: str,
        patterns: Optional[list[str]] = None,
        dump_prefix: str = "sheerid_ui",
    ) -> UiCheckResult:
        page: Optional[Page] = None
        pats = patterns or self.default_patterns()
        try:
            page = await context.new_page()
            verify_url = config.VERIFY_URL_TEMPLATE.format(verification_id=verification_id)
            await page.goto(verify_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await self._wait_network_idle_soft(page, 15000)
            except Exception:
                pass

            res = await self.scan_text_patterns(page, pats)
            if not res.ok:
                try:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    await self._ui.dump_page(page, f"{dump_prefix}_{ts}")
                except Exception:
                    if self._debug_enabled():
                        logger.debug("StatusInspector dump_page failed prefix=%s", dump_prefix)
            return res
        except Exception as e:
            if self._debug_enabled():
                logger.debug("StatusInspector.inspect_verify_page failed err=%s", str(e)[:200])
            return UiCheckResult(ok=True, reason="inspect_failed_soft")
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass