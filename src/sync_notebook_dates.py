"""episodes/ 파일들의 날짜 부분을 NotebookLM 노트북의 cover 생성일로 일괄 변경.

흐름:
1. NotebookLM 홈에서 모든 노트북 ID 수집
2. 각 노트북 페이지 방문 → .cover-title (제목) + .cover-subtitle-date의
   title 속성 (정확한 ISO datetime) 추출
3. episodes/ 안 파일들의 노트북 부분과 매칭 → 파일명 날짜만 rename
4. docs/episodes/ 도 같이 rename

사용:
  python src/sync_notebook_dates.py --dry-run
  python src/sync_notebook_dates.py
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import yaml
from playwright.async_api import TimeoutError as PWTimeoutError

from downloader import (
    HOME_URL,
    NOTEBOOK_ID_RE,
    NOTEBOOK_URL_TEMPLATE,
    SELECTOR_COVER_DATE,
    SELECTOR_HOME_NOTEBOOK_LINK,
    SELECTOR_NOTEBOOK_TITLE,
    open_context,
    parse_cover_date,
    slugify,
)

FILENAME_RE = re.compile(r"^(\d{8})__(.+?)__(.+)\.(?:mp3|m4a)$", re.IGNORECASE)


async def collect_notebook_dates(auth_dir: Path) -> list[tuple[str, datetime]]:
    """[(notebook title, generation datetime), ...] 반환."""
    ctx, pw = await open_context(auth_dir, headless=True)
    out: list[tuple[str, datetime]] = []
    try:
        page = await ctx.new_page()
        print(f"[home] {HOME_URL}")
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20_000)
        try:
            await page.wait_for_selector(SELECTOR_HOME_NOTEBOOK_LINK, timeout=10_000)
        except PWTimeoutError:
            print("[error] 홈에서 노트북 링크를 못 찾음. 세션 만료일 수 있음.")
            return out

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
        print(f"[home] {len(ids)}개 노트북 발견")

        for i, nid in enumerate(ids, 1):
            url = NOTEBOOK_URL_TEMPLATE.format(id=nid)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except PWTimeoutError:
                print(f"[{i}/{len(ids)}] {nid[:8]}…  page load timeout, skip")
                continue
            try:
                await page.wait_for_selector(SELECTOR_COVER_DATE, timeout=10_000)
            except PWTimeoutError:
                print(f"[{i}/{len(ids)}] {nid[:8]}…  cover-subtitle-date 없음")
                continue

            title = ""
            try:
                t = await page.locator(SELECTOR_NOTEBOOK_TITLE).first.text_content(timeout=2_000)
                if t:
                    title = t.strip()
            except PWTimeoutError:
                pass

            date_attr = ""
            try:
                date_attr = (
                    await page.locator(SELECTOR_COVER_DATE).first.get_attribute("title")
                ) or ""
            except PWTimeoutError:
                pass

            dt = parse_cover_date(date_attr)
            if dt and title:
                out.append((title, dt))
                print(f"[{i}/{len(ids)}] {title[:50]:50} {dt:%Y-%m-%d}")
            else:
                print(f"[{i}/{len(ids)}] {nid[:8]}…  parse fail: title={title!r}, date={date_attr!r}")
    finally:
        await ctx.close()
        await pw.stop()
    return out


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

    nb_info = asyncio.run(collect_notebook_dates(auth_dir))
    if not nb_info:
        print("[error] 노트북 정보를 모으지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    # 파일명에 들어가는 키는 slugified 노트북 이름이므로 동일 변환을 거쳐야 매칭됨.
    # 동명 노트북이 여럿이면 가장 오래된 날짜를 사용.
    name_to_date: dict[str, datetime] = {}
    for title, dt in nb_info:
        key = slugify(title)
        if key not in name_to_date or name_to_date[key] > dt:
            name_to_date[key] = dt
    print(f"\n[mapping] {len(name_to_date)}개 슬러그 → 날짜")

    eps = Path(args.episodes_dir)
    docs_eps = Path(args.docs_episodes_dir)
    files = sorted(eps.glob("*.mp3")) + sorted(eps.glob("*.m4a"))

    plan: list[tuple[str, str, datetime]] = []
    no_match: list[str] = []
    already_correct: list[str] = []

    for f in files:
        m = FILENAME_RE.match(f.name)
        if not m:
            no_match.append(f.name)
            continue
        old_date, nb_slug, title_slug = m.group(1), m.group(2), m.group(3)
        if nb_slug not in name_to_date:
            no_match.append(f.name)
            continue
        new_dt = name_to_date[nb_slug]
        new_date = new_dt.strftime("%Y%m%d")
        if new_date == old_date:
            already_correct.append(f.name)
            continue
        new_name = f"{new_date}__{nb_slug}__{title_slug}{f.suffix}"
        plan.append((f.name, new_name, new_dt))

    print()
    print(f"[summary] 변경 예정 {len(plan)}, 이미 정확 {len(already_correct)}, 매칭 실패 {len(no_match)}")
    if no_match:
        print("\n[no-match] 샘플:")
        for n in no_match[:15]:
            print(f"  {n}")
        if len(no_match) > 15:
            print(f"  ... ({len(no_match) - 15} more)")

    if plan:
        print("\n[plan] 샘플:")
        for old, new, dt in plan[:15]:
            print(f"  - {old}")
            print(f"  → {new}  ({dt:%Y-%m-%d})")
        if len(plan) > 15:
            print(f"  ... ({len(plan) - 15} more)")

    if args.dry_run:
        print("\n[dry-run] 실제 변경 안 함")
        return
    if not plan:
        print("[done] 변경할 게 없습니다")
        return

    renamed, skipped = 0, 0
    for old, new, _ in plan:
        src = eps / old
        dst = eps / new
        if dst.exists():
            print(f"[skip] target 이미 존재: {new}")
            skipped += 1
            continue
        src.rename(dst)
        d_old, d_new = docs_eps / old, docs_eps / new
        if d_old.exists() and not d_new.exists():
            d_old.rename(d_new)
        renamed += 1
    print(f"\n[done] rename={renamed} skipped={skipped}")
    print("       이제 'python src/main.py --skip-download' 으로 RSS 재생성하고 git push 하세요.")


if __name__ == "__main__":
    main()
