"""PNG 截图生成模块（仅测试用样例页面，不代表真实在读证明）"""
import random
from datetime import datetime
import os
from pathlib import Path
from string import Template
import asyncio
import threading
import atexit
from typing import Callable, TypeVar

try:
    from . import config
except Exception:  # 兼容脚本直跑（python one/img_generator.py）
    import importlib.util

    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "config.py")
    spec = importlib.util.spec_from_file_location("one_local.config", cfg_path)
    if not spec or not spec.loader:
        raise ImportError(f"无法加载 one/config.py: {cfg_path}")
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)


T = TypeVar("T")


def _now(fixed_datetime: str | None = None) -> datetime:
    """返回当前时间。

    优先级：
    1) fixed_datetime 参数（优先）
    2) 环境变量 ONE_FIXED_DATETIME
    3) datetime.now()

    示例：
    - fixed_datetime="2026-02-01T13:25:00"
    - ONE_FIXED_DATETIME=2026-02-08T12:00:00
    """
    raw = (fixed_datetime or "").strip()
    if not raw:
        raw = (os.getenv("ONE_FIXED_DATETIME", "") or "").strip()
    if not raw:
        return datetime.now()

    try:
        # 兼容 ISO8601 的 Z
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except Exception:
        return datetime.now()


MAJOR_OPTIONS = [
    "Computer Science (BS)",
    "Software Engineering (BS)",
    "Information Sciences and Technology (BS)",
    "Data Science (BS)",
    "Electrical Engineering (BS)",
    "Mechanical Engineering (BS)",
    "Business Administration (BS)",
    "Psychology (BA)",
]

# 复用 Playwright 浏览器，避免每次生成 PNG/PDF 都启动一次 Chromium（很耗时）
# 注意：Playwright Sync API 对象不支持跨线程复用；因此缓存必须“按线程”隔离。
_PW = None
_BROWSER = None
_PW_THREAD_ID: int | None = None
_PW_LOCK = threading.Lock()


def _close_cached_browser() -> None:
    global _PW, _BROWSER, _PW_THREAD_ID
    try:
        if _BROWSER is not None:
            _BROWSER.close()
    except Exception:
        pass
    try:
        if _PW is not None:
            _PW.stop()
    except Exception:
        pass
    _BROWSER = None
    _PW = None
    _PW_THREAD_ID = None


atexit.register(_close_cached_browser)


def _get_browser():
    """获取（并按需初始化）全局复用的 Chromium browser。

    重要：Playwright 的 Sync API 对象不能跨线程使用。
    如果检测到当前线程与缓存创建线程不同，则自动降级为“无缓存实例”。
    """
    global _PW, _BROWSER, _PW_THREAD_ID

    # 需要完全隔离/排查时可禁用缓存
    if (os.getenv("ONE_PW_NO_CACHE", "") or "").strip().lower() in ("1", "true", "yes", "on"):
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        return pw, browser, False

    cur_tid = threading.get_ident()
    with _PW_LOCK:
        # 若缓存来自其它线程，则不要复用（否则会报：cannot switch to a different thread ...）
        if _PW_THREAD_ID is not None and _PW_THREAD_ID != cur_tid:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            return pw, browser, False

        if _PW is None or _BROWSER is None:
            from playwright.sync_api import sync_playwright
            _PW = sync_playwright().start()
            _BROWSER = _PW.chromium.launch(headless=True)
            _PW_THREAD_ID = cur_tid
        return _PW, _BROWSER, True


def _in_asyncio_loop() -> bool:
    """当前线程是否处于运行中的 asyncio 事件循环内。"""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _run_in_thread(fn: Callable[[], T]) -> T:
    """在后台线程运行阻塞函数，并把异常/返回值传回当前线程。"""
    out: dict[str, object] = {}

    def _target() -> None:
        try:
            out["value"] = fn()
        except Exception as e:
            out["error"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join()

    if "error" in out:
        raise out["error"]  # type: ignore[misc]
    return out["value"]  # type: ignore[return-value]


def _template_candidates(env_path: str, file_names: list[str]) -> list[Path]:
    """构建模板候选路径：env -> 当前目录 -> 上级目录。"""
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))

    base_dirs = [Path(__file__).resolve().parent, Path(__file__).resolve().parents[1]]
    for base in base_dirs:
        for name in file_names:
            candidates.append(base / name)

    seen: set[Path] = set()
    unique: list[Path] = []
    for item in candidates:
        key = item.resolve() if item.exists() else item
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _escape_template_dollars(template_text: str) -> str:
    """避免 string.Template 因为模板内出现意外的 $xxx 触发 ValueError。

    规则：保留我们约定的占位符，其余所有 $ 统一转义为 $$。

    注意：收据模板 test2.html 也使用 $run_date/$receipt_no 等变量，这里一并白名单。
    """
    placeholders = [
        # schedule/test1.html
        "$first_name",
        "$last_name",
        "$name",
        "$psu_id",
        "$major",
        "$date",
        "$school_id",
        "$school_name",
        "$school_city",
        "$school_state",
        "$school_country",
        "$school_domain",
        "$school_city_state",
        # receipt/test2.html
        "$student_id",
        "$term",
        "$career",
        "$campus",
        "$account",
        "$receipt_no",
        "$txn_id",
        "$payment_dt",
        "$pay_method",
        "$auth_code",
        "$status",
        "$posted_on",
        "$posted_by",
        "$location",
        "$run_date",
        "$run_time",
        "$total_amount",
    ]

    tokens = {ph: f"__TPL_TOKEN_{i}__" for i, ph in enumerate(placeholders)}
    for ph, token in tokens.items():
        template_text = template_text.replace(ph, token)

    template_text = template_text.replace("$", "$$")

    for ph, token in tokens.items():
        template_text = template_text.replace(token, ph)

    return template_text


def generate_psu_id():
    """生成随机 PSU ID (9位数字)"""
    return f"9{random.randint(10000000, 99999999)}"


def generate_psu_email(first_name: str, last_name: str) -> str:
    """生成 PSU 邮箱（清理空格/特殊符号后拼接）。"""
    digit_count = random.choice([3, 4])
    digits = "".join(str(random.randint(0, 9)) for _ in range(digit_count))

    def _norm(v: str) -> str:
        raw = (v or "").strip().lower()
        cleaned = "".join(ch for ch in raw if ch.isalnum())
        return cleaned or "student"

    email = f"{_norm(first_name)}.{_norm(last_name)}{digits}@psu.edu"
    return email


def _get_school_template_context(school_id: str) -> dict[str, str]:
    """基于 config.SCHOOLS 生成模板可用的学校字段。"""
    school = config.get_school(str(school_id)) if hasattr(config, "get_school") else None
    school = school or {}
    city = str(school.get("city", "")).strip()
    state = str(school.get("state", "")).strip()
    city_state = ", ".join(v for v in (city, state) if v)

    return {
        "school_name": str(school.get("name", "")).strip(),
        "school_city": city,
        "school_state": state,
        "school_country": str(school.get("country", "US")).strip(),
        "school_domain": str(school.get("domain", "")).strip().lower(),
        "school_city_state": city_state,
    }


def generate_html(first_name, last_name, school_id='2565'):
    """
    生成 Penn State LionPATH HTML

    Args:
        first_name: 名字
        last_name: 姓氏
        school_id: 学校 ID

    Returns:
        str: HTML 内容
    """
    psu_id = generate_psu_id()
    name = f"{first_name} {last_name}"
    # 固定为 02/01/2026，且不显示小时
    date = _now(fixed_datetime="2026-02-01T12:00:00").strftime('%m/%d/%Y')

    # 随机选择专业
    major = random.choice(MAJOR_OPTIONS)
    school_ctx = _get_school_template_context(str(school_id))

    # 优先使用外置模板 test1.html。
    # - 默认路径：项目根目录/test1.html（与 web_app.py 同级）
    # - 可用环境变量 TEST_HTML_PATH 指定绝对/相对路径
    # - 如果设置 REQUIRE_TEST_HTML=1，则找不到模板会直接报错（不回退到内置 HTML）
    require_test_html = (os.getenv("REQUIRE_TEST_HTML", "0") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    env_path = (os.getenv("TEST_HTML_PATH", "") or "").strip()
    candidates = _template_candidates(env_path, ["test1", "test1.html"])

    for template_path in candidates:
        try:
            if not template_path.is_file():
                continue
            raw = template_path.read_text(encoding="utf-8")
            raw = _escape_template_dollars(raw)
            tpl = Template(raw)
            return tpl.safe_substitute(
                first_name=first_name,
                last_name=last_name,
                name=name,
                psu_id=psu_id,
                major=major,
                date=date,
                school_id=school_id,
                **school_ctx,
            )
        except Exception:
            continue

    if require_test_html:
        tried = ", ".join(str(p) for p in candidates)
        raise FileNotFoundError(f"未找到可用的 test1.html 模板，已尝试: {tried}")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Demo Portal - Student Home (Sample)</title>
    <style>
        :root {{
            --psu-blue: #1E407C; /* Penn State Nittany Navy */
            --psu-light-blue: #96BEE6;
            --bg-gray: #f4f4f4;
            --text-color: #333;
        }}

        body {{
            font-family: "Roboto", "Helvetica Neue", Helvetica, Arial, sans-serif;
            background-color: #e0e0e0; /* 浏览器背景 */
            margin: 0;
            padding: 20px;
            color: var(--text-color);
            display: flex;
            justify-content: center;
        }}


        /* 模拟浏览器窗口 */
        .viewport {{
            width: 100%;
            max-width: 1100px;
            background-color: #fff;
            box-shadow: 0 5px 20px rgba(0,0,0,0.15);
            min-height: 800px;
            display: flex;
            flex-direction: column;
        }}

        /* 顶部导航栏 LionPATH */
        .header {{
            background-color: var(--psu-blue);
            color: white;
            padding: 0 20px;
            height: 60px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .brand {{
            display: flex;
            align-items: center;
            gap: 15px;
        }}

        /* PSU Logo 模拟 */
        .psu-logo {{
            font-family: "Georgia", serif;
            font-size: 20px;
            font-weight: bold;
            letter-spacing: 1px;
            border-right: 1px solid rgba(255,255,255,0.3);
            padding-right: 15px;
        }}

        .system-name {{
            font-size: 18px;
            font-weight: 300;
        }}

        .user-menu {{
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 20px;
        }}

        .nav-bar {{
            background-color: #f8f8f8;
            border-bottom: 1px solid #ddd;
            padding: 10px 20px;
            font-size: 13px;
            color: #666;
            display: flex;
            gap: 20px;
        }}
        .nav-item {{ cursor: pointer; }}
        .nav-item.active {{ color: var(--psu-blue); font-weight: bold; border-bottom: 2px solid var(--psu-blue); padding-bottom: 8px; }}

        /* 主内容区 */
        .content {{
            padding: 30px;
            flex: 1;
        }}

        .page-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            margin-bottom: 20px;
            border-bottom: 1px solid #eee;
            padding-bottom: 10px;
        }}

        .page-title {{
            font-size: 24px;
            color: var(--psu-blue);
            margin: 0;
        }}

        .term-selector {{
            background: #fff;
            border: 1px solid #ccc;
            padding: 5px 10px;
            border-radius: 4px;
            font-size: 14px;
            color: #333;
            font-weight: bold;
        }}

        /* 学生信息卡片 */
        .student-card {{
            background: #fcfcfc;
            border: 1px solid #e0e0e0;
            padding: 15px;
            margin-bottom: 25px;
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            font-size: 13px;
        }}
        .info-label {{ color: #777; font-size: 11px; text-transform: uppercase; margin-bottom: 4px; }}
        .info-val {{ font-weight: bold; color: #333; font-size: 14px; }}
        .status-badge {{
            background-color: #e6fffa; color: #007a5e;
            padding: 4px 8px; border-radius: 4px; font-weight: bold; border: 1px solid #b2f5ea;
        }}

        /* 课程表 */
        .schedule-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        .schedule-table th {{
            text-align: left;
            padding: 12px;
            background-color: #f0f0f0;
            border-bottom: 2px solid #ccc;
            color: #555;
        }}

        .schedule-table td {{
            padding: 15px 12px;
            border-bottom: 1px solid #eee;
        }}

        .course-code {{ font-weight: bold; color: var(--psu-blue); }}
        .course-title {{ font-weight: 500; }}

        /* 打印适配 */
        @media print {{
            body {{ background: white; padding: 0; }}
            .viewport {{ box-shadow: none; max-width: 100%; min-height: auto; }}
            .nav-bar {{ display: none; }}
            @page {{ margin: 1cm; size: landscape; }}
        }}
    </style>
</head>
<body>

<div class="viewport">
    <div class="header">
        <div class="brand">
            <div class="psu-logo">PennState</div>
            <div class="system-name">LionPATH</div>
        </div>
        <div class="user-menu">
            <span>Welcome, <strong>{name}</strong></span>
            <span>|</span>
            <span>Sign Out</span>
        </div>
    </div>

    <div class="nav-bar">
        <div class="nav-item">Student Home</div>
        <div class="nav-item active">My Class Schedule</div>
        <div class="nav-item">Academics</div>
        <div class="nav-item">Finances</div>
        <div class="nav-item">Campus Life</div>
    </div>

    <div class="content">
        <div class="page-header">
            <h1 class="page-title">My Class Schedule</h1>
            <div class="term-selector">
                Term: <strong>Fall 2026</strong> (Fem 2026 - Dec 12)
            </div>
        </div>

        <div class="student-card">
            <div>
                <div class="info-label">Student Name</div>
                <div class="info-val">{name}</div>
            </div>
            <div>
                <div class="info-label">PSU ID</div>
                <div class="info-val">{psu_id}</div>
            </div>
            <div>
                <div class="info-label">Academic Program</div>
                <div class="info-val">{major}</div>
            </div>
            <div>
                <div class="info-label">Enrollment Status</div>
                <div class="status-badge">✅ Enrolled</div>
            </div>
        </div>

        <div style="margin-bottom: 10px; font-size: 12px; color: #666; text-align: right;">
            Data retrieved: <span>{date}</span>
        </div>

        <table class="schedule-table">
            <thead>
                <tr>
                    <th width="10%">Class Nbr</th>
                    <th width="15%">Course</th>
                    <th width="35%">Title</th>
                    <th width="20%">Days & Times</th>
                    <th width="10%">Room</th>
                    <th width="10%">Units</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>14920</td>
                    <td class="course-code">CMPSC 465</td>
                    <td class="course-title">Data Structures and Algorithms</td>
                    <td>MoWeFr 10:10AM - 11:00AM</td>
                    <td>Willard 062</td>
                    <td>3.00</td>
                </tr>
                <tr>
                    <td>18233</td>
                    <td class="course-code">MATH 230</td>
                    <td class="course-title">Calculus and Vector Analysis</td>
                    <td>TuTh 1:35PM - 2:50PM</td>
                    <td>Thomas 102</td>
                    <td>4.00</td>
                </tr>
                <tr>
                    <td>20491</td>
                    <td class="course-code">CMPSC 473</td>
                    <td class="course-title">Operating Systems Design</td>
                    <td>MoWe 2:30PM - 3:45PM</td>
                    <td>Westgate E201</td>
                    <td>3.00</td>
                </tr>
                <tr>
                    <td>11029</td>
                    <td class="course-code">ENGL 202C</td>
                    <td class="course-title">Technical Writing</td>
                    <td>Fr 1:25PM - 2:15PM</td>
                    <td>Boucke 304</td>
                    <td>3.00</td>
                </tr>
                <tr>
                    <td>15502</td>
                    <td class="course-code">STAT 318</td>
                    <td class="course-title">Elementary Probability</td>
                    <td>TuTh 9:05AM - 10:20AM</td>
                    <td>Osmond 112</td>
                    <td>3.00</td>
                </tr>
            </tbody>
        </table>

        <div style="margin-top: 50px; border-top: 1px solid #ddd; padding-top: 10px; font-size: 11px; color: #888; text-align: center;">
            &copy; 2026 The Pennsylvania State University. All rights reserved.<br>
            LionPATH is the student information system for Penn State.
        </div>
    </div>
</div>

</body>
</html>
"""

    return html


def _rand_alnum(n: int) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(n))


def generate_receipt_html(
    first_name: str,
    last_name: str,
    student_id: str | None = None,
    term: str = "Spring 2026",
    total_amount: str = "$7,220.00",
    fixed_datetime: str | None = None,
) -> str:
    """用收据模板生成 HTML（支持 test3/test3.html，其次 test2/test2.html）。"""
    name = f"{first_name} {last_name}".strip()
    now = _now(fixed_datetime=fixed_datetime)

    if not student_id:
        student_id = str(random.randint(2026000000, 2026999999))

    receipt_no = f"R-{now.strftime('%Y%m%d')}-{random.randint(1, 99999):05d}"
    txn_id = f"TXN-{_rand_alnum(4)}-{_rand_alnum(4)}-{_rand_alnum(4)}"

    mapping = {
        "name": name,
        "student_id": str(student_id),
        "term": term,
        "career": "UGRD",
        "campus": "MAIN",
        "account": "STDNT_AR_01",
        "receipt_no": receipt_no,
        "txn_id": txn_id,
        "payment_dt": now.strftime("%m/%d/%Y %I:%M %p PT"),
        "pay_method": "VISA **** 4821",
        "auth_code": _rand_alnum(6),
        "status": "PAID",
        "posted_on": now.strftime("%m/%d/%Y"),
        "posted_by": "ARPOST01",
        "location": "Online Portal",
        "run_date": now.strftime("%m/%d/%Y"),
        "run_time": now.strftime("%I:%M:%S %p PT"),
        "total_amount": total_amount,
    }

    # 优先使用外置模板。
    # - 可用环境变量 RECEIPT_HTML_PATH 指定绝对/相对路径
    # - 默认查找：test3/test3.html（你当前命名）-> test2/test2.html（历史命名）
    env_path = (os.getenv("RECEIPT_HTML_PATH", "") or "").strip()
    candidates = _template_candidates(env_path, ["test3", "test3.html", "test2", "test2.html"])

    for template_path in candidates:
        if not template_path.is_file():
            continue
        raw = template_path.read_text(encoding="utf-8")
        raw = _escape_template_dollars(raw)
        tpl = Template(raw)
        return tpl.safe_substitute(**mapping)

    tried = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"未找到可用收据模板（test3/test3.html/test2/test2.html），已尝试: {tried}")


def generate_receipt_image(
    first_name: str,
    last_name: str,
    student_id: str | None = None,
    term: str = "Spring 2026",
    total_amount: str = "$7,220.00",
    fixed_datetime: str | None = None,
) -> bytes:
    """生成收据 PNG（基于收据模板 test3/test2）。"""

    def _impl() -> bytes:
        try:
            from playwright.sync_api import sync_playwright

            html_content = generate_receipt_html(
                first_name=first_name,
                last_name=last_name,
                student_id=student_id,
                term=term,
                total_amount=total_amount,
                fixed_datetime=fixed_datetime,
            )

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1100, "height": 900})
                page.set_content(html_content, wait_until="load")
                page.wait_for_timeout(300)
                screenshot_bytes = page.screenshot(type="png", full_page=True)
                browser.close()

            return screenshot_bytes
        except ImportError:
            raise Exception("需要安装 playwright: pip install playwright && playwright install chromium")
        except Exception as e:
            raise Exception(f"生成收据图片失败: {str(e)}")

    if _in_asyncio_loop():
        return _run_in_thread(_impl)
    return _impl()


def generate_image(first_name, last_name, school_id='2565'):
    """生成 Penn State LionPATH 截图 PNG（基于 test1.html）。"""

    def _impl() -> bytes:
        try:
            html_content = generate_html(first_name, last_name, school_id)

            pw, browser, cached = _get_browser()
            page = browser.new_page(viewport={'width': 1200, 'height': 900})
            try:
                page.set_content(html_content, wait_until='load')
                page.wait_for_timeout(500)
                screenshot_bytes = page.screenshot(type='png', full_page=True)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
                if not cached:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    try:
                        pw.stop()
                    except Exception:
                        pass

            return screenshot_bytes

        except ImportError:
            raise Exception("需要安装 playwright: pip install playwright && playwright install chromium")
        except Exception as e:
            raise Exception(f"生成图片失败: {str(e)}")

    # 关键修复：若当前线程处于 asyncio loop 内，Playwright Sync API 会报错。
    if _in_asyncio_loop():
        return _run_in_thread(_impl)
    return _impl()


def generate_pdf(first_name, last_name, school_id='2565') -> bytes:
    """生成课程页 PDF（基于 test1.html）。"""

    def _impl() -> bytes:
        try:
            html_content = generate_html(first_name, last_name, school_id)

            pw, browser, cached = _get_browser()
            page = browser.new_page(viewport={'width': 1200, 'height': 900})
            try:
                page.set_content(html_content, wait_until='load')
                page.emulate_media(media="print")
                page.wait_for_timeout(300)
                pdf_bytes = page.pdf(
                    format="Letter",
                    print_background=True,
                    prefer_css_page_size=True,
                )
            finally:
                try:
                    page.close()
                except Exception:
                    pass
                if not cached:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    try:
                        pw.stop()
                    except Exception:
                        pass

            return pdf_bytes

        except ImportError:
            raise Exception("需要安装 playwright: pip install playwright && playwright install chromium")
        except Exception as e:
            raise Exception(f"生成 PDF 失败: {str(e)}")

    # 关键修复：若当前线程处于 asyncio loop 内，Playwright Sync API 会报错。
    if _in_asyncio_loop():
        return _run_in_thread(_impl)
    return _impl()


def generate_receipt_pdf(
    first_name: str,
    last_name: str,
    student_id: str | None = None,
    term: str = "Spring 2026",
    total_amount: str = "$7,220.00",
    fixed_datetime: str | None = None,
) -> bytes:
    """生成收据 PDF（基于收据模板 test3/test2）。"""

    def _impl() -> bytes:
        try:
            from playwright.sync_api import sync_playwright

            html_content = generate_receipt_html(
                first_name=first_name,
                last_name=last_name,
                student_id=student_id,
                term=term,
                total_amount=total_amount,
                fixed_datetime=fixed_datetime,
            )

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1100, "height": 900})
                page.set_content(html_content, wait_until="load")
                page.emulate_media(media="print")
                page.wait_for_timeout(300)
                pdf_bytes = page.pdf(
                    format="Letter",
                    print_background=True,
                    prefer_css_page_size=True,
                )
                browser.close()

            return pdf_bytes

        except ImportError:
            raise Exception("需要安装 playwright: pip install playwright && playwright install chromium")
        except Exception as e:
            raise Exception(f"生成收据 PDF 失败: {str(e)}")

    if _in_asyncio_loop():
        return _run_in_thread(_impl)
    return _impl()


if __name__ == "__main__":
    # CLI：python -m one.img_generator
    import sys
    import io
    import argparse

    # 修复 Windows 控制台编码问题
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="Generate schedule/receipt files (PNG/PDF)")
    parser.add_argument("--first", dest="first_name", default="John", help="First name")
    parser.add_argument("--last", dest="last_name", default="Smith", help="Last name")
    parser.add_argument("--school-id", dest="school_id", default="2565", help="School ID for schedule")
    parser.add_argument("--term", dest="term", default="Spring 2026", help="Term for receipt")
    parser.add_argument("--total", dest="total_amount", default="$7,220.00", help="Total amount for receipt")
    parser.add_argument(
        "--out-dir",
        dest="out_dir",
        default=".",
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--only",
        choices=["schedule", "receipt", "both"],
        default="both",
        help="What to generate",
    )
    parser.add_argument(
        "--format",
        dest="out_format",
        choices=["png", "pdf", "both"],
        default="pdf",
        help="Output format",
    )

    args = parser.parse_args()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print("生成文件...")
    print(f"姓名: {args.first_name} {args.last_name}")

    try:
        if args.only in ("schedule", "both"):
            if args.out_format in ("png", "both"):
                schedule_png = generate_image(args.first_name, args.last_name, args.school_id)
                schedule_path = os.path.join(out_dir, "schedule.png")
                with open(schedule_path, "wb") as f:
                    f.write(schedule_png)
                print(f"✓ 课程表 PNG 已生成：{schedule_path}（{len(schedule_png)} bytes）")

            if args.out_format in ("pdf", "both"):
                schedule_pdf = generate_pdf(args.first_name, args.last_name, args.school_id)
                schedule_pdf_path = os.path.join(out_dir, "schedule.pdf")
                with open(schedule_pdf_path, "wb") as f:
                    f.write(schedule_pdf)
                print(f"✓ 课程表 PDF 已生成：{schedule_pdf_path}（{len(schedule_pdf)} bytes）")

        if args.only in ("receipt", "both"):
            if args.out_format in ("png", "both"):
                receipt_png = generate_receipt_image(
                    first_name=args.first_name,
                    last_name=args.last_name,
                    term=args.term,
                    total_amount=args.total_amount,
                )
                receipt_path = os.path.join(out_dir, "receipt.png")
                with open(receipt_path, "wb") as f:
                    f.write(receipt_png)
                print(f"✓ 收据 PNG 已生成：{receipt_path}（{len(receipt_png)} bytes）")

            if args.out_format in ("pdf", "both"):
                receipt_pdf = generate_receipt_pdf(
                    first_name=args.first_name,
                    last_name=args.last_name,
                    term=args.term,
                    total_amount=args.total_amount,
                )
                receipt_pdf_path = os.path.join(out_dir, "receipt.pdf")
                with open(receipt_pdf_path, "wb") as f:
                    f.write(receipt_pdf)
                print(f"✓ 收据 PDF 已生成：{receipt_pdf_path}（{len(receipt_pdf)} bytes）")

    except Exception as e:
        print(f"✗ 错误: {e}")
        raise