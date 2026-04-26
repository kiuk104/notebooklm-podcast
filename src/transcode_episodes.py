"""기존 episodes/ 폴더의 m4a를 모두 mp3로 일괄 재인코딩.

사용:
  python src/transcode_episodes.py            # 64k mono mp3로 변환, 원본 m4a 삭제
  python src/transcode_episodes.py --bitrate 96k
  python src/transcode_episodes.py --keep-original
  python src/transcode_episodes.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audio_tools import find_bin, transcode_to_mp3


def main() -> None:
    parser = argparse.ArgumentParser(description="episodes/ 안 m4a를 mp3로 일괄 변환")
    parser.add_argument("--episodes-dir", default="episodes")
    parser.add_argument("--bitrate", default="64k", help="기본: 64k (음성용 mono)")
    parser.add_argument("--keep-original", action="store_true", help="변환 후 m4a 보존")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not find_bin("ffmpeg"):
        print(
            "[error] ffmpeg를 찾을 수 없습니다. "
            "Windows: winget install Gyan.FFmpeg",
            file=sys.stderr,
        )
        sys.exit(1)

    episodes_dir = Path(args.episodes_dir)
    if not episodes_dir.is_dir():
        print(f"[error] {episodes_dir} 폴더가 없습니다", file=sys.stderr)
        sys.exit(1)

    m4as = sorted(episodes_dir.glob("*.m4a"))
    print(f"[transcode] 대상 {len(m4as)}개 (bitrate={args.bitrate}, mono, dry-run={args.dry_run})")
    if not m4as:
        return

    converted = 0
    skipped = 0
    failed = 0
    saved_bytes = 0

    for i, src in enumerate(m4as, 1):
        dst = src.with_suffix(".mp3")
        prefix = f"[{i}/{len(m4as)}]"
        if dst.exists():
            print(f"{prefix} [skip] {dst.name} 이미 존재")
            skipped += 1
            continue
        old_size = src.stat().st_size
        if args.dry_run:
            print(f"{prefix} [dry] {src.name} → {dst.name} ({old_size//1024//1024}MB)")
            continue
        ok = transcode_to_mp3(src, dst, bitrate=args.bitrate)
        if not ok:
            print(f"{prefix} [fail] {src.name}")
            failed += 1
            continue
        new_size = dst.stat().st_size
        saved_bytes += (old_size - new_size)
        if not args.keep_original:
            src.unlink()
        converted += 1
        print(
            f"{prefix} [ok]   {dst.name}  "
            f"({old_size//1024//1024}MB → {new_size//1024//1024}MB)"
        )

    print()
    print(
        f"[done] 변환={converted} 스킵={skipped} 실패={failed} "
        f"절감={saved_bytes//1024//1024}MB"
    )


if __name__ == "__main__":
    main()
