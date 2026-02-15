from __future__ import annotations

import logging
import asyncio
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from playwright.async_api import Frame, Locator, Page

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FillDelays:
    after_field_sec: float = 0.0
    after_org_sec: float = 0.0
    after_submit_sec: float = 0.0


class FormFiller:
    """
    表单填充器 - V28 双重修复版 (Dual Fix)
    修复日志：
    1. [Month Fix] 针对月份 "03" 选不到的问题，增加数字转英文映射 (03 -> March)，并强制键盘回车确认。
    2. [Click Fix] 学校选择不再使用物理 click()，改用 dispatch_event('click')，防止 30s 超时。
    3. [Smart Wait] 保持 V27 的智能等待逻辑，过滤干扰信息。
    """

    def __init__(
            self,
            *,
            selectors: Dict[str, Any],
            delays: Optional[FillDelays] = None,
            human_delay_fn: Optional[Any] = None,
    ) -> None:
        self.selectors = selectors
        self.delays = delays or FillDelays()
        self._human_delay = human_delay_fn

    async def _sleep(self, sec: float) -> None:
        if sec <= 0: return
        await asyncio.sleep(sec)

    async def _human_delay_if_needed(self) -> None:
        fn = getattr(self, "_human_delay", None)
        if not fn: return
        try:
            r = fn()
            if asyncio.iscoroutine(r): await r
        except Exception:
            pass

    async def _wait_any_selector(self, frame: Frame, selectors: Union[str, list[str], None], *,
                                 timeout_ms: int = 8000) -> bool:
        if not selectors: return False
        sels = selectors if isinstance(selectors, list) else [str(selectors)]
        for sel in [s for s in sels if isinstance(s, str) and s.strip()]:
            try:
                await frame.wait_for_selector(sel, state="attached", timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    async def _find_frame_with_selector(self, page: Page, selector: str) -> Frame:
        try:
            if await page.main_frame.locator(selector).first.count() > 0:
                return page.main_frame
        except Exception:
            pass
        for frame in page.frames:
            try:
                if await frame.locator(selector).first.count() > 0:
                    return frame
            except Exception:
                continue
        return page.main_frame

    async def locate_form_frame(self, page: Page) -> Frame:
        candidates = ["#sid-college-name", "#sid-email", "#sid-first-name"]
        for sel in candidates:
            frame = await self._find_frame_with_selector(page, sel)
            if frame != page.main_frame: return frame
        return page.main_frame

    async def _find_org_combobox_input(self, frame: Frame) -> Optional[Locator]:
        """
        找到“学校”对应的 combobox input。
        关键：页面上还会有语言选择 combobox（changeLanguageSelector-input），必须排除。
        """
        # 1) 优先：在 .sid-college-name-id 区块里找 role=combobox
        try:
            loc = frame.locator(".sid-college-name-id input[role='combobox']").first
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            pass

        # 2) 根据 label[for] 反查（截图里 label for="sid-college-name"）
        try:
            loc = frame.locator("label[for='sid-college-name'] ~ div input[role='combobox']").first
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            pass

        # 3) 回退：找所有 combobox，排除语言选择器
        try:
            loc = frame.locator(
                "input[role='combobox'][aria-controls]:not(#changeLanguageSelector-input)"
            ).first
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            pass

        # 4) 兜底：直接用已知 id（避免被 changeLanguageSelector-input 抢先匹配）
        try:
            loc = frame.locator("#sid-college-name").first
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            pass

        # 5) 最后兜底：有些页面 school 不是 combobox，而是普通输入框（placeholder=School）
        try:
            loc = frame.locator("input[placeholder='School'], input[aria-label='School']").first
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            pass
        return None

    async def locate_form_frame_for_key(self, page: Page, key: str, *, timeout_ms: int = 5000) -> Frame:
        return await self.locate_form_frame(page)

    def _locator(self, frame: Frame, key: str) -> Locator:
        raw: Union[str, list[str], None] = self.selectors.get(key)
        candidates = raw if isinstance(raw, list) else [str(raw)]
        for sel in candidates:
            return frame.locator(sel).first
        raise ValueError(f"selector invalid for {key}")

    async def fill_text(self, frame: Frame, key: str, value: str) -> None:
        try:
            loc = self._locator(frame, key)
            await loc.click(force=True, timeout=2000)
            await loc.fill("")
            await loc.type(value, delay=20)
            await self._human_delay_if_needed()
            await self._sleep(self.delays.after_field_sec)
        except Exception as e:
            logger.debug("fill_text failed key=%s err=%s", key, str(e)[:200])

    async def _check_org_id_filled(self, frame: Frame) -> Optional[str]:
        """检查 ID 是否填充"""
        candidates = [
            'input[name="organizationId"]',
            'input[name="schoolId"]',
            'input[name="organization.id"]',
            'input[name*="organization" i][type="hidden"]',
            'input[name*="school" i][type="hidden"]',
            'input[id*="college" i][type="hidden"]',
            'input[id*="org" i][type="hidden"]',
        ]
        for css in candidates:
            try:
                loc = frame.locator(css).first
                if await loc.count() > 0:
                    val = await loc.get_attribute("value")
                    if val and val.strip() and len(val) > 5: return val.strip()
            except Exception:
                continue

        try:
            # 兼容：历史版本用 sid-college-name；你截图里是 sid-college-name
            loc = frame.locator("#sid-college-name, #sid-college-name").first
            if await loc.count() > 0:
                val = await loc.get_attribute("data-selected-id")
                if val: return val
        except:
            pass

        return None

    async def _reset_ui_state(self, frame: Frame) -> None:
        try:
            await frame.page.keyboard.press("Escape")
            await self._sleep(0.2)
            await frame.locator("body").click(force=True, position={"x": 1, "y": 1})
            await self._sleep(0.3)
        except Exception:
            pass

    async def _fresh_org_input(self, frame: Frame) -> Optional[Locator]:
        # 统一走更精准的定位（避免命中语言下拉）
        return await self._find_org_combobox_input(frame)

    async def _is_org_already_selected(self, frame: Frame) -> bool:
        """优先以“selected 卡片”作为成功信号，其次看 hidden id。"""
        try:
            selected_name = await frame.locator(
                ".sid-college-name-id .sid-selected-org__name"
            ).first.text_content(timeout=300)
            if selected_name and selected_name.strip():
                return True
        except Exception:
            pass

        try:
            hid = await frame.locator(
                "input[name='organizationId'], input[name='schoolId']"
            ).first.input_value(timeout=300)
            if hid and hid.strip():
                return True
        except Exception:
            pass

        return False

    async def _extract_selected_org_from_ui(self, frame: Frame) -> Optional[dict]:
        """
        从 UI 上尽力提取当前“已选学校”的信息。
        说明：
        - 不同 SheerID 版本可能把选中结果放在 input 的 data-* 上，或放在选中 option 的 data-value 等属性上。
        - 这里用于 debug 和兜底判断，不作为唯一真相。
        """
        try:
            loc = await self._fresh_org_input(frame)
            if not loc:
                return None
            return await loc.evaluate(
                """(el) => {
  const v = (el.value || '').trim();
  const ds = el.dataset || {};
  const out = {
    value: v || null,
    dataSelectedId: ds.selectedId || ds.selectedID || null,
    ariaActivedescendant: el.getAttribute('aria-activedescendant'),
    ariaExpanded: el.getAttribute('aria-expanded'),
  };
  // 尝试从 activedescendant 找到 option
  const ad = out.ariaActivedescendant;
  if (ad) {
    const opt = document.getElementById(ad);
    if (opt) {
      out.activeOptionText = (opt.textContent || '').trim() || null;
      out.activeOptionId = opt.id || null;
      out.activeOptionValue = opt.getAttribute('data-value') || opt.getAttribute('value') || null;
    }
  }
  return out;
}"""
            )
        except Exception:
            return None

    async def _check_org_selected_strict(self, frame: Frame) -> tuple[bool, str, Optional[str]]:
        """
        学校是否“真选中”的严格判断：
        1) 优先看 selected 卡片（.sid-selected-org__name 有文本）
        2) 其次看 hidden id（organizationId / schoolId）
        返回: (ok, reason, school_text)
        """
        try:
            name_loc = frame.locator(".sid-college-name-id .sid-selected-org__name").first
            if await name_loc.count() > 0:
                txt = (await name_loc.text_content() or "").strip()
                if txt:
                    return True, "selected_org_container", txt
        except Exception:
            pass

        try:
            hid_loc = frame.locator("input[name='organizationId'], input[name='schoolId']").first
            if await hid_loc.count() > 0:
                hid = (await hid_loc.input_value() or "").strip()
                if hid:
                    return True, "hidden_org_id", None
        except Exception:
            pass

        return False, "not_selected", None

    async def _debug_dump_org_dom(self, frame: Frame) -> Optional[dict]:
        """输出学校控件相关 DOM 关键信息（用于定位 selector 是否对）"""
        try:
            return await frame.evaluate(
                """() => {
  const root = document.querySelector('.sid-college-name-id') || document;

  const selectedWrap = root.querySelector('.sid-selected-org__container');
  const selectedName = root.querySelector('.sid-selected-org__name');
  const selectedLocation = root.querySelector('.sid-selected-org__location');

  const input =
    root.querySelector('#sid-college-name') ||
    root.querySelector("input[id*='college'][role='combobox']") ||
    root.querySelector("input[role='combobox'][aria-controls*='college']") ||
    root.querySelector("input[role='combobox'][aria-controls]") ||
    null;

  const menuId = input ? input.getAttribute('aria-controls') : null;
  const menu = menuId ? document.getElementById(menuId) : null;

  const hiddenOrgId =
    document.querySelector("input[name='organizationId']")?.value ||
    document.querySelector("input[name='schoolId']")?.value ||
    null;

  return {
    // selected 视图
    selectedWrapFound: !!selectedWrap,
    selectedName: selectedName ? (selectedName.textContent || '').trim() : null,
    selectedLocation: selectedLocation ? (selectedLocation.textContent || '').trim() : null,

    // input 视图
    inputFound: !!input,
    inputId: input ? input.id : null,
    inputValue: input ? (input.value || '').trim() : null,
    ariaControls: menuId,
    ariaExpanded: input ? input.getAttribute('aria-expanded') : null,
    menuFound: !!menu,
    roleOptionCount: menu ? menu.querySelectorAll("[role='option']").length : 0,

    // hidden / data-*
    hiddenOrgId: hiddenOrgId,
    dataSelectedId: input ? (input.dataset?.selectedId || input.getAttribute('data-selected-id') || null) : null,
  };
}"""
            )
        except Exception:
            return None

    async def _force_select_org_by_exact_text(self, frame: Frame, full_text: str) -> bool:
        """兜底：当页面已进入 selected_org_container 形态但没有任何 hidden id 时，
        尝试用“先清空再输入完整学校名+回车/聚焦离焦”的方式触发内部绑定写入。
        """
        try:
            loc = await self._fresh_org_input(frame)
            if not loc:
                return False
            await self._safe_focus_input(loc)
            try:
                await loc.fill("")
            except Exception:
                pass
            try:
                await frame.page.keyboard.press("Control+A")
                await frame.page.keyboard.press("Backspace")
            except Exception:
                pass
            await self._sleep(0.15)
            try:
                await loc.type(str(full_text or ""), delay=40)
                await self._sleep(0.25)
                await loc.press("Enter")
            except Exception:
                pass
            await self._sleep(0.35)
            try:
                await loc.dispatch_event("input")
                await loc.dispatch_event("change")
                await loc.dispatch_event("blur")
            except Exception:
                pass
            await self._sleep(0.35)
            return bool(await self._check_org_id_filled(frame))
        except Exception:
            return False

    async def _try_select_org_option_js(self, frame: Frame, option_selector: str) -> bool:
        """
        用 frame.evaluate 直接在页面上下文触发点击（不通过 Locator.evaluate，避免 30s 等待）。
        """
        try:
            return await frame.evaluate(
                """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  try { el.scrollIntoView({block: 'center'}); } catch (e) {}
  for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {
    try { el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})); } catch (e) {}
  }
  return true;
}""",
                option_selector,
            )
        except Exception:
            return False

    async def _safe_focus_input(self, loc: Locator, *, timeout_ms: int = 8000) -> bool:
        try:
            await loc.click(force=True, timeout=timeout_ms)
            return True
        except:
            return False

    async def fill_org_and_select_first(self, frame: Frame, org_name: str) -> None:
        """学校填写（V28 - 修复超时与月份）"""
        # 如果页面已经有“已选学校”的展示卡片，直接视为已选中（避免再去找 input#sid-college-name）
        if await self._is_org_already_selected(frame):
            logger.info("检测到页面已存在已选学校卡片，跳过学校下拉选择。")
            return
        try:
            await frame.wait_for_selector("#sid-college-name", state="attached", timeout=8000)
        except:
            await self._wait_any_selector(frame, self.selectors.get("organization"), timeout_ms=5000)

        loc = await self._fresh_org_input(frame)
        if not loc:
            logger.error("❌ 无法找到学校输入框")
            return

        search_terms: list[str] = []
        raw_name = (org_name or "").strip()
        if "-" in raw_name:
            parts = [p.strip() for p in raw_name.split("-") if p.strip()]
            if len(parts) > 1: search_terms.append(parts[-1])

        clean_name = raw_name.replace("-", " ").strip()
        if clean_name and clean_name not in search_terms: search_terms.append(clean_name)
        if raw_name and raw_name not in search_terms: search_terms.append(raw_name)

        success = False

        for term in search_terms:
            if success: break
            if len(term) < 2: continue

            logger.info(f"正在尝试学校关键词: {term}")

            try:
                loc = await self._fresh_org_input(frame)
                if not loc: continue

                # A. 聚焦与清空
                await self._safe_focus_input(loc)
                await loc.fill("")
                await frame.page.keyboard.press("Control+A")
                await frame.page.keyboard.press("Backspace")
                await self._sleep(0.1)

                logger.info(f"输入: {term}")
                await loc.press_sequentially(term, delay=80)

                # B. 智能等待
                menu_selector = "#sid-college-name-menu"
                # 兼容：有的版本菜单 id 会挂在 input 的 aria-controls 上（你截图是 sid-org-list-menu）
                try:
                    menu_id = await loc.get_attribute("aria-controls")
                except Exception:
                    menu_id = None
                if menu_id and menu_id.strip():
                    menu_selector = f"#{menu_id.strip()}"

                valid_option_sel = f"{menu_selector} [role='option'], {menu_selector} div[class*='option']"

                logger.info("等待真实选项...")
                try:
                    await frame.wait_for_selector(valid_option_sel, state="visible", timeout=6000)
                    logger.info("✅ 真实选项已检测到")
                except Exception:
                    logger.warning(f"⚠️ 未检测到有效选项")
                    continue

                # C. 选中策略：必须让前端真正写入 organizationId/schoolId（仅出现“已选学校卡片”不一定够）
                options = frame.locator(valid_option_sel)
                count = await options.count()

                if count > 0:
                    logger.info("找到选项，尝试选中并等待 schoolId/organizationId 写入")

                    # 多轮尝试：键盘 + JS 点击第一项；每轮后检查 hidden id
                    for _ in range(4):
                        try:
                            await frame.page.keyboard.press("ArrowDown")
                            await self._sleep(0.15)
                            await frame.page.keyboard.press("Enter")
                        except Exception:
                            pass

                        await self._sleep(0.25)
                        if await self._check_org_id_filled(frame):
                            break

                        try:
                            # 不等待 locator 稳定性，直接在页面上下文点第一项
                            js_clicked = await self._try_select_org_option_js(
                                frame, f"{menu_selector} [role='option']"
                            )
                            if not js_clicked:
                                await self._try_select_org_option_js(
                                    frame, f"{menu_selector} div[class*='option']"
                                )
                        except Exception:
                            pass

                        await self._sleep(0.35)
                        if await self._check_org_id_filled(frame):
                            break

                    # 收尾：离焦触发 change/blur 绑定
                    try:
                        await frame.page.keyboard.press("Tab")
                    except Exception:
                        pass

                # 给前端一点时间把 hidden id 写进去（很多模板是异步写入）
                for _ in range(8):
                    if await self._check_org_id_filled(frame):
                        break
                    await self._sleep(0.25)

                ok, reason, school_text = await self._check_org_selected_strict(frame)
                if ok:
                    success = True
                    logger.info(f"✅ 学校已选中: reason={reason} school={school_text}")
                    # 但有些模板不会在 DOM 放任何 hidden id（dump 里 hiddenCandidates=[]），这里强制再触发一次绑定
                    # 目标：尽最大努力让 organizationId/schoolId 写入，以便后续 submit 校验通过
                    if not await self._check_org_id_filled(frame):
                        try:
                            forced = await self._force_select_org_by_exact_text(frame, raw_name or term)
                            if forced:
                                logger.info("✅ 强制触发绑定后检测到 schoolId/organizationId 已写入")
                        except Exception:
                            pass
                    break

            except Exception as e:
                logger.warning(f"本轮尝试异常: {e}")
                await frame.page.keyboard.press("Escape")

        ok, reason, school_text = await self._check_org_selected_strict(frame)
        if ok:
            logger.info(f"学校流程结束: reason={reason} school={school_text}")
        else:
            ui_state = await self._extract_selected_org_from_ui(frame)
            logger.error(f"学校UI状态(用于排查): {ui_state}")
            dom_state = await self._debug_dump_org_dom(frame)
            logger.error(f"学校DOM状态(用于排查): {dom_state}")
            logger.error("❌ 学校未选中，尝试暴力注入...")
            if raw_name:
                try:
                    loc = await self._fresh_org_input(frame)
                    if loc:
                        await loc.evaluate(f"el => el.value = '{raw_name}'")
                        await loc.dispatch_event("input")
                        await loc.dispatch_event("change")
                        await loc.dispatch_event("blur")
                except:
                    pass

        await self._sleep(self.delays.after_org_sec)

    # --- 生日填写逻辑 (V28 修复版) ---
    async def fill_birth_date_any(self, frame: Frame, birth_date: str) -> bool:
        target_frame = await self._find_frame_with_selector(frame.page, "#sid-birthdate__month")
        return await self.fill_birth_date_combobox(target_frame, birth_date)

    async def fill_birth_date_combobox(self, frame: Frame, birth_date: str) -> bool:
        if not birth_date: return False
        try:
            m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", str(birth_date).strip())
            if not m: return False
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        except Exception:
            return False

        ids = {"day": "#sid-birthdate-day", "month": "#sid-birthdate__month", "year": "#sid-birthdate-year"}

        # 1. 尝试 JS 注入基础值
        js_script = f"""() => {{
            const setNativeValue = (el, value) => {{
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                if (setter) setter.call(el, value); else el.value = value;
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
            }};
            const d = document.querySelector('{ids["day"]}');
            if (d) setNativeValue(d, '{d}');
            const y = document.querySelector('{ids["year"]}');
            if (y) setNativeValue(y, '{y}');
        }}"""
        try:
            await frame.evaluate(js_script)
        except:
            pass

        async def _check_and_fill(key: str, val_str: str, is_month: bool = False) -> None:
            loc = frame.locator(ids[key]).first
            if not await loc.count() or not await loc.is_visible(): return

            # --- V28 月份特殊处理 ---
            if is_month:
                # 英文月份映射
                months = ["January", "February", "March", "April", "May", "June",
                          "July", "August", "September", "October", "November", "December"]
                try:
                    month_idx = int(val_str) - 1
                    if 0 <= month_idx < 12:
                        month_name = months[month_idx]
                        logger.info(f"正在输入月份: {val_str} -> {month_name}")

                        await loc.click(force=True)
                        await loc.fill("")
                        # 先尝试输入英文月份
                        await loc.type(month_name, delay=50)
                        await self._sleep(0.5)
                        await loc.press("Enter")
                        return
                except:
                    pass
            # -----------------------

            if val_str in await loc.input_value(): return

            try:
                await loc.click(force=True)
                await loc.fill(val_str)
                await loc.press("Tab")
            except:
                pass

        await _check_and_fill("month", f"{mo:02d}", is_month=True)  # 传入 True 开启特殊处理
        await _check_and_fill("day", str(d))
        await _check_and_fill("year", str(y))

        await self._human_delay_if_needed()
        await self._sleep(1.0)
        return True

    # 兼容占位
    async def fill_birth_date_simple(self, f, d):
        return False

    async def fill_birth_date_selects(self, f, d):
        return False

    async def _clear_birthdate_selects_if_present(self, f):
        pass

    async def check_all_visible_checkboxes(self, frame: Frame) -> int:
        checked = 0
        candidates = [
            'input[type="checkbox"][name*="consent" i]',
            'input[type="checkbox"][name*="agree" i]',
            'input[type="checkbox"][aria-label*="consent" i]',
            'input[type="checkbox"][aria-label*="agree" i]',
            'input[type="checkbox"][aria-label*="terms" i]',
            'input[type="checkbox"][name*="terms" i]',
            'input[type="checkbox"][name*="policy" i]',
        ]
        for sel in candidates:
            try:
                box = frame.locator(sel).first
                if await box.count() and await box.is_visible() and not await box.is_checked():
                    await box.click(force=True, timeout=800)
                    checked += 1
            except Exception:
                continue
        if checked == 0:
            try:
                # 避免误点“订阅/营销”之类：优先找附近有 Terms/Consent 文本的 checkbox
                b = frame.locator(
                    "label:has-text('Terms') input[type=checkbox],"
                    "label:has-text('Consent') input[type=checkbox],"
                    "label:has-text('Agree') input[type=checkbox],"
                    "label:has-text('I agree') input[type=checkbox]"
                ).first
                if await b.count() and await b.is_visible() and not await b.is_checked():
                    await b.click(force=True, timeout=800)
                    checked += 1
            except Exception:
                pass
        return checked

    async def click_submit(self, frame: Frame) -> None:
        try:
            try:
                await frame.locator("body").press("PageDown")
            except:
                pass
            btn = frame.locator("button[data-testid='submit-button']").first
            if not await btn.count():
                btn = frame.locator("button[type='submit']").first

            if await btn.count():
                await btn.click(force=True)
            else:
                await frame.locator("button:has-text('Verificar'), button:has-text('Verify')").first.click(force=True)

            await self._human_delay_if_needed()
            await self._sleep(self.delays.after_submit_sec)
        except Exception as e:
            logger.error("Click submit failed err=%s", str(e)[:100])