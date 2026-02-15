from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SheerIDApiService:
    """
    只封装“能力”：对 SheerID 的 HTTP/API 调用。
    注意：此处不引入新的重试/超时/错误语义；只是把既有实现搬家并集中。
    """

    request_json: object  # async (method, url, body) -> (dict, status_code)
    sleep_between_requests: object  # async () -> None

    async def request(self, method: str, url: str, body: Optional[Dict] = None) -> Tuple[Dict, int]:
        # 仅搬家：等待/ensure 的顺序、异常语义保持与原 _sheerid_request() 一致
        await self.sleep_between_requests()
        return await self.request_json(method, url, body)

    async def precheck(self, *, base_url: str, verification_id: str) -> Tuple[str, Dict]:
        pre_data, _ = await self.request(
            "GET",
            f"{base_url}/rest/v2/verification/{verification_id}",
        )
        step = pre_data.get("currentStep", "collectStudentPersonalInfo")
        return step, pre_data