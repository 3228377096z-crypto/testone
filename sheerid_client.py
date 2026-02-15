from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from playwright.async_api import APIRequestContext

from . import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientSettings:
    timeout_ms: int = 20000
    max_retries: int = 1
    backoff_cap_sec: float = 30.0

    @classmethod
    def from_env(cls) -> "ClientSettings":
        timeout_ms = int((os.getenv("ONE_SHEERID_REQUEST_TIMEOUT_MS", "20000") or "20000"))
        max_retries = max(0, int((os.getenv("ONE_SHEERID_MAX_RETRIES", "1") or "1")))
        backoff_cap_sec = float((os.getenv("ONE_SHEERID_BACKOFF_CAP_SEC", "30") or "30"))
        return cls(timeout_ms=timeout_ms, max_retries=max_retries, backoff_cap_sec=backoff_cap_sec)


class SheerIDClient:
    """只负责 HTTP/API：重试/退避/上传。（Async API）"""

    def __init__(self, request: APIRequestContext, *, accept_language: str) -> None:
        self.request = request
        self.accept_language = accept_language
        self.settings = ClientSettings.from_env()

    def _compute_backoff_sec(self, attempt: int) -> float:
        base = 2 ** max(0, attempt)
        jitter = random.uniform(0, 1)  # noqa: S311
        return min(float(self.settings.backoff_cap_sec), float(base) + float(jitter))

    async def request_json(self, method: str, url: str, body: Optional[Dict] = None) -> Tuple[Dict[str, Any], int]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": config.SHEERID_BASE_URL,
            "Referer": config.SHEERID_BASE_URL + "/",
        }
        if self.accept_language:
            headers["Accept-Language"] = self.accept_language

        last_data: Dict[str, Any] = {}
        last_status = 0
        last_exc: Optional[Exception] = None

        # 延迟节奏由调用层/Verifier 控制，这里不做阻塞 sleep
        for attempt in range(self.settings.max_retries + 1):
            try:
                resp = await self.request.fetch(
                    url,
                    method=method,
                    headers=headers,
                    data=json.dumps(body) if body else None,
                    timeout=self.settings.timeout_ms,
                )
                last_status = resp.status
                try:
                    last_data = await resp.json()
                except Exception:
                    last_data = {"raw": ((await resp.text()) or "")[:500]}

                if attempt < self.settings.max_retries and last_status in {408, 429, 500, 502, 503, 504}:
                    await asyncio.sleep(self._compute_backoff_sec(attempt))
                    continue

                return last_data, last_status
            except Exception as e:
                last_exc = e
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(self._compute_backoff_sec(attempt))
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        return last_data, last_status

    async def upload_to_s3(self, upload_url: str, file_data: bytes, content_type: str) -> bool:
        max_attempts = max(1, int((os.getenv("ONE_SHEERID_S3_UPLOAD_MAX_ATTEMPTS", "2") or "2")))
        timeout_ms = int((os.getenv("ONE_SHEERID_S3_UPLOAD_TIMEOUT_MS", "60000") or "60000"))

        for attempt in range(max_attempts):
            try:
                resp = await self.request.fetch(
                    upload_url,
                    method="PUT",
                    headers={"Content-Type": content_type},
                    data=file_data,
                    timeout=timeout_ms,
                )
                if 200 <= resp.status < 300:
                    return True

                try:
                    body = ((await resp.text()) or "")[:300]
                except Exception:
                    body = ""
                logger.warning("S3 upload failed attempt=%s status=%s body=%s", attempt + 1, resp.status, body)
            except Exception as e:
                logger.warning("S3 upload exception attempt=%s err=%s", attempt + 1, str(e)[:200])

            await asyncio.sleep(min(10.0, 0.5 * (2 ** attempt)))

        return False