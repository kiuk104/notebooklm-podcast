"""
NotebookLM 음성개요(Audio Overview) 자동 다운로더.

- Playwright persistent context 로 Google 세션을 유지 (.auth/)
- auto_discover=true 이면 홈 페이지의 모든 내 노트북을 자동 수집
- 또는 config.yaml 에 개별 노트북 ID 지정 가능
- 이미 받은 파일은 스킵

UI 가 바뀌면 SELECTOR_* 상수만 갱신하면 됩니다.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from playwright.async_api import (
    BrowserContext, Download, Page,
    TimeoutError as PWTimeoutError, async_playwright,
)

SELECTOR_HOME_NOTEBOOK_LINK = 'a[href*="/notebook/"]'
# 노트북 페이지의 노트북 제목 (chat 패널 헤더의 cover-title)
SELECTOR_NOTEBOOK_TITLE = '.cover-title'
# audio 카드 안의 제목 텍스트
SELECTOR_ARTIFACT_TITLE = '.artifact-title'
# 노트북 페이지에서 이미 생성된 음성개요 카드의 재생 버튼 (audio 전용 액션)
SELECTOR_AUDIO_PLAY = 'button[aria-label="재생"], button[aria-label="Play"]'
# 같은 카드의 ⋮ (더보기) 메뉴 트리거 — audio/video/slides 모두 공통이므로 카드 ancestor로 한정해서 사용
SELECTOR_ARTIFACT_MORE = 'button.artifact-more-button'
# ⋮ 메뉴를 열었을 때 표시되는 "다운로드" 메뉴 항목
SELECTOR_DOWNLOAD_MENUITEM = (
    'button[mat-menu-item]:has-text("다운로드"), '
    '[role="menuitem"]:has-text("다운로드"), '
    'button[mat-menu-item]:has-text("Download"), '
    '[role="menuitem"]:has-text("Download")'
)

HOME_URL = "https://notebooklm.google.com/"
NOTEBOOK_URL_TEMPLATE = "https://notebooklm.google.com/notebook/{id}"
NOTEBOOK_ID_RE = re.compile(r"/notebook/([^/?#]+)")


@dataclass
class NotebookConfig:
    id: str
    name: str


@dataclass
class DownloaderConfig:
    auth_dir: Path
    episodes_dir: Path
    auto_discover: bool
    exclude_names: list
    notebooks: list


def load_config(path: Path) -> DownloaderConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    nb_raw = raw.get("notebooks", [])
    auto_discover = False
    exclude_names = []
    notebooks = []
    if isinstance(nb_raw, dict):
        auto_discover = bool(nb_raw.get("auto_discover", False))
        exclude_names = list(nb_raw.get("exclude", []))
    elif isinstance(nb_raw, list):
        notebooks = [NotebookConfig(id=n["id"], name=n["name"]) for n in nb_raw]
    return DownloaderConfig(
        auth_dir=Path(raw.get("auth_dir", ".auth")),
        episodes_dir=Path(raw.get("episodes_dir", "episodes")),
        auto_discover=auto_discover,
        exclude_names=exclude_names,
        notebooks=notebooks,
    )


def slugify(text: str) -> str:
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"[^0-9A-Za-z가-힣\-_]", "", text)
    return text[:60] or "episode"


async def open_context(auth_dir: Path, headless: bool):
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
    await page.goto(HOME_URL)
    print("[login] 로그인이 끝나면 이 터미널에서 Enter를 누르세요...")
    await asyncio.get_event_loop().run_in_executor(None, input)
    await ctx.close()
    await pw.stop()
    print(f"[login] 세션이 {auth_dir} 에 저장되었습니다.")


async def discover_notebooks(page: Page, exclude_names: list) -> list:
    print(f"[discover] 홈 페이지에서 노트북 목록을 수집합니다…")
    await page.goto(HOME_URL, wait_until="networkidle")

    final_url = page.url
    if "accounts.google.com" in final_url or "/signin" in final_url or "ServiceLogin" in final_url:
        print(
            f"[discover] 세션 만료 — 로그인 페이지로 리다이렉트됨 ({final_url}). "
            f"`python src/downloader.py --login`으로 다시 로그인하세요."
        )
        return []

    try:
        await page.wait_for_selector(SELECTOR_HOME_NOTEBOOK_LINK, timeout=8000)
    except PWTimeoutError:
        print(
            "[discover] 세션 만료 가능성 — 노트북 링크를 찾지 못했습니다. "
            "`python src/downloader.py --login`으로 다시 로그인하거나, NotebookLM 홈에 실제로 노트북이 있는지 확인하세요."
        )
        return []

    anchors = page.locator(SELECTOR_HOME_NOTEBOOK_LINK)
    count = await anchors.count()

    seen = set()
    discovered = []
    for i in range(count):
        a = anchors.nth(i)
        href = await a.get_attribute("href") or ""
        m = NOTEBOOK_ID_RE.search(href)
        if not m:
            continue
        nb_id = m.group(1)
        if nb_id in seen:
            continue
        seen.add(nb_id)
        name = ""
        aria = await a.get_attribute("aria-label")
        if aria and aria.strip():
            name = aria.strip().split("\n")[0]
        if not name:
            try:
                text = (await a.text_content(timeout=2000)) or ""
                name = text.strip().split("\n")[0]
            except PWTimeoutError:
                name = ""
        name = name or nb_id
        if any(ex.strip() and ex.strip() in name for ex in exclude_names):
            print(f"[discover] 제외: {name}")
            continue
        discovered.append(NotebookConfig(id=nb_id, name=name))

    print(f"[discover] 노트북 {len(discovered)}개 발견")
    for nb in discovered:
        print(f"           • {nb.name}  ({nb.id[:12]}…)")
    return discovered


async def download_audio_for_notebook(page: Page, notebook: NotebookConfig, episodes_dir: Path) -> list:
    saved = []
    url = NOTEBOOK_URL_TEMPLATE.format(id=notebook.id)
    print(f"[fetch] {notebook.name} → {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    except PWTimeoutError:
        print(f"[fetch] {notebook.name}: 페이지 로드 타임아웃, 스킵")
        return saved

    try:
        await page.wait_for_selector(SELECTOR_AUDIO_PLAY, timeout=12_000)
    except PWTimeoutError:
        print(f"[fetch] {notebook.name}: 음성개요 없음")
        return saved

    notebook_title = notebook.name
    try:
        cover = page.locator(SELECTOR_NOTEBOOK_TITLE).first
        text = ((await cover.text_content(timeout=3_000)) or "").strip()
        if text:
            notebook_title = text
    except PWTimeoutError:
        pass

    play_buttons = page.locator(SELECTOR_AUDIO_PLAY)
    count = await play_buttons.count()
    print(f"[fetch] {notebook_title}: 음성개요 {count}개 발견")

    for i in range(count):
        play = play_buttons.nth(i)
        card = play.locator(
            'xpath=ancestor::*[.//button[contains(@class, "artifact-more-button")]][1]'
        ).first

        episode_title = f"audio-{i}"
        try:
            t = ((await card.locator(SELECTOR_ARTIFACT_TITLE).first.text_content(timeout=2_000)) or "").strip()
            if t:
                episode_title = t
        except PWTimeoutError:
            pass

        more = card.locator('button.artifact-more-button').first
        try:
            await more.scroll_into_view_if_needed()
            await more.click(timeout=5_000)
        except PWTimeoutError:
            print(f"[fetch] {notebook_title} #{i}: 더보기(⋮) 버튼을 못 찾음, 스킵")
            continue

        try:
            async with page.expect_download(timeout=60_000) as dl_info:
                await page.locator(SELECTOR_DOWNLOAD_MENUITEM).first.click(timeout=5_000)
            download: Download = await dl_info.value
        except PWTimeoutError:
            print(f"[fetch] {notebook_title} #{i}: 다운로드 메뉴 항목 없음, 스킵")
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            continue

        suggested = download.suggested_filename or ""
        ext = Path(suggested).suffix.lower() if suggested else ".mp3"
        if ext not in (".mp3", ".m4a"):
            ext = ".mp3"
        suffix = f"__{slugify(notebook_title)}__{slugify(episode_title)}{ext}"

        existing = next(
            (
                p
                for p in episodes_dir.iterdir()
                if p.suffix.lower() in (".mp3", ".m4a") and p.name.endswith(suffix)
            ),
            None,
        )
        if existing is not None:
            print(f"[skip] 이미 존재 (날짜 무시): {existing.name}")
            continue

        target = episodes_dir / f"{date.today().strftime('%Y%m%d')}{suffix}"
        await download.save_as(str(target))
        saved.append(target)
        print(f"[saved] {target.name}")

    return saved


async def cmd_debug(auth_dir: Path, notebook_id: str, headless: bool = False) -> None:
    """
    한 노트북을 열어 HTML 전체와 스크린샷을 저장하고, audio/download 관련
    DOM 단서를 출력한다. 셀렉터 갱신용 디버그 명령.
    """
    out_dir = Path("debug")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[debug] 출력 폴더: {out_dir.resolve()}")
    print(f"[debug] headless={headless}")

    ctx, pw = await open_context(auth_dir, headless=headless)
    try:
        page = await ctx.new_page()
        url = NOTEBOOK_URL_TEMPLATE.format(id=notebook_id)
        print(f"[debug] {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except PWTimeoutError:
            print("[debug] domcontentloaded 타임아웃, 그래도 dump 시도")

        try:
            await page.wait_for_selector(SELECTOR_AUDIO_PLAY, timeout=12_000)
            print("[debug] 음성개요 카드 발견")
        except PWTimeoutError:
            print("[debug] 음성개요 재생 버튼을 못 찾음 — 음성개요가 없거나 셀렉터가 또 바뀜")
        await page.wait_for_timeout(2000)

        html = await page.content()
        (out_dir / "page.html").write_text(html, encoding="utf-8")
        print(f"[debug] HTML 저장: {out_dir / 'page.html'} ({len(html):,} bytes)")

        try:
            await page.screenshot(path=str(out_dir / "page.png"), full_page=True)
            print(f"[debug] 스크린샷 저장: {out_dir / 'page.png'}")
        except Exception as e:
            print(f"[debug] 스크린샷 실패: {e}")

        for label, expr in [
            ("data-testid 모음", "Array.from(new Set(Array.from(document.querySelectorAll('[data-testid]')).map(e => e.getAttribute('data-testid')))).filter(t => /audio|overview|studio|download/i.test(t))"),
            ("aria-label에 다운로드/audio 포함된 버튼", "Array.from(document.querySelectorAll('button[aria-label]')).map(b => b.getAttribute('aria-label')).filter(a => /다운로드|download|audio|음성/i.test(a))"),
            ("audio 관련 클래스", "Array.from(new Set(Array.from(document.querySelectorAll('[class]')).flatMap(e => e.className.toString().split(/\\s+/)))).filter(c => /audio|overview/i.test(c))"),
        ]:
            try:
                vals = await page.evaluate(expr)
                print(f"\n[debug] === {label} ({len(vals)}개) ===")
                for v in vals[:30]:
                    print(f"        {v}")
                if len(vals) > 30:
                    print(f"        … ({len(vals) - 30} more)")
            except Exception as e:
                print(f"[debug] {label} 평가 실패: {e}")
    finally:
        await ctx.close()
        await pw.stop()


async def cmd_run(config: DownloaderConfig) -> None:
    config.episodes_dir.mkdir(parents=True, exist_ok=True)
    ctx, pw = await open_context(config.auth_dir, headless=True)
    try:
        page = await ctx.new_page()
        if config.auto_discover:
            notebooks = await discover_notebooks(page, config.exclude_names)
        else:
            notebooks = config.notebooks
            if not notebooks:
                print("[error] config.yaml 에 노트북이 비어 있습니다. auto_discover: true 로 바꾸거나 수동으로 추가하세요.")
                return
        all_saved = []
        for nb in notebooks:
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
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--login", action="store_true", help="첫 실행 시 Google 로그인")
    parser.add_argument("--list", action="store_true", help="발견된 노트북만 출력 (다운로드 안 함)")
    parser.add_argument("--debug", metavar="NOTEBOOK_ID", help="해당 노트북의 HTML/스크린샷을 debug/ 에 저장하고 audio 관련 셀렉터 단서 출력")
    parser.add_argument("--headless", action="store_true", help="--debug 와 함께 사용 시 헤드리스 모드로 실행")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if args.login:
        auth_dir = Path(".auth")
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                auth_dir = Path(raw.get("auth_dir", ".auth"))
        asyncio.run(cmd_login(auth_dir))
        return

    if not cfg_path.exists():
        print(f"[error] {cfg_path} 가 없습니다.")
        sys.exit(1)
    config = load_config(cfg_path)

    if args.list:
        async def _list():
            ctx, pw = await open_context(config.auth_dir, headless=True)
            try:
                page = await ctx.new_page()
                await discover_notebooks(page, config.exclude_names)
            finally:
                await ctx.close()
                await pw.stop()
        asyncio.run(_list())
        return

    if args.debug:
        asyncio.run(cmd_debug(config.auth_dir, args.debug, headless=args.headless))
        return

    asyncio.run(cmd_run(config))


if __name__ == "__main__":
    main()
