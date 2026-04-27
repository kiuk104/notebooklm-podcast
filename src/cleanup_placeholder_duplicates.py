"""
episodes/ 폴더에서 'audio-N' 플레이스홀더 제목으로 받은 중복 파일을 정리한다.

같은 {YYYYMMDD}__{notebook}__ prefix 로 audio-N 이 아닌 실제 제목의 sibling 이
존재하면 audio-N 파일을 중복으로 보고 삭제한다. sibling 이 없으면 유일본이므로
보존한다.

기본은 dry-run. 실제 삭제는 --apply 플래그.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPISODES_DIR = ROOT / "episodes"
FILENAME_RE = re.compile(r"^(\d{8})__(.+?)__(.+)\.(mp3|m4a)$", re.IGNORECASE)
PLACEHOLDER_TITLE_RE = re.compile(r"^audio[\s\-_]?\d+$", re.IGNORECASE)


def main() -> None:
    parser = argparse.ArgumentParser(description="audio-N 플레이스홀더 중복 청소")
    parser.add_argument("--apply", action="store_true", help="실제로 파일을 삭제 (없으면 dry-run)")
    args = parser.parse_args()

    if not EPISODES_DIR.exists():
        print(f"[error] {EPISODES_DIR} 가 없습니다.")
        sys.exit(1)

    groups: dict[tuple[str, str], list[tuple[Path, str]]] = defaultdict(list)
    for p in sorted(EPISODES_DIR.iterdir()):
        if not p.is_file():
            continue
        m = FILENAME_RE.match(p.name)
        if not m:
            continue
        date, notebook, title = m.group(1), m.group(2), m.group(3)
        groups[(date, notebook)].append((p, title))

    to_delete: list[Path] = []
    keep_lonely: list[Path] = []
    ambiguous: list[Path] = []
    for (_date, _notebook), entries in groups.items():
        placeholders = [(p, t) for p, t in entries if PLACEHOLDER_TITLE_RE.match(t)]
        real = [(p, t) for p, t in entries if not PLACEHOLDER_TITLE_RE.match(t)]
        if not placeholders:
            continue
        if not real:
            for p, _t in placeholders:
                keep_lonely.append(p)
            continue
        if len(placeholders) > len(real):
            for p, _t in placeholders:
                ambiguous.append(p)
            continue
        for p, _t in placeholders:
            to_delete.append(p)

    if to_delete:
        print(f"[plan] 삭제 대상 {len(to_delete)}개:")
        for p in to_delete:
            print(f"  - {p.name}")
    else:
        print("[plan] 삭제 대상 없음.")

    if ambiguous:
        print(f"\n[warn] 플레이스홀더 수가 실제-제목 sibling 보다 많아 어떤 게 중복인지 불명확 (보존):")
        for p in ambiguous:
            print(f"  - {p.name}")

    if keep_lonely:
        print(f"\n[info] sibling 없는 audio-N (보존) {len(keep_lonely)}개")

    if not to_delete:
        return

    if not args.apply:
        print(f"\n[dry-run] 실제 삭제하려면 `python src/cleanup_placeholder_duplicates.py --apply`")
        return

    deleted = 0
    for p in to_delete:
        try:
            p.unlink()
            deleted += 1
        except OSError as e:
            print(f"[error] {p.name}: {e}")
    print(f"\n[done] {deleted}/{len(to_delete)}개 삭제 완료.")


if __name__ == "__main__":
    main()
