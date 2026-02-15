"""本地代理网关：把浏览器的请求转发到 ProxyPool 选出的上游代理（HTTP 代理）。

用途：让浏览器只配置一个本地代理地址（本服务），而本服务内部自动在多个上游代理之间轮换。

说明：
- 支持 HTTP 代理的两种常见请求：
  1) 普通 HTTP 请求（带绝对 URL 的 request-target）
  2) HTTPS 隧道（CONNECT host:port）
- 上游代理要求是“HTTP 代理”（例如 http://user:pass@ip:port）。
  SOCKS5 上游不在此网关实现范围内。

环境变量：
- ONE_PROXIES / ONE_PROXIES_FILE: 代理池来源（见 one/proxy_pool.py）
- ONE_GATEWAY_HOST: 监听地址（默认 127.0.0.1）
- ONE_GATEWAY_PORT: 监听端口（默认 18080）

运行：
  python -m one.proxy_gateway
然后把浏览器代理指向：
  HTTP/HTTPS 代理 = 127.0.0.1:18080
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlsplit


# 兼容两种运行方式：
# 1) 作为包运行：python -m one.proxy_gateway
# 2) 直接运行脚本：python one/proxy_gateway.py
if __package__:
    from .proxy_pool import ProxyPool, ProxyState
else:  # pragma: no cover
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(base_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from one.proxy_pool import ProxyPool, ProxyState


@dataclass
class UpstreamProxy:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def auth_header(self) -> str:
        if not self.username and not self.password:
            return ""
        token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        return f"Proxy-Authorization: Basic {token}\r\n"


def _parse_upstream(url: str) -> UpstreamProxy:
    s = urlsplit(url)
    if s.scheme not in ("http", "https"):
        raise ValueError(f"上游代理仅支持 http/https: {url}")
    if not s.hostname or not s.port:
        raise ValueError(f"上游代理缺少 host/port: {url}")
    return UpstreamProxy(
        scheme=s.scheme,
        host=s.hostname,
        port=int(s.port),
        username=s.username or "",
        password=s.password or "",
    )


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _read_headers(reader: asyncio.StreamReader) -> Tuple[bytes, bytes]:
    """读到 \r\n\r\n，返回 (head, rest)。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = await reader.read(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 1024 * 1024:
            break
    if b"\r\n\r\n" in buf:
        head, rest = buf.split(b"\r\n\r\n", 1)
        return head + b"\r\n\r\n", rest
    return buf, b""


def _parse_request_line(head: bytes) -> Tuple[str, str, str]:
    line = head.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
    parts = line.split(" ")
    if len(parts) < 3:
        raise ValueError("bad request line")
    return parts[0], parts[1], parts[2]


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, pool: ProxyPool) -> None:
    peer = writer.get_extra_info("peername")
    proxy_state: Optional[ProxyState] = None

    try:
        head, rest = await _read_headers(reader)
        if not head:
            writer.close()
            await writer.wait_closed()
            return

        method, target, version = _parse_request_line(head)

        # 选上游代理
        proxy_state = pool.acquire()
        upstream = _parse_upstream(proxy_state.url)

        up_reader, up_writer = await asyncio.open_connection(upstream.host, upstream.port)

        if method.upper() == "CONNECT":
            # CONNECT host:port
            connect_req = (
                f"CONNECT {target} {version}\r\n"
                f"Host: {target}\r\n"
                f"Proxy-Connection: keep-alive\r\n"
                f"Connection: keep-alive\r\n"
                f"{upstream.auth_header}"
                f"\r\n"
            ).encode("latin-1")
            up_writer.write(connect_req)
            await up_writer.drain()

            # 读上游响应头
            up_head, up_rest = await _read_headers(up_reader)
            if not up_head:
                raise RuntimeError("upstream closed")

            status_line = up_head.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
            if " 200 " not in status_line:
                # 直接把上游响应回给客户端
                writer.write(up_head + up_rest)
                await writer.drain()
                raise RuntimeError(f"upstream CONNECT failed: {status_line}")

            # 告诉客户端隧道建立成功
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            # 隧道：客户端<->上游（上游此时已经连到目标站）
            # 注意：up_rest 理论上应为空；若不为空也直接转发。
            if up_rest:
                writer.write(up_rest)
                await writer.drain()

            t1 = asyncio.create_task(_pipe(reader, up_writer))
            t2 = asyncio.create_task(_pipe(up_reader, writer))
            await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)

        else:
            # 普通 HTTP 代理请求：浏览器通常发送绝对 URL 作为 target
            # 这里直接把原始头（去掉 Proxy-Authorization 交给上游）转发给上游。
            # 为简单起见，不做头部重写/压缩处理。
            up_writer.write(head)
            if rest:
                up_writer.write(rest)
            await up_writer.drain()

            t1 = asyncio.create_task(_pipe(reader, up_writer))
            t2 = asyncio.create_task(_pipe(up_reader, writer))
            await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)

        # 若走到这里，视为成功
        pool.report_success(proxy_state)

    except Exception as e:
        if proxy_state is not None:
            pool.report_failure(proxy_state, str(e))
        try:
            # 兜底返回 502
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 11\r\n\r\nBad Gateway")
            await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def amain() -> int:
    pool = ProxyPool.from_env()
    host = (os.getenv("ONE_GATEWAY_HOST", "127.0.0.1") or "127.0.0.1").strip()
    port = int((os.getenv("ONE_GATEWAY_PORT", "18080") or "18080").strip() or 18080)

    server = await asyncio.start_server(lambda r, w: handle_client(r, w, pool), host, port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    print(f"proxy-gateway listening on {addrs}")

    async with server:
        await server.serve_forever()
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())