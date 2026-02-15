from __future__ import annotations

from typing import Any, Dict, Optional

from playwright.async_api import Frame, Locator


async def safe_count(loc: Locator) -> int:
    try:
        return await loc.count()
    except Exception:
        return 0


async def val(frame: Frame, selector: str) -> Optional[str]:
    """尽量读取 property value；不存在或不可读则 None；永不阻塞。"""
    try:
        loc_all = frame.locator(selector)
        if await safe_count(loc_all) == 0:
            return None
        loc = loc_all.first

        # 先读 property value（最接近用户看到的）
        try:
            v = await loc.input_value(timeout=30)
            if v is not None:
                v = v.strip()
                if v:
                    return v
        except Exception:
            pass

        # 再读 attribute value（兜底）
        try:
            v2 = await loc.get_attribute("value")
            if v2 is not None:
                v2 = v2.strip()
                if v2:
                    return v2
        except Exception:
            pass

        return None
    except Exception:
        return None


async def text(frame: Frame, selector: str) -> Optional[str]:
    """读取文本（用于 combobox/只读输入等 value 不可靠的场景）；永不阻塞。"""
    try:
        loc_all = frame.locator(selector)
        if await safe_count(loc_all) == 0:
            return None
        loc = loc_all.first

        try:
            t = await loc.text_content(timeout=30)
            if t is not None:
                t = t.strip()
                if t:
                    return t
        except Exception:
            pass

        try:
            t2 = await loc.inner_text(timeout=30)
            if t2 is not None:
                t2 = t2.strip()
                if t2:
                    return t2
        except Exception:
            pass

        return None
    except Exception:
        return None


async def attr(frame: Frame, selector: str, name: str) -> Optional[str]:
    try:
        if await safe_count(frame.locator(selector)) == 0:
            return None
        return await frame.locator(selector).first.get_attribute(name)
    except Exception:
        return None


async def checked(frame: Frame, selector: str) -> Optional[bool]:
    try:
        if await safe_count(frame.locator(selector)) == 0:
            return None
        return await frame.locator(selector).first.is_checked()
    except Exception:
        return None


async def any_checked(frame: Frame, selectors: list[str]) -> Optional[bool]:
    """依次检查多个 selector，返回第一个可读的 checked；都不可读则 None。"""
    for sel in selectors:
        v = await checked(frame, sel)
        if v is not None:
            return v
    return None


async def ensure_consent_checked(frame: Frame) -> bool:
    selectors = [
        "input[type='checkbox'][name*='consent' i]",
        "input[type='checkbox'][id*='consent' i]",
        "input[type='checkbox'][name*='terms' i]",
        "input[type='checkbox'][id*='terms' i]",
        "input[type='checkbox'][name*='privacy' i]",
        "input[type='checkbox'][id*='privacy' i]",
        ".sid-form-region input[type='checkbox']",
    ]

    for sel in selectors:
        loc = frame.locator(sel).first
        try:
            if await loc.count() == 0:
                continue
            try:
                if not await loc.is_checked():
                    await loc.check(force=True)
            except Exception:
                await loc.click(force=True)

            try:
                return bool(await loc.is_checked())
            except Exception:
                return True
        except Exception:
            pass

    # 找不到 checkbox，尝试点同意文案行（兜底）
    row = frame.locator(":text-matches('By submitting|terms|privacy|consent', 'i')").first
    try:
        if await row.count() > 0:
            await row.click(force=True)
            return True
    except Exception:
        pass

    return False


async def ensure_consent_checked(frame: Frame) -> bool:
    selectors = [
        "input[type='checkbox'][name*='consent' i]",
        "input[type='checkbox'][id*='consent' i]",
        "input[type='checkbox'][name*='terms' i]",
        "input[type='checkbox'][id*='terms' i]",
        "input[type='checkbox'][name*='privacy' i]",
        "input[type='checkbox'][id*='privacy' i]",
        ".sid-form-region input[type='checkbox']",
    ]

    for sel in selectors:
        loc = frame.locator(sel).first
        try:
            if await loc.count() == 0:
                continue
            try:
                if not await loc.is_checked():
                    await loc.check(force=True)
            except Exception:
                await loc.click(force=True)

            try:
                return bool(await loc.is_checked())
            except Exception:
                return True
        except Exception:
            pass

    # 找不到 checkbox，尝试点同意文案行（兜底）
    row = frame.locator(":text-matches('By submitting|terms|privacy|consent', 'i')").first
    try:
        if await row.count() > 0:
            await row.click(force=True)
            return True
    except Exception:
        pass

    return False


async def _read_school_id_multi_source(frame: Frame) -> Optional[str]:
    """多源兜底读取 schoolId/organizationId（读不到返回 None）。"""
    # ---------- 1) hidden input（优先） ----------
    hidden_selectors = [
        "input[name='organizationId']",
        "input[name='schoolId']",
        "input[name='organization.id']",
        "input[name='orgId']",
        "input[name='organizationID']",
        "input[name='schoolID']",
        "input[id*='organization'][type='hidden']",
        "input[id*='school'][type='hidden']",
    ]
    for sel in hidden_selectors:
        try:
            if await safe_count(frame.locator(sel)) == 0:
                continue
            loc = frame.locator(sel).first
            try:
                v = (await loc.input_value(timeout=30) or "").strip()
                if v:
                    return v
            except Exception:
                pass
            try:
                v2 = (await loc.get_attribute("value") or "").strip()
                if v2:
                    return v2
            except Exception:
                pass
        except Exception:
            pass

    # ---------- 2) 学校输入框 data-selected-id / dataset ----------
    input_selectors = [
        "#sid-college-name",
        ".sid-college-name-id #sid-college-name",
        ".sid-college-name-id input[role='combobox']",
        "input[aria-controls*='college'][role='combobox']",
        # 放最后，防止误命中语言控件
        "input[role='combobox'][aria-controls]",
    ]
    for sel in input_selectors:
        try:
            if await safe_count(frame.locator(sel)) == 0:
                continue
            inp = frame.locator(sel).first

            for a in ("data-selected-id", "data-selectedid", "data-org-id", "data-school-id"):
                try:
                    v = (await inp.get_attribute(a) or "").strip()
                    if v:
                        return v
                except Exception:
                    pass

            # aria-activedescendant -> option data-*
            try:
                v_ad = await inp.evaluate(
                    """(el) => {
  const ad = el?.getAttribute?.('aria-activedescendant');
  if (!ad) return '';
  const opt = document.getElementById(ad);
  if (!opt) return '';
  return (opt.getAttribute('data-value') ||
          opt.getAttribute('data-id') ||
          opt.getAttribute('value') || '').toString().trim();
}"""
                )
                if v_ad:
                    return v_ad
            except Exception:
                pass

            try:
                v_js = await inp.evaluate(
                    """el => (el?.dataset?.selectedId ||
                              el?.dataset?.selectedid ||
                              el?.dataset?.orgId ||
                              el?.dataset?.schoolId || '').toString().trim()"""
                )
                if v_js:
                    return v_js
            except Exception:
                pass
        except Exception:
            pass

    # ---------- 3) selected 容器上的 data-* ----------
    try:
        selected_wrap_sel = ".sid-college-name-id .sid-selected-org__container"
        if await safe_count(frame.locator(selected_wrap_sel)) > 0:
            selected_wrap = frame.locator(selected_wrap_sel).first
            for a in ("data-id", "data-value", "data-org-id", "data-school-id", "data-selected-id"):
                try:
                    v = (await selected_wrap.get_attribute(a) or "").strip()
                    if v:
                        return v
                except Exception:
                    pass
            try:
                v_js = await selected_wrap.evaluate(
                    """el => (el?.dataset?.id ||
                              el?.dataset?.value ||
                              el?.dataset?.orgId ||
                              el?.dataset?.schoolId ||
                              el?.dataset?.selectedId || '').toString().trim()"""
                )
                if v_js:
                    return v_js
            except Exception:
                pass
    except Exception:
        pass

    # ---------- 4) 选项节点 data-*（有些模板点击后仍保留） ----------
    option_selectors = [
        "[role='option'][aria-selected='true']",
        "[role='option'][data-selected='true']",
        "[role='option'].selected",
        "[role='option'][id*='college']",
    ]
    for sel in option_selectors:
        try:
            if await safe_count(frame.locator(sel)) == 0:
                continue
            opt = frame.locator(sel).first
            for a in ("data-value", "data-id", "data-org-id", "data-school-id", "value"):
                try:
                    v = (await opt.get_attribute(a) or "").strip()
                    if v:
                        return v
                except Exception:
                    pass
        except Exception:
            pass

    return None


async def _read_school_id_from_org_container_descendants(frame: Frame) -> Optional[str]:
    """从已选中学校容器附近（descendants）兜底找 hidden/id。"""
    try:
        base_sel = ".sid-college-name-id .sid-selected-org__container"
        if await safe_count(frame.locator(base_sel)) == 0:
            return None
        base = frame.locator(base_sel).first

        candidates = [
            "input[type='hidden'][name='organizationId']",
            "input[type='hidden'][name='schoolId']",
            "input[type='hidden'][name='organization.id']",
            "input[type='hidden'][name*='organization' i]",
            "input[type='hidden'][name*='school' i]",
            "input[type='hidden'][id*='organization' i]",
            "input[type='hidden'][id*='school' i]",
        ]
        for sel in candidates:
            try:
                locs = base.locator(sel)
                if await safe_count(locs) == 0:
                    continue
                loc = locs.first
                try:
                    v = (await loc.input_value(timeout=30) or "").strip()
                    if v:
                        return v
                except Exception:
                    pass
                try:
                    v2 = (await loc.get_attribute("value") or "").strip()
                    if v2:
                        return v2
                except Exception:
                    pass
            except Exception:
                pass
        return None
    except Exception:
        return None
    except Exception:
        return None


async def _read_school_id_from_js(frame: Frame) -> Optional[str]:
    """最后兜底：直接在页面里扫 hidden inputs / dataset，尽量提取 org/school id。"""
    try:
        v = await frame.evaluate(
            """() => {
  const isIdLike = (s) => typeof s === 'string' && s.trim() && /[a-z0-9]{6,}/i.test(s.trim());
  const pick = (s) => (typeof s === 'string' ? s.trim() : '');

  // 1) hidden inputs（全页面）
  const hidden = Array.from(document.querySelectorAll("input[type='hidden']")).map(el => ({
    name: el.getAttribute('name') || '',
    id: el.getAttribute('id') || '',
    value: pick(el.value || el.getAttribute('value') || ''),
  }));
  for (const x of hidden) {
    const key = `${x.name} ${x.id}`.toLowerCase();
    if (!/(school|org|organization)/i.test(key)) continue;
    if (isIdLike(x.value)) return x.value;
  }

  // 2) combobox / selected container dataset
  const input = document.querySelector("#sid-college-name, .sid-college-name-id input[role='combobox']");
  if (input && input.dataset) {
    for (const k of ['selectedId','selectedid','orgId','schoolId','organizationId']) {
      const val = pick(input.dataset[k]);
      if (isIdLike(val)) return val;
    }
  }
  const wrap = document.querySelector(".sid-college-name-id .sid-selected-org__container");
  if (wrap && wrap.dataset) {
    for (const k of ['id','value','orgId','schoolId','selectedId']) {
      const val = pick(wrap.dataset[k]);
      if (isIdLike(val)) return val;
    }
  }
  return '';
}"""
        )
        v = (v or "").strip()
        return v or None
    except Exception:
        return None
    except Exception:
        return None


async def dump_form_ready(frame: Frame) -> Dict[str, Any]:
    # --- 收窄 selector（白名单），避免宽匹配误命中 ---
    FIRST_NAME_SEL = 'input[name="firstName"], input[id="sid-first-name"], input[autocomplete="given-name"]'
    LAST_NAME_SEL = 'input[name="lastName"], input[id="sid-last-name"], input[autocomplete="family-name"]'
    EMAIL_SEL = 'input[type="email"], input[name="email"], input[id="sid-email"]'

    DOB_MONTH_SEL = 'input[name*="birthDate.month"], input[name*="dob.month"], input[id*="month"]'
    DOB_DAY_SEL = 'input[name*="birthDate.day"], input[name*="dob.day"], input[id*="day"]'
    DOB_YEAR_SEL = 'input[name*="birthDate.year"], input[name*="dob.year"], input[id*="year"]'
    DOB_SINGLE_SEL = 'input[name="birthDate"], input[name*="dob"], input[id*="birth"]'

    SCHOOL_TEXT_SEL = (
        ".sid-college-name-id .sid-selected-org__name, "
        "#sid-college-name, "
        "input[name=\"organization.name\"]"
    )
    SCHOOL_ID_SEL = (
        'input[name="schoolId"], '
        'input[name="organizationId"], '
        'input[name="organization.id"], '
        'input[name="schoolID"], '
        'input[name="organizationID"], '
        'input[name*="school" i][type="hidden"], '
        'input[name*="organization" i][type="hidden"], '
        'input[id*="school" i][type="hidden"], '
        'input[id*="organization" i][type="hidden"], '
        'input[id*="org" i][type="hidden"]'
    )

    CONSENT_SEL = (
        'input[type="checkbox"][name*="consent"],'
        'input[type="checkbox"][name*="agree"],'
        'input[type="checkbox"][name*="terms"],'
        'input[type="checkbox"][aria-label*="consent" i],'
        'input[type="checkbox"][aria-label*="agree" i],'
        'input[type="checkbox"][aria-label*="terms" i]'
    )

    async def _dump_school_debug() -> Dict[str, Any]:
        """仅用于诊断：把学校控件相关信息一次性打出来，便于定位 schoolId 存在于哪里。"""
        try:
            return await frame.evaluate(
                """() => {
  const root = document.querySelector('.sid-college-name-id') || document;
  const input =
    root.querySelector('#sid-college-name') ||
    root.querySelector("input[role='combobox'][aria-controls]") ||
    root.querySelector("input[role='combobox']") ||
    root.querySelector("input[name='organization.name']") ||
    null;

  const inputInfo = input ? {
    id: input.id || null,
    name: input.getAttribute('name') || null,
    value: (input.value || '').trim() || null,
    ariaControls: input.getAttribute('aria-controls'),
    ariaActive: input.getAttribute('aria-activedescendant'),
    dataSelectedId: (input.dataset && (input.dataset.selectedId || input.dataset.selectedid || input.dataset.orgId || input.dataset.schoolId)) || input.getAttribute('data-selected-id') || null,
  } : null;

  const menuId = input ? input.getAttribute('aria-controls') : null;
  const menu = menuId ? document.getElementById(menuId) : null;

  const selectedName = root.querySelector('.sid-selected-org__name')?.textContent?.trim() || null;
  const selectedWrap = root.querySelector('.sid-selected-org__container') || null;
  const selectedWrapAttrs = selectedWrap ? {
    id: selectedWrap.getAttribute('id'),
    dataId: selectedWrap.getAttribute('data-id'),
    dataValue: selectedWrap.getAttribute('data-value'),
    dataOrgId: selectedWrap.getAttribute('data-org-id'),
    dataSchoolId: selectedWrap.getAttribute('data-school-id'),
    dataSelectedId: selectedWrap.getAttribute('data-selected-id'),
  } : null;

  const hiddenCandidates = Array.from(document.querySelectorAll("input[type='hidden']")).map(el => ({
    name: el.getAttribute('name'),
    id: el.getAttribute('id'),
    value: (el.value || '').trim(),
  })).filter(x => (x.name && /school|org|organization/i.test(x.name)) || (x.id && /school|org|organization/i.test(x.id)));

  const activeId = input ? input.getAttribute('aria-activedescendant') : null;
  const activeEl = activeId ? document.getElementById(activeId) : null;
  const activeAttrs = activeEl ? {
    id: activeEl.id || null,
    text: (activeEl.textContent || '').trim() || null,
    dataValue: activeEl.getAttribute('data-value'),
    dataId: activeEl.getAttribute('data-id'),
    value: activeEl.getAttribute('value'),
  } : null;

  return {
    selectedName,
    inputInfo,
    menuFound: !!menu,
    menuRoleOptionCount: menu ? menu.querySelectorAll("[role='option']").length : 0,
    activeOption: activeAttrs,
    selectedWrapAttrs,
    hiddenCandidates,
  };
}"""
            )
        except Exception:
            return {}

    async def _dump_school_debug() -> Dict[str, Any]:
        """仅用于诊断：把学校控件相关信息一次性打出来，便于定位 schoolId 存在于哪里。"""
        try:
            return await frame.evaluate(
                """() => {
  const root = document.querySelector('.sid-college-name-id') || document;
  const input =
    root.querySelector('#sid-college-name') ||
    root.querySelector("input[role='combobox'][aria-controls]") ||
    root.querySelector("input[role='combobox']") ||
    root.querySelector("input[name='organization.name']") ||
    null;

  const inputInfo = input ? {
    id: input.id || null,
    name: input.getAttribute('name') || null,
    value: (input.value || '').trim() || null,
    ariaControls: input.getAttribute('aria-controls'),
    ariaActive: input.getAttribute('aria-activedescendant'),
    dataSelectedId: (input.dataset && (input.dataset.selectedId || input.dataset.selectedid || input.dataset.orgId || input.dataset.schoolId)) || input.getAttribute('data-selected-id') || null,
  } : null;

  const menuId = input ? input.getAttribute('aria-controls') : null;
  const menu = menuId ? document.getElementById(menuId) : null;

  const selectedName = root.querySelector('.sid-selected-org__name')?.textContent?.trim() || null;
  const selectedWrap = root.querySelector('.sid-selected-org__container') || null;
  const selectedWrapAttrs = selectedWrap ? {
    id: selectedWrap.getAttribute('id'),
    dataId: selectedWrap.getAttribute('data-id'),
    dataValue: selectedWrap.getAttribute('data-value'),
    dataOrgId: selectedWrap.getAttribute('data-org-id'),
    dataSchoolId: selectedWrap.getAttribute('data-school-id'),
    dataSelectedId: selectedWrap.getAttribute('data-selected-id'),
  } : null;

  const hiddenCandidates = Array.from(document.querySelectorAll("input[type='hidden']")).map(el => ({
    name: el.getAttribute('name'),
    id: el.getAttribute('id'),
    value: (el.value || '').trim(),
  })).filter(x => (x.name && /school|org|organization/i.test(x.name)) || (x.id && /school|org|organization/i.test(x.id)));

  const activeId = input ? input.getAttribute('aria-activedescendant') : null;
  const activeEl = activeId ? document.getElementById(activeId) : null;
  const activeAttrs = activeEl ? {
    id: activeEl.id || null,
    text: (activeEl.textContent || '').trim() || null,
    dataValue: activeEl.getAttribute('data-value'),
    dataId: activeEl.getAttribute('data-id'),
    value: activeEl.getAttribute('value'),
  } : null;

  return {
    selectedName,
    inputInfo,
    menuFound: !!menu,
    menuRoleOptionCount: menu ? menu.querySelectorAll("[role='option']").length : 0,
    activeOption: activeAttrs,
    selectedWrapAttrs,
    hiddenCandidates,
  };
}"""
            )
        except Exception:
            return {}

    school_id = await _read_school_id_multi_source(frame)
    if not school_id:
        school_id = await _read_school_id_from_org_container_descendants(frame)
    if not school_id:
        school_id = await _read_school_id_from_js(frame)
    if not school_id:
        school_id = await val(frame, SCHOOL_ID_SEL)

    # consent：预提交前强制勾选；同时仍记录可读状态用于诊断
    consent_checked = await ensure_consent_checked(frame)
    if consent_checked is False:
        consent_checked = await any_checked(
            frame,
            [
                CONSENT_SEL,
                "input[name*='consent' i]",
                "input[id*='consent' i]",
                "input[type='checkbox'][required]",
                "input[type='checkbox']",
            ],
        )

    try:
        invalid_count = await frame.locator('[aria-invalid="true"]').count()
    except Exception:
        invalid_count = -1

    m = await val(frame, DOB_MONTH_SEL)
    d = await val(frame, DOB_DAY_SEL)
    y = await val(frame, DOB_YEAR_SEL)
    dob = f"{y}-{m}-{d}" if (y and m and d) else (await val(frame, DOB_SINGLE_SEL))

    # schoolText：优先 selected 容器 name + location（避免粘连/歧义）
    selected_name = await text(frame, ".sid-college-name-id .sid-selected-org__name")
    selected_loc = await text(frame, ".sid-college-name-id .sid-selected-org__location")

    school_text = None
    if selected_name:
        n = selected_name.strip()
        l = (selected_loc or "").strip()

        # 防止 name 已经包含 location，再重复拼接
        if l and l.lower() in n.lower():
            school_text = n
        else:
            school_text = f"{n} {l}".strip()

    if not school_text:
        school_text = (await val(frame, SCHOOL_TEXT_SEL)) or (await text(frame, SCHOOL_TEXT_SEL))

    data: Dict[str, Any] = {
        "firstName": await val(frame, FIRST_NAME_SEL),
        "lastName": await val(frame, LAST_NAME_SEL),
        "email": await val(frame, EMAIL_SEL),
        "dob": dob,
        "schoolText": school_text,
        "schoolId": school_id,
        "consentChecked": consent_checked,
        "submitDisabled": await attr(frame, 'button[type="submit"]', "disabled"),
        "submitAriaDisabled": await attr(frame, 'button[type="submit"]', "aria-disabled"),
        "ariaInvalidCount": invalid_count,
    }
    # 额外 school 调试信息（不影响提交逻辑）
    try:
        data["schoolDebug"] = await _dump_school_debug()
    except Exception:
        pass
    print("[diagnostics] form_values", data)
    return data