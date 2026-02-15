from __future__ import annotations

import asyncio
import re
import sys

from dotenv import load_dotenv

from .sheerid_verifier import SheerIDVerifier


async def _amain(argv: list[str] | None = None) -> int:
    # 从项目根目录的 .env 读取环境变量（如 ONE_SHEERID_DRY_RUN 等）
    load_dotenv(override=False)

    argv = list(sys.argv[1:] if argv is None else argv)
    url = argv[0] if len(argv) > 0 else input("请输入 SheerID URL: ").strip()
    email = argv[1] if len(argv) > 1 else input("请输入邮箱: ").strip()

    vid_match = re.search(r"verificationId=([^&#]+)", url)
    if not vid_match:
        print("无效 URL")
        return 2

    async with SheerIDVerifier(vid_match.group(1)) as verifier:
        result = await verifier.verify(email=email)
        print(f"结果: {result['message']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return int(asyncio.run(_amain(argv)))


if __name__ == "__main__":
    raise SystemExit(main())