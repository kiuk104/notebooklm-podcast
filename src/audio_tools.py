"""ffmpeg/ffprobe 외부 바이너리 탐색과 미디어 변환 헬퍼.

shutil.which 우선, Windows에서는 winget 표준 설치 경로도 fallback으로 탐색해
PATH가 갱신되지 않은 셸에서도 동작하게 한다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_BIN_CACHE: dict[str, str] = {}


def find_bin(name: str) -> str | None:
    """ffmpeg/ffprobe 등의 절대 경로를 찾는다. 못 찾으면 None."""
    if name in _BIN_CACHE:
        return _BIN_CACHE[name] or None
    found = shutil.which(name) or ""
    if not found and os.name == "nt":
        winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        for path in winget_root.glob(f"Gyan.FFmpeg_*/ffmpeg-*/bin/{name}.exe"):
            found = str(path)
            break
    _BIN_CACHE[name] = found
    return found or None


def transcode_to_mp3(
    src: Path,
    dst: Path,
    bitrate: str = "64k",
    mono: bool = True,
    timeout: int = 300,
) -> bool:
    """src 오디오를 mp3 (libmp3lame)로 인코딩해 dst에 저장. 성공이면 True."""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        return False
    args = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(src),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", bitrate,
    ]
    if mono:
        args += ["-ac", "1"]
    args.append(str(dst))
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
