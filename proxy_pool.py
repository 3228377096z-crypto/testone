"""简单代理池示例（HTTP/HTTPS）。

用途：为合法的外部 HTTP 请求提供“轮换代理 + 健康检查 + 冷却时间”。
注意：代理池并不能绕过网站条款/风控；请确保你有权使用目标服务且遵守其规则。

依赖：httpx

环境变量：
- ONE_PROXIES: 逗号分隔的代理 URL 列表，例如：
  http://user:pass@1.2.3.4:8080, http://5.6.7.8:3128
- ONE_PROXIES_FILE: 代理列表文件路径（可选；每行一个代理；支持空行与以 # 开头的注释）
- ONE_PROXY_TEST_URL: 健康检查 URL（默认 https://httpbin.org/ip）
- ONE_PROXY_TEST_TIMEOUT_SEC: 健康检查超时（默认 8）
"""

from __future__ import annotations

import os
import time
import random
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx


@dataclass
class ProxyState:
    url: str
    ok: bool = True
    fail_count: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""


class ProxyPool:
    """线程安全的简单代理池（round-robin + 失败冷却）。"""

    def __init__(
        self,
        proxies: List[str],
        *,
        cooldown_sec: float = 30.0,
        max_fail: int = 2,
    ) -> None:
        self._lock = threading.Lock()
        self._idx = 0
        self._cooldown_sec = float(cooldown_sec)
        self._max_fail = int(max_fail)
        self._items: List[ProxyState] = [ProxyState(url=p.strip()) for p in proxies if p.strip()]
        if not self._items:
            raise ValueError("ProxyPool 需要至少 1 个代理")

    @staticmethod
    def _load_proxies_file(path: str) -> List[str]:
        try:
            p = (path or "").strip()
            if not p:
                return []
            with open(p, "r", encoding="utf-8") as f:
                out: List[str] = []
                for line in f:
                    s = (line or "").strip()
                    if not s or s.startswith("#"):
                        continue
                    out.append(s)
                return out
        except Exception:
            # 读取失败时不阻断主流程（让 ONE_PROXIES 仍可用）
            return []

    @staticmethod
    def from_env() -> "ProxyPool":
        raw = (os.getenv("ONE_PROXIES", "") or "").strip()
        raw_file = (os.getenv("ONE_PROXIES_FILE", "") or "").strip()

        proxies: List[str] = []
        if raw:
            proxies.extend([p.strip() for p in raw.split(",") if p.strip()])
        if raw_file:
            proxies.extend(ProxyPool._load_proxies_file(raw_file))

        # 去重并保持顺序
        seen = set()
        uniq: List[str] = []
        for p in proxies:
            if p in seen:
                continue
            seen.add(p)
            uniq.append(p)

        if not uniq:
            raise ValueError("未设置 ONE_PROXIES 且 ONE_PROXIES_FILE 为空/不可读（需要至少 1 个代理）")

        cooldown = float((os.getenv("ONE_PROXY_COOLDOWN_SEC", "30") or "30").strip() or 30)
        max_fail = int((os.getenv("ONE_PROXY_MAX_FAIL", "2") or "2").strip() or 2)
        return ProxyPool(uniq, cooldown_sec=cooldown, max_fail=max_fail)

    def _now(self) -> float:
        return time.time()

    def _is_available(self, it: ProxyState, now: float) -> bool:
        return now >= float(it.cooldown_until)

    def acquire(self) -> ProxyState:
        """拿一个当前可用代理（都在冷却时，返回最早解封的那个）。"""
        with self._lock:
            now = self._now()
            n = len(self._items)
            best: Optional[Tuple[float, ProxyState]] = None

            for _ in range(n):
                it = self._items[self._idx]
                self._idx = (self._idx + 1) % n
                if self._is_available(it, now):
                    return it
                # 记录最早解封的
                t = float(it.cooldown_until)
                if best is None or t < best[0]:
                    best = (t, it)

            # 全部冷却：返回最早解封的（调用方可选择 sleep 一下再用）
            assert best is not None
            return best[1]

    def report_success(self, proxy: ProxyState) -> None:
        with self._lock:
            proxy.ok = True
            proxy.fail_count = 0
            proxy.last_error = ""
            proxy.cooldown_until = 0.0

    def report_failure(self, proxy: ProxyState, err: str = "") -> None:
        with self._lock:
            proxy.ok = False
            proxy.fail_count += 1
            proxy.last_error = (err or "").strip()[:300]
            if proxy.fail_count >= self._max_fail:
                proxy.cooldown_until = self._now() + self._cooldown_sec

    def as_httpx_proxies(self, proxy: ProxyState) -> Dict[str, str]:
        """返回 httpx.Client(..., proxies=...) 需要的格式。"""
        return {"http://": proxy.url, "https://": proxy.url}

    def snapshot(self) -> List[ProxyState]:
        with self._lock:
            return [ProxyState(**it.__dict__) for it in self._items]


def check_proxy_once(proxy_url: str) -> Tuple[bool, str]:
    """对单个代理做一次健康检查（同步）。"""
    test_url = (os.getenv("ONE_PROXY_TEST_URL", "https://httpbin.org/ip") or "").strip()
    timeout_sec = float((os.getenv("ONE_PROXY_TEST_TIMEOUT_SEC", "8") or "8").strip() or 8)

    try:
        with httpx.Client(
            timeout=timeout_sec,
            proxies={"http://": proxy_url, "https://": proxy_url},
            headers={"User-Agent": "one-proxy-pool/1.0 (httpx)"},
        ) as c:
            r = c.get(test_url)
            if 200 <= r.status_code < 300:
                return True, ""
            return False, f"bad status {r.status_code}"
    except Exception as e:
        return False, str(e)


def demo_rotate_requests(url: str, total: int = 10) -> None:
    """演示：轮换代理请求一个 URL。"""
    pool = ProxyPool.from_env()

    for i in range(total):
        ps = pool.acquire()
        # 若全在冷却期，可选择等到解封
        wait = max(0.0, float(ps.cooldown_until) - time.time())
        if wait > 0:
            time.sleep(min(wait, 2.0))

        try:
            with httpx.Client(timeout=20.0, proxies=pool.as_httpx_proxies(ps)) as c:
                r = c.get(url)
            if 200 <= r.status_code < 300:
                pool.report_success(ps)
                print(f"[{i+1}/{total}] OK via {ps.url} status={r.status_code}")
            else:
                pool.report_failure(ps, f"status={r.status_code}")
                print(f"[{i+1}/{total}] FAIL via {ps.url} status={r.status_code}")

        except Exception as e:
            pool.report_failure(ps, str(e))
            print(f"[{i+1}/{total}] ERROR via {ps.url}: {e}")

        # 做一点随机间隔（可选）
        time.sleep(0.2 + random.uniform(0.0, 0.5))


if __name__ == "__main__":
    # 示例：
    #   export ONE_PROXIES='http://1.2.3.4:8080,http://5.6.7.8:3128'
    #   python -m one.proxy_pool
    demo_rotate_requests("https://httpbin.org/get", total=5)