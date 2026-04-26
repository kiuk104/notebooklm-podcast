"""NotebookLM의 현재 audio overview 목록을 source-of-truth로 보고
episodes/ 에서 사라진 audio (NotebookLM 측에서 삭제된 것)를 prune.

흐름:
1. 모든 노트북 방문 → (노트북 제목, audio 카드 제목들) 수집
2. {slugify(노트북): {slugify(audio), ...}} 셋 작성
3. episodes/ 파일 스캔
4. 파일의 (노트북-슬러그, audio-슬러그)가 위 셋에 없으면 삭제 후보
5. 단, 노트북 자체가 NotebookLM에서 안 잡힌 경우 *보호* — 일시적 미발견 가능

사용:
  python src/sync_with_notebooklm.py --dry-run
  python src/sync_with_notebooklm.py
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import yaml
from playwright.async_api import TimeoutError as PWTimeoutError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from downloader import (
    HOME_URL,
    NOTEBOOK_ID_RE,
    NOTEBOOK_URL_TEMPLATE,
    SELECTOR_ARTIFACT_TITLE,
    SELECTOR_AUDIO_PLAY,
    SELECTOR_HOME_NOTEBOOK_LINK,
    SELECTOR_NOTEBOOK_TITLE,
    open_context,
    slugify,
)

FILENAME_RE = re.compile(r"^(\d{8})__(.+?)__(.+)\.(?:mp3|m4a)$", re.IGNORECASE)


async def collect_notebooklm_state(auth_dir: Path) -> dict[str, set[str]]:
    """{notebook-slug: {audio-slug, ...}} 매핑."""
    ctx, pw = await open_context(auth_dir, headless=True)
    nb_map: dict[str, set[str]] = {}
    try:
        page = await ctx.new_page()
        print(f"[home] {HOME_URL}")
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20_000)
        try:
            await page.wait_for_selector(SELECTOR_HOME_NOTEBOOK_LINK, timeout=10_000)
        except PWTimeoutError:
            print("[error] 홈에서 노트북을 못 찾음. 세션 만료일 수 있음.")
            return nb_map

        anchors = page.locator(SELECTOR_HOME_NOTEBOOK_LINK)
        count = await anchors.count()
        ids: list[str] = []
        seen: set[str] = set()
        for i in range(count):
            href = await anchors.nth(i).get_attribute("href") or ""
            m = NOTEBOOK_ID_RE.search(href)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                ids.append(m.group(1))
        print(f"[home] {len(ids)}개 노트북")

        for i, nid in enumerate(ids, 1):
            url = NOTEBOOK_URL_TEMPLATE.format(id=nid)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except PWTimeoutError:
                print(f"[{i}/{len(ids)}] {nid[:8]}…  page load timeout, skip")
                continue

            title = ""
            try:
                await page.wait_for_selector(SELECTOR_NOTEBOOK_TITLE, timeout=5_000)
                t = await page.locator(SELECTOR_NOTEBOOK_TITLE).first.text_content(timeout=2_000)
                if t:
                    title = t.strip()
            except PWTimeoutError:
                pass
            if not title:
                print(f"[{i}/{len(ids)}] {nid[:8]}…  title 추출 실패, skip")
                continue

            nb_slug = slugify(title)
            audio_slugs: set[str] = set()
            try:
                await page.wait_for_selector(SELECTOR_AUDIO_PLAY, timeout=5_000)
                play_buttons = page.locator(SELECTOR_AUDIO_PLAY)
                cnt = await play_buttons.count()
                for j in range(cnt):
                    play = play_buttons.nth(j)
                    card = play.locator(
                        'xpath=ancestor::*[.//button[contains(@class, "artifact-more-button")]][1]'
                    ).first
                    try:
                        t = await card.locator(SELECTOR_ARTIFACT_TITLE).first.text_content(timeout=2_000)
                        if t:
                            audio_slugs.add(slugify(t.strip()))
                    except PWTimeoutError:
                        pass
            except PWTimeoutError:
                pass

            nb_map.setdefault(nb_slug, set()).update(audio_slugs)
            print(f"[{i}/{len(ids)}] {title[:50]:50} audio={len(audio_slugs)}")
    finally:
        await ctx.close()
        await pw.stop()
    return nb_map


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--episodes-dir", default="episodes")
    parser.add_argument("--docs-episodes-dir", default="docs/episodes")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    auth_dir = Path(cfg.get("auth_dir", ".auth"))

    nb_map = asyncio.run(collect_notebooklm_state(auth_dir))
    if not nb_map:
        print("[error] NotebookLM 상태 수집 실패", file=sys.stderr)
        sys.exit(1)

    total_audios = sum(len(v) for v in nb_map.values())
    print(f"\n[state] {len(nb_map)}개 슬러그, audio overview {total_audios}개")

    eps = Path(args.episodes_dir)
    docs_eps = Path(args.docs_episodes_dir)
    files = sorted(eps.glob("*.mp3")) + sorted(eps.glob("*.m4a"))

    to_delete: list[Path] = []
    protected_unknown_nb: list[str] = []
    bad_format: list[str] = []
    keep = 0

    for f in files:
        m = FILENAME_RE.match(f.name)
        if not m:
            bad_format.append(f.name)
            continue
        nb_slug, audio_slug = m.group(2), m.group(3)
        if nb_slug not in nb_map:
            protected_unknown_nb.append(f.name)
            continue
        if audio_slug not in nb_map[nb_slug]:
            to_delete.append(f)
        else:
            keep += 1

    print(
        f"\n[summary] 유지 {keep}, 삭제 후보 {len(to_delete)}, "
        f"보호(노트북 미발견) {len(protected_unknown_nb)}, "
        f"파일명 형식 오류 {len(bad_format)}"
    )

    if to_delete:
        print("\n[delete-plan]")
        for p in to_delete[:30]:
            print(f"  - {p.name}")
        if len(to_delete) > 30:
            print(f"  ... ({len(to_delete) - 30} more)")

    if protected_unknown_nb:
        print(f"\n[protected] 샘플 (앞 10개):")
        for n in protected_unknown_nb[:10]:
            print(f"  - {n}")

    if args.dry_run:
        print("\n[dry-run] 실제 삭제 안 함")
        return
    if not to_delete:
        print("[done] 삭제할 게 없습니다")
        return

    deleted = 0
    for p in to_delete:
        p.unlink(missing_ok=True)
        (docs_eps / p.name).unlink(missing_ok=True)
        deleted += 1
    print(f"\n[done] {deleted}개 삭제 완료")
    print("       'python src/main.py --skip-download' 으로 RSS 재생성하세요.")


if __name__ == "__main__":
    main()
