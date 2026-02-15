"""SheerID 学生验证主程序（Playwright Async API，严格不阻塞 event loop）。"""

from __future__ import annotations

import logging
import os
import traceback
import random
from typing import Dict, Optional, Tuple

from . import config
from .browser_manager import BrowserConfig, BrowserManager
from .form_filler import FillDelays, FormFiller
from .name_generator import NameGenerator, generate_birth_date
from .sheerid_client import SheerIDClient
from .sheerid_selectors import FORM_SELECTORS
from .sheerid_utils import compact_for_trace, mask, mask_vid, new_run_id, utc_now_iso
from .services.sheerid_api_service import SheerIDApiService
from .form_ready_dump import dump_form_ready
from .use_cases.verification_orchestrator import (
    VerificationDeps,
    VerificationOrchestrator,
    VerificationProfile,
)

logging.basicConfig(level=logging.DEBUG, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


async def _async_sleep_between_requests() -> None:
    """只允许 async 版本（不允许同步回退）。"""
    from .sheerid_utils import sleep_between_requests_async

    await sleep_between_requests_async()


async def _async_human_delay(lo: float = 0.2, hi: float = 0.6) -> None:
    """只允许 async 版本（不允许同步回退）。"""
    from .sheerid_utils import human_delay_async

    await human_delay_async(lo, hi)


def _safe_float_env(key: str, default: float = 0.0) -> float:
    try:
        raw = (os.getenv(key, "") or "").strip()
        if not raw:
            return float(default)
        return float(raw)
    except Exception:
        return float(default)


class SheerIDVerifier:
    def __init__(self, verification_id: str):
        self.verification_id = verification_id
        self.run_id = new_run_id()
        self.run_started_at = utc_now_iso()
        self._has_run = False

        self._browser_mgr: Optional[BrowserManager] = None
        self.context = None
        self.http_client = None
        self._api: Optional[SheerIDClient] = None
        self._api_svc: Optional[SheerIDApiService] = None
        self._form: Optional[FormFiller] = None

        env_fp = (os.getenv("ONE_SHEERID_DEVICE_FINGERPRINT", "") or "").strip().lower()
        self.device_fingerprint = env_fp if env_fp else "".join(random.choice("0123456789abcdef") for _ in range(32))

        force_en = (os.getenv("ONE_SHEERID_FORCE_EN_US", "1") or "1").strip().lower() in ("1", "true", "yes", "on")
        if force_en:
            self.accept_language = "en-US,en;q=0.9"
            self.locale = "en-US"
        else:
            al = (os.getenv("ONE_SHEERID_ACCEPT_LANGUAGE", "") or "").strip()
            self.accept_language = al or "en-US,en;q=0.9"
            env_locale = (os.getenv("ONE_SHEERID_LOCALE", "") or "").strip()
            self.locale = env_locale or (self.accept_language.split(",", 1)[0].strip() or "en-US")

        logger.info(
            "sheerid locale configured locale=%s accept_language=%s vid=%s",
            getattr(self, "locale", ""),
            getattr(self, "accept_language", ""),
            mask_vid(self.verification_id),
        )

    async def close(self) -> None:
        try:
            if self._browser_mgr is not None:
                await self._browser_mgr.close()
        except Exception:
            pass
        self._browser_mgr = None
        self.context = None
        self.http_client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def _wait_network_idle_soft(self, page, timeout_ms: int = 15000) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

    async def _ensure_browser(self) -> None:
        if self.context is not None and self.http_client is not None:
            return

        if self._browser_mgr is None:
            proxy_url = (os.getenv("ONE_HTTP_PROXY", "") or "").strip()
            cfg = BrowserConfig(
                accept_language=getattr(self, "accept_language", "en-US,en;q=0.9"),
                locale=getattr(self, "locale", "en-US"),
                proxy_url=proxy_url,
            )
            self._browser_mgr = BrowserManager(self.verification_id, cfg)

        await self._browser_mgr.ensure()
        self.context = self._browser_mgr.context
        self.http_client = self._browser_mgr.http_client

    async def _sheerid_request(self, method: str, url: str, body: Optional[Dict] = None) -> Tuple[Dict, int]:
        # 仅薄代理：实现已收编进 SheerIDApiService（搬家不改语义）
        await self._ensure_api_service()
        return await self._api_svc.request(method, url, body)

    async def _ensure_api_service(self) -> None:
        if self._api_svc is not None:
            return
        await self._ensure_browser()
        if self._api is None and self.http_client is not None:
            self._api = SheerIDClient(self.http_client, accept_language=getattr(self, "accept_language", ""))
        if self._api is None:
            raise Exception("API client 未初始化")
        self._api_svc = SheerIDApiService(
            request_json=self._api.request_json,
            sleep_between_requests=_async_sleep_between_requests,
        )

    async def _do_precheck(self) -> Tuple[str, Dict]:
        # 仅搬家：对外返回(step, pre_data) 结构与日志语义保持一致
        await self._ensure_api_service()
        step, pre_data = await self._api_svc.precheck(base_url=config.SHEERID_BASE_URL, verification_id=self.verification_id)
        # 关键：以 precheck 返回的 locale 为准，统一后续 Accept-Language/locale，避免会话/语言不一致导致提交不生效
        try:
            srv_locale = (pre_data.get("locale") or "").strip()
            if srv_locale and srv_locale != getattr(self, "locale", ""):
                old_locale = getattr(self, "locale", "")
                self.locale = srv_locale
                # accept-language 用 “服务端 locale + 英文兜底”
                self.accept_language = f"{srv_locale},{srv_locale.split('-')[0]};q=0.9,en-US;q=0.8,en;q=0.7"
                # 如果 API client 已初始化，更新其请求头语言（其 headers 是每次 request_json 现算的）
                if self._api is not None:
                    self._api.accept_language = self.accept_language
                logger.info(
                    "server locale override old=%s new=%s accept_language=%s vid=%s",
                    old_locale,
                    self.locale,
                    self.accept_language,
                    mask_vid(self.verification_id),
                )
        except Exception:
            pass
        logger.info("预检返回 currentStep=%s vid=%s", step, mask_vid(self.verification_id))
        logger.debug("预检详情 vid=%s errorIds=%s redirectUrl=%s", mask_vid(self.verification_id), pre_data.get("errorIds"), pre_data.get("redirectUrl"))
        return step, pre_data

    async def _ensure_form(self) -> None:
        if self._form is not None:
            return
        self._form = FormFiller(
            selectors=FORM_SELECTORS,
            delays=FillDelays(
                after_field_sec=_safe_float_env("ONE_DELAY_AFTER_FIELD_SEC", 0.0),
                after_org_sec=_safe_float_env("ONE_DELAY_AFTER_ORG_SEC", 0.0),
                after_submit_sec=_safe_float_env("ONE_DELAY_AFTER_SUBMIT_SEC", 0.0),
            ),
            human_delay_fn=None,
        )

    async def verify(
        self,
        first_name: str = None,
        last_name: str = None,
        email: str = None,
        birth_date: str = None,
        school_id: str = None,
        _in_thread: bool = False,
    ) -> Dict:
        try:
            if self._has_run:
                raise Exception("实例仅允许执行一次 verify()")
            self._has_run = True

            if not first_name or not last_name:
                gen = NameGenerator.generate()
                first_name, last_name = first_name or gen["first_name"], last_name or gen["last_name"]

            if not email:
                raise Exception("未提供邮箱")

            school_id = school_id or getattr(config, "DEFAULT_SCHOOL_ID", "2565")
            school = config.get_school(str(school_id)) if hasattr(config, "get_school") else config.SCHOOLS.get(str(school_id))
            if not school:
                raise Exception(f"未知学校 ID: {school_id}")

            birth_date = birth_date or generate_birth_date()

            logger.info(
                "办理验证 school=%s vid=%s email=%s",
                school.get("name"),
                mask_vid(self.verification_id),
                mask(email),
            )

            # 依赖装配：确保 _form 非空，但不触发额外 precheck（避免重复日志/重复请求）
            await self._ensure_form()
            deps = VerificationDeps(
                do_precheck=self._do_precheck,
                ensure_browser=self._ensure_browser,
                ensure_form=self._ensure_form,
                new_page=lambda: self.context.new_page(),  # context 由 ensure_browser 装配后再取，避免 NoneType
                wait_network_idle_soft=self._wait_network_idle_soft,
                locate_form_frame=self._form.locate_form_frame,  # type: ignore[union-attr]
                fill_text=self._form.fill_text,  # type: ignore[union-attr]
                fill_org_and_select_first=self._form.fill_org_and_select_first,  # type: ignore[union-attr]
                fill_birth_date_any=self._form.fill_birth_date_any,  # type: ignore[union-attr]
                check_all_visible_checkboxes=self._form.check_all_visible_checkboxes,  # type: ignore[union-attr]
                click_submit=self._form.click_submit,  # type: ignore[union-attr]
                human_delay=_async_human_delay,
                build_verify_url=lambda vid: config.VERIFY_URL_TEMPLATE.format(verification_id=vid),
                dump_form_ready=dump_form_ready,
            )
            orch = VerificationOrchestrator(deps)
            out = await orch.run(
                VerificationProfile(
                    first_name=str(first_name or ""),
                    last_name=str(last_name or ""),
                    email=str(email or ""),
                    birth_date=birth_date,
                    school_name=str(school.get("name") or ""),
                    verification_id=self.verification_id,
                )
            )

            # 保持原来的 message 文案（不改语义）
            if out.get("status") and out.get("success") is False and out.get("message") == "表单提交后进入 error":
                out["message"] = f"表单提交后进入 error，详情: {compact_for_trace(out.get('status') or {})}"
            return out
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "trace": traceback.format_exc()[:2000],
                "verification_id": self.verification_id,
            }