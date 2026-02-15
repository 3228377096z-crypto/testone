from __future__ import annotations

import asyncio
import os
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, Protocol, Tuple

from ..ui_diagnostics import UiDiagnostics


class PrecheckFn(Protocol):
    async def __call__(self) -> Tuple[str, Dict]: ...


class EnsureBrowserFn(Protocol):
    async def __call__(self) -> None: ...


class EnsureFormFn(Protocol):
    async def __call__(self) -> None: ...


class NewPageFn(Protocol):
    async def __call__(self):
        ...


class WaitNetworkIdleSoftFn(Protocol):
    async def __call__(self, page, timeout_ms: int = 15000) -> None: ...


class LocateFormFrameFn(Protocol):
    async def __call__(self, page):
        ...


class FormFillTextFn(Protocol):
    async def __call__(self, frame, key: str, value: str) -> None: ...


class FormFillOrgFn(Protocol):
    async def __call__(self, frame, org_name: str) -> None: ...


class FormFillBirthdateFn(Protocol):
    async def __call__(self, frame, birth_date: str) -> bool: ...


class FormCheckCheckboxesFn(Protocol):
    async def __call__(self, frame) -> int: ...


class FormClickSubmitFn(Protocol):
    async def __call__(self, frame) -> None: ...


class AsyncDelayFn(Protocol):
    async def __call__(self, lo: float = 0.2, hi: float = 0.6) -> None: ...


@dataclass(frozen=True)
class VerificationProfile:
    first_name: str
    last_name: str
    email: str
    birth_date: str
    school_name: str
    verification_id: str


@dataclass(frozen=True)
class VerificationDeps:
    do_precheck: PrecheckFn
    ensure_browser: EnsureBrowserFn
    ensure_form: EnsureFormFn
    new_page: NewPageFn
    wait_network_idle_soft: WaitNetworkIdleSoftFn
    locate_form_frame: LocateFormFrameFn
    fill_text: FormFillTextFn
    fill_org_and_select_first: FormFillOrgFn
    fill_birth_date_any: FormFillBirthdateFn
    check_all_visible_checkboxes: FormCheckCheckboxesFn
    click_submit: FormClickSubmitFn
    human_delay: AsyncDelayFn
    build_verify_url: Callable[[str], str]

    dump_form_ready: Callable[[Any], Any] | None = None


class VerificationOrchestrator:
    """
    只做编排（顺序/汇总/吞错保持一致），不做 UI 细节判断、不做材料生成/上传。
    依赖通过 deps 注入，便于替换/测试；逻辑保持与原 verify() 一致（搬家不改语义）。
    """

    def __init__(self, deps: VerificationDeps):
        self._deps = deps

    async def run(self, profile: VerificationProfile) -> Dict:
        ui_diag = UiDiagnostics()
        page = None
        try:
            current_step, pre_status = await self._deps.do_precheck()

            # 支持本地“只跑 UI 流程”调试：即便 precheck=error 也继续打开验证页并 dump 表单
            force_ui = (os.getenv("ONE_SHEERID_FORCE_UI", "") or "").strip().lower() in ("1", "true", "yes", "on")
            if current_step != "collectStudentPersonalInfo" and not force_ui:
                return {
                    "success": current_step != "error",
                    "message": f"预检步骤为 {current_step}",
                    "verification_id": profile.verification_id,
                    "status": {"currentStep": current_step, "precheck": pre_status},
                }

            await self._deps.ensure_browser()
            await self._deps.ensure_form()

            page = await self._deps.new_page()
            verify_url = self._deps.build_verify_url(profile.verification_id)

            try:
                # 不使用 commit：某些网络/代理环境下 commit 事件可能等不到
                await page.goto(verify_url, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(800)
            except Exception as e:
                if not force_ui:
                    raise
                try:
                    url_now = page.url
                except Exception:
                    url_now = ""
                try:
                    title_now = (await page.title()) or ""
                except Exception:
                    title_now = ""
                try:
                    html_head = ((await page.content()) or "")[:2000]
                except Exception:
                    html_head = ""
                try:
                    await ui_diag.dump_page(page, prefix=f"goto_failed_{profile.verification_id}")
                except Exception:
                    pass
                try:
                    await page.close()
                except Exception:
                    pass
                return {
                    "success": False,
                    "message": f"force_ui: Page.goto 超时/失败：{e}",
                    "verification_id": profile.verification_id,
                    "status": {
                        "precheckCurrentStep": current_step,
                        "precheck": pre_status,
                        "url": url_now,
                        "title": title_now,
                        "htmlHead": html_head,
                    },
                }

            await self._deps.wait_network_idle_soft(page, 15000)

            try:
                form_frame = await self._deps.locate_form_frame(page)
            except Exception as e:
                await ui_diag.dump_page(page, prefix=f"locate_form_frame_failed_{profile.verification_id}")
                if not force_ui:
                    raise
                try:
                    url_now = page.url
                except Exception:
                    url_now = ""
                try:
                    title_now = (await page.title()) or ""
                except Exception:
                    title_now = ""
                try:
                    html_head = ((await page.content()) or "")[:2000]
                except Exception:
                    html_head = ""
                try:
                    iframe_count = await page.locator("iframe").count()
                except Exception:
                    iframe_count = -1
                try:
                    await page.close()
                except Exception:
                    pass
                return {
                    "success": False,
                    "message": f"force_ui: locate_form_frame 失败：{e}",
                    "verification_id": profile.verification_id,
                    "status": {
                        "precheckCurrentStep": current_step,
                        "precheck": pre_status,
                        "url": url_now,
                        "title": title_now,
                        "iframeCount": iframe_count,
                        "htmlHead": html_head,
                    },
                }

            await self._deps.human_delay(0.2, 0.6)
            await self._deps.fill_text(form_frame, "first_name", str(profile.first_name or ""))
            await self._deps.human_delay(0.2, 0.6)
            await self._deps.fill_text(form_frame, "last_name", str(profile.last_name or ""))
            await self._deps.human_delay(0.2, 0.6)
            await self._deps.fill_text(form_frame, "email", str(profile.email or ""))

            try:
                await self._deps.human_delay(0.2, 0.6)
                await self._deps.fill_org_and_select_first(form_frame, str(profile.school_name or ""))
            except Exception:
                await ui_diag.dump_page(page, prefix=f"fill_org_exception_{profile.verification_id}")
                pass

            try:
                await self._deps.human_delay(0.2, 0.6)
                await self._deps.fill_birth_date_any(form_frame, profile.birth_date)
            except Exception:
                pass

            try:
                await self._deps.human_delay(0.2, 0.6)
                await self._deps.check_all_visible_checkboxes(form_frame)
            except Exception:
                pass

            try:
                if getattr(self._deps, "dump_form_ready", None):
                    data = await self._deps.dump_form_ready(form_frame)  # type: ignore[misc]
                else:
                    data = None
            except Exception:
                data = None

            # DRY_RUN=1：仅填表 + 打印诊断，不做真实提交/不轮询 step
            if (os.getenv("ONE_SHEERID_DRY_RUN", "") or "").strip().lower() in ("1", "true", "yes", "on"):
                # DRY_RUN 也截一张，方便肉眼确认 UI 到底停在哪里
                await ui_diag.dump_page(page, prefix=f"dry_run_{profile.verification_id}")
                try:
                    await page.close()
                except Exception:
                    pass
                return {
                    "success": True,
                    "message": "DRY_RUN: 仅填表未提交（用于校验前端绑定）",
                    "verification_id": profile.verification_id,
                    "form_values": data,
                    "status": {
                        "currentStep": "collectStudentPersonalInfo",
                        "precheckCurrentStep": current_step,
                        "precheck": pre_status,
                    },
                }

            # 提交前硬门槛：未达标直接退出，避免“假提交”污染会话
            try:
                if isinstance(data, dict):
                    school_id = data.get("schoolId")
                    school_text = data.get("schoolText")
                    consent_checked = data.get("consentChecked")
                    invalid_count = data.get("ariaInvalidCount")
                    # 额外兜底：若 dump 读不到 consent，但页面上确实有 checkbox，就尝试直接再勾一遍并重新 dump
                    if consent_checked is not True:
                        try:
                            await self._deps.human_delay(0.2, 0.6)
                            await self._deps.check_all_visible_checkboxes(form_frame)
                            if getattr(self._deps, "dump_form_ready", None):
                                data = await self._deps.dump_form_ready(form_frame)  # type: ignore[misc]
                                school_id = data.get("schoolId")
                                school_text = data.get("schoolText")
                                consent_checked = data.get("consentChecked")
                                invalid_count = data.get("ariaInvalidCount")
                        except Exception:
                            pass
                    school_id_s = str(school_id or "").strip()
                    school_text_s = str(school_text or "").strip()
                    school_ok = bool(school_id_s) or bool(school_text_s)

                    consent_val = consent_checked
                    consent_ok = (consent_val is True) or (str(consent_val).lower() == "true") or (consent_val == 1)

                    try:
                        aria_invalid = int(invalid_count or 0)
                    except Exception:
                        aria_invalid = 0
                    aria_ok = aria_invalid == 0

                    if not (school_ok and consent_ok and aria_ok):
                        await ui_diag.dump_page(page, prefix=f"pre_submit_validation_failed_{profile.verification_id}")
                        return {
                            "success": False,
                            "message": (
                                "提交前校验失败："
                                f"school_ok={school_ok}, consent_ok={consent_ok}, aria_ok={aria_ok}, "
                                f"schoolId={data.get('schoolId')}, schoolText={data.get('schoolText')}, "
                                f"consentChecked={data.get('consentChecked')}, ariaInvalidCount={data.get('ariaInvalidCount')}"
                            ),
                            "verification_id": profile.verification_id,
                            "form_values": data,
                            "status": {"currentStep": "collectStudentPersonalInfo"},
                        }
            except Exception:
                pass

            await self._deps.human_delay(0.2, 0.6)
            await self._deps.click_submit(form_frame)

            await self._deps.wait_network_idle_soft(page, 15000)
            await ui_diag.dump_page(page, prefix=f"after_submit_{profile.verification_id}")

            # 诊断：提交后是否发生跳转
            try:
                url_after = ""
                try:
                    url_after = page.url
                except Exception:
                    pass
                title_after = ""
                try:
                    title_after = (await page.title()) or ""
                except Exception:
                    pass
                if url_after or title_after:
                    print(
                        f"[diagnostics] after_submit vid={profile.verification_id} "
                        f"url={url_after!r} title={title_after!r}"
                    )
            except Exception:
                pass

            # 有些表单提交后后端状态会异步推进：这里做短暂轮询
            last_step = None
            last_status: Dict = {}
            for _ in range(6):  # ~12s
                current_step2, status2 = await self._deps.do_precheck()
                last_step, last_status = current_step2, status2
                if current_step2 != "collectStudentPersonalInfo":
                    break
                try:
                    await self._deps.wait_network_idle_soft(page, 6000)
                except Exception:
                    pass
                await asyncio.sleep(2.0)
            current_step2, status2 = last_step, last_status

            if current_step2 == "error":
                await ui_diag.dump_page(page, prefix=f"after_submit_error_{profile.verification_id}")
                return {
                    "success": False,
                    "message": "表单提交后进入 error",
                    "verification_id": profile.verification_id,
                    "status": status2,
                }

            if current_step2 == "collectStudentPersonalInfo":
                await ui_diag.dump_page(page, prefix=f"step_not_advanced_{profile.verification_id}")
                return {
                    "success": False,
                    "message": "表单提交未生效（步骤未推进，仍为 collectStudentPersonalInfo）",
                    "verification_id": profile.verification_id,
                    "status": status2,
                }

            return {
                "success": True,
                "message": f"表单已提交，当前步骤: {current_step2}",
                "verification_id": profile.verification_id,
                "status": status2,
            }
        except Exception as e:
            try:
                await ui_diag.dump_page(page, prefix=f"unhandled_exception_{profile.verification_id}")
            except Exception:
                pass
            return {
                "success": False,
                "message": str(e),
                "trace": traceback.format_exc()[:2000],
                "verification_id": profile.verification_id,
            }
