"""
NotebookLM 음성개요(Audio Overview) 자동 다운로더.

전략:
- Playwright의 'persistent context' 사용 → .auth/ 폴더에 쿠키/스토리지 저장
- 첫 실행은 --login 모드로 사용자가 직접 Google 로그인
- 이후 실행은 headless로 노트북 페이지에 들어가 mp3를 다운로드

⚠️ NotebookLM은 공식 API가 없어서 DOM 셀렉터에 의존합니다.
   UI가 바뀌면 SELECTOR_* 상수만 갱신하면 됩니다.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from playwright.async_api import (
    BrowserContext,
    Download,
    Page,
    TimeoutError as PWTimeoutError,
    async_playwright,
)


# ── 셀렉터 (UI 변경 시 여기만 수정) ────────────────────────────
SELECTOR_AUDIO_TAB = 'button:has-text("음성 개요"), button:has-text("Audio Overview")'
SELECTOR_DOWNLOAD_BUTTON = (
    'button[aria-label*="다운로드"], button[aria-label*="Download"], '
    'button:has-text("다운로드"), button:has-text("Download")'
)
# 음성개요 카드(여러 개일 수 있음)
SELECTOR_AUDIO_CARD = '[data-testid*="audio"], [class*="audio-overview"]'

NOTEBOOK_URL_TEMPLATE = "https://notebooklm.google.com/notebook/{id}"


@dataclass
class NotebookConfig:
    id: str
    name: str


@dataclass
class DownloaderConfig:
    auth_dir: Path
    episodes_dir: Path
    notebooks: list[NotebookConfig]


def load_config(path: Path) -> DownloaderConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return DownloaderConfig(
        auth_dir=Path(raw.get("auth_dir", ".auth")),
        episodes_dir=Path(raw.get("episodes_dir", "episodes")),
        notebooks=[NotebookConfig(id=n["id"], name=n["name"]) for n in raw["notebooks"]],
    )


def slugify(text: str) -> str:
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"[^0-9A-Za-z가-힣\-_]", "", text)
    return text[:60] or "episode"


async def open_context(auth_dir: Path, headless: bool) -> tuple[BrowserContext, "object"]:
    """Playwright persistent context를 연다."""
    auth_dir.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(auth_dir),
        headless=headless,
        accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return ctx, pw


async def cmd_login(auth_dir: Path) -> None:
    print("[login] 브라우저가 열리면 Google 로그인 후 NotebookLM이 보이면 창을 닫으세요.")
    ctx, pw = await open_context(auth_dir, headless=False)
    page = await ctx.new_page()
    await page.goto("https://notebooklm.google.com/")
    print("[login] 로그인이 끝나면 이 터미널에서 Enter를 누르세요...")
    # 사용자 입력은 이벤트 루프 밖에서:
    await asyncio.get_event_loop().run_in_executor(None, input)
    await ctx.close()
    await pw.stop()
    print(f"[login] 세션이 {auth_dir} 에 저장되었습니다.")


async def download_audio_for_notebook(
    page: Page, notebook: NotebookConfig, episodes_dir: Path
) -> list[Path]:
    """한 노트북의 모든 음성개요를 다운로드. 이미 받은 파일은 스킵."""
    saved: list[Path] = []
    url = NOTEBOOK_URL_TEMPLATE.format(id=notebook.id)
    print(f"[fetch] {notebook.name} → {url}")
    await page.goto(url, wait_until="networkidle")

    # 음성개요 탭이 분리되어 있는 UI라면 클릭
    try:
        await page.locator(SELECTOR_AUDIO_TAB).first.click(timeout=3000)
    except PWTimeoutError:
        pass  # 탭이 없으면 그냥 진행

    cards = page.locator(SELECTOR_AUDIO_CARD)
    count = await cards.count()
    if count == 0:
        print(f"[fetch] {notebook.name}: 음성개요가 없습니다.")
        return saved

    for i in range(count):
        card = cards.nth(i)
        # 카드 안에서 다운로드 버튼 찾기
        try:
            dl_button = card.locator(SELECTOR_DOWNLOAD_BUTTON).first
            await dl_button.scroll_into_view_if_needed()
            async with page.expect_download(timeout=60_000) as dl_info:
                await dl_button.click()
            download: Download = await dl_info.value
        except PWTimeoutError:
            print(f"[fetch] {notebook.name} #{i}: 다운로드 버튼을 못 찾음, 스킵")
            continue

        suggested = download.suggested_filename or f"{notebook.name}-{i}.mp3"
        # YYYYMMDD-노트북-제목.mp3 형식으로 저장 (RSS에서 파싱)
        from datetime import date

        stem = Path(suggested).stem
        date_prefix = date.today().strftime("%Y%m%d")
        target_name = f"{date_prefix}__{slugify(notebook.name)}__{slugify(stem)}.mp3"
        target = episodes_dir / target_name

        if target.exists():
            print(f"[skip] 이미 존재: {target.name}")
            continue

        await download.save_as(str(target))
        saved.append(target)
        print(f"[saved] {target.name}")

    return saved


async def cmd_run(config: DownloaderConfig) -> None:
    config.episodes_dir.mkdir(parents=True, exist_ok=True)
    ctx, pw = await open_context(config.auth_dir, headless=True)
    try:
        page = await ctx.new_page()
        all_saved: list[Path] = []
        for nb in config.notebooks:
            try:
                saved = await download_audio_for_notebook(page, nb, config.episodes_dir)
                all_saved.extend(saved)
            except Exception as e:
                print(f"[error] {nb.name}: {e}", file=sys.stderr)
        print(f"\n[done] 새로 받은 에피소드: {len(all_saved)}개")
    finally:
        await ctx.close()
        await pw.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="NotebookLM 음성개요 다운로더")
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로")
    parser.add_argument(
        "--login",
        action="store_true",
        help="첫 실행 시 Google 로그인 (헤드풀 브라우저)",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if args.login:
        # 로그인은 config의 auth_dir만 알면 됨
        auth_dir = Path(".auth")
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                auth_dir = Path(raw.get("auth_dir", ".auth"))
        asyncio.run(cmd_login(auth_dir))
        return

    if not cfg_path.exists():
        print(f"[error] {cfg_path} 가 없습니다. config.example.yaml을 복사해서 만드세요.")
        sys.exit(1)
    config = load_config(cfg_path)
    asyncio.run(cmd_run(config))


if __name__ == "__main__":
    main()
