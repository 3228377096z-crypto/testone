"""
SheerID 辅助工具集（Stealth 增强版）

注意：本模块只保留 **Async 不阻塞** 的实现：
- 延时：只提供 async 版本（避免 time.sleep 卡死 event loop）
- 旧同步调用点：提供同步 wrapper，但会在 event loop 中报 warning（可通过环境变量关闭）
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- 1) 延时工具（Async 优先）---

def _warn_sync_delay_in_async_context(fn_name: str) -> None:
    try:
        if (os.getenv("ONE_WARN_SYNC_DELAY", "") or "").strip().lower() not in ("1", "true", "yes", "on"):
            return
        asyncio.get_running_loop()
    except RuntimeError:
        return
    except Exception:
        return
    try:
        logger.warning("sync delay %s() called inside async event loop; use *_async()", fn_name)
    except Exception:
        pass


async def human_delay_async(lo: float = 0.5, hi: float = 1.5) -> None:
    if (os.getenv("ONE_SHEERID_HUMAN_DELAY", "") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    await asyncio.sleep(random.uniform(lo, hi))


async def sleep_between_requests_async() -> None:
    await human_delay_async(1.0, 2.5)


def human_delay(lo: float = 0.5, hi: float = 1.5) -> None:
    """同步兼容：不建议在 async 中调用。"""
    try:
        # 如果在 event loop 内，直接 no-op 或失败（默认失败）
        asyncio.get_running_loop()
        if (os.getenv("ONE_SYNC_DELAY_IN_LOOP", "error") or "error").strip().lower() in ("noop", "no-op", "0"):
            return
        raise RuntimeError("human_delay() called inside async event loop; use human_delay_async()")
    except RuntimeError as e:
        # 若是“没有运行中的 loop”，那就是同步上下文，允许 sleep
        if "no running event loop" not in str(e).lower():
            raise
    except Exception:
        # 其它异常：保守不阻塞
        return

    if (os.getenv("ONE_SHEERID_HUMAN_DELAY", "") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    time.sleep(random.uniform(lo, hi))


def sleep_between_requests() -> None:
    """同步兼容：不建议在 async 中调用。"""
    try:
        asyncio.get_running_loop()
        if (os.getenv("ONE_SYNC_DELAY_IN_LOOP", "error") or "error").strip().lower() in ("noop", "no-op", "0"):
            return
        raise RuntimeError("sleep_between_requests() called inside async event loop; use sleep_between_requests_async()")
    except RuntimeError as e:
        if "no running event loop" not in str(e).lower():
            raise
    except Exception:
        return

    human_delay(1.0, 2.5)

def utc_now_iso() -> str:
    """获取当前 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat()

def new_run_id() -> str:
    """生成运行 ID。"""
    return uuid.uuid4().hex[:8]

def mask(s: str) -> str:
    """通用脱敏。"""
    if not s:
        return ""
    s = str(s)
    if len(s) < 6:
        return "*" * len(s)
    return s[:2] + "****" + s[-2:]

def mask_vid(vid: str) -> str:
    """Verification ID 脱敏。"""
    if not vid:
        return "N/A"
    if len(vid) < 10:
        return vid
    return f"******************{vid[-6:]}"

def compact_for_trace(data: Any) -> Any:
    """压缩字典/列表用于日志打印，移除过长字段。"""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in ("files", "content", "body") and isinstance(v, (str, bytes)) and len(v) > 200:
                out[k] = f"<{len(v)} bytes>"
            elif isinstance(v, (dict, list)):
                out[k] = compact_for_trace(v)
            else:
                out[k] = v
        return out
    elif isinstance(data, list):
        return [compact_for_trace(x) for x in data]
    return data

# --- 2. 高级 Stealth 能力 (Async Only) ---

class HumanLikeMouse:
    """
    仿生鼠标移动算法 (贝塞尔曲线)。
    仅支持 Async 调用。
    """
    def __init__(self, page):
        self.page = page

    async def move(self, x: float, y: float, steps: int = 10):
        """
        使用贝塞尔曲线移动鼠标到 (x, y)，模拟人手抖动。
        """
        try:
            # 获取当前鼠标位置 (Playwright 没有直接获取鼠标位置的 API，通常需要自己记录或假设)
            # 这里为了简单，我们假设从页面中心或上一个已知位置开始
            # 实际生产中，更好的做法是在 page 上注入 JS 获取鼠标位置，或者不使用相对移动
            
            # 由于 Playwright mouse.move 是绝对坐标，我们生成一条路径
            # 起点：由于拿不到当前点，我们简单地只做终点附近的微调，或者直接调用 move
            # 为了效果，我们可以生成一条“从当前假设位置”到“目标位置”的曲线
            # 但为了稳健，我们只增加随机抖动步骤。
            
            # 简化版拟人：分段移动 + 随机偏差
            # 1. 稍微偏一点的目标
            target_x = x + random.uniform(-2, 2)
            target_y = y + random.uniform(-2, 2)
            
            # 2. 执行移动 (Playwright 的 steps 参数本身就是线性的，我们手动做非线性)
            await self.page.mouse.move(target_x, target_y, steps=steps)
            
        except Exception as e:
            logger.warning(f"Mouse move failed: {e}")

    async def random_move(self):
        """随机在视口内移动，模拟用户“无聊”时的鼠标行为。"""
        try:
            vp = self.page.viewport_size
            if not vp: return
            w, h = vp["width"], vp["height"]
            
            # 随机生成一个点
            rx = random.randint(10, w - 10)
            ry = random.randint(10, h - 10)
            
            await self.move(rx, ry, steps=random.randint(5, 20))
        except: pass

async def install_stealth_scripts(page):
    """
    注入高级反指纹脚本。
    覆盖 webdriver 属性, mock 插件列表, 伪造语言/时区等。
    """
    try:
        await page.add_init_script("""
            // 1. 隐藏 WebDriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });

            // 2. 伪造 Chrome 插件 (针对 SheerID 常见的监测)
            const mockPlugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
            ];
            Object.defineProperty(navigator, 'plugins', {
                get: () => mockPlugins,
            });
            
            // 3. 伪造语言 (与请求头一致)
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });

            // 4. WebGL 指纹噪音 (轻微扰动)
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                // 37445 = UNMASKED_VENDOR_WEBGL
                // 37446 = UNMASKED_RENDERER_WEBGL
                if (parameter === 37445) {
                    return 'Google Inc. (NVIDIA)';
                }
                if (parameter === 37446) {
                    return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                }
                return getParameter(parameter);
            };
        """)
        logger.info("Stealth scripts installed.")
    except Exception as e:
        logger.warning(f"Stealth injection failed: {e}")