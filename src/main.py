"""
다운로더 → RSS 생성을 한 번에 실행.
GitHub Actions나 cron에서 이 파일만 호출하면 된다.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from downloader import cmd_run, load_config
from rss_generator import generate


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument(
        "--skip-download",
        action="store_true",
        help="다운로드 스킵하고 RSS만 다시 생성 (디버그용)",
    )
    args = p.parse_args()
    cfg_path = Path(args.config)

    if not args.skip_download:
        config = load_config(cfg_path)
        asyncio.run(cmd_run(config))

    generate(cfg_path)


if __name__ == "__main__":
    main()
