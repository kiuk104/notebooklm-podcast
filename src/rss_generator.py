"""
episodes/ 폴더의 mp3/m4a들을 스캔해서 podcast-spec 호환 RSS 2.0 피드를 생성한다.
파일명 규칙: <YYYYMMDD>__<노트북명>__<제목>.<mp3|m4a>
"""
from __future__ import annotations
import html
import mimetypes
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

import yaml
import mutagen

from audio_tools import find_bin

FILENAME_RE = re.compile(r"^(\d{8})__(.+?)__(.+)\.(?:mp3|m4a)$", re.IGNORECASE)
AUDIO_EXTS = ("*.mp3", "*.m4a")


def _long_path(p: Path) -> str:
    """Windows MAX_PATH (260) 한계를 회피하는 \\?\ prefix 자동 적용.
    한국어/긴 노트북 제목 + 긴 audio 제목 조합으로 mp3 절대 경로가
    250자를 넘기는 케이스에서 mutagen/ffprobe/shutil 등이 fail."""
    s = str(p)
    if os.name == "nt":
        try:
            s = str(p.resolve())
        except OSError:
            s = str(p.absolute())
        if len(s) >= 240 and not s.startswith("\\\\?\\"):
            s = "\\\\?\\" + s
    return s


def _ffprobe_duration(path: Path) -> int:
    bin_path = find_bin("ffprobe")
    if not bin_path:
        return 0
    try:
        r = subprocess.run(
            [bin_path, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", _long_path(path)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(float(r.stdout.strip()))
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, OSError):
        pass
    return 0


@dataclass
class Episode:
    path: Path
    title: str
    notebook: str
    pub_date: datetime
    duration_sec: int
    size_bytes: int

    @property
    def guid(self) -> str:
        return self.path.name

    @property
    def url(self) -> str:
        return f"episodes/{quote(self.path.name)}"


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return os.stat(_long_path(path))
    except OSError:
        return None


def parse_episode(path: Path) -> Episode:
    m = FILENAME_RE.match(path.name)
    st = _safe_stat(path)
    if m:
        date_str, notebook, title = m.group(1), m.group(2), m.group(3)
        pub = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    else:
        notebook = "기타"
        title = path.stem
        mt = st.st_mtime if st else 0
        pub = datetime.fromtimestamp(mt, tz=timezone.utc)
    duration = 0
    try:
        meta = mutagen.File(_long_path(path))
        if meta and meta.info and meta.info.length and meta.info.length > 0:
            duration = int(meta.info.length)
    except Exception:
        pass
    if duration == 0:
        duration = _ffprobe_duration(path)
    return Episode(
        path=path,
        title=title.replace("-", " "),
        notebook=notebook.replace("-", " "),
        pub_date=pub,
        duration_sec=duration,
        size_bytes=st.st_size if st else 0,
    )


def fmt_duration(sec: int) -> str:
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def build_rss(config: dict, episodes: list[Episode]) -> str:
    pod = config["podcast"]
    base_url = pod["base_url"].rstrip("/")
    cover = pod.get("cover_image", "cover.jpg")
    cover_url = cover if cover.startswith("http") else f"{base_url}/{cover}"
    now = format_datetime(datetime.now(timezone.utc))

    items = []
    for ep in sorted(episodes, key=lambda e: e.pub_date, reverse=True):
        ep_url = f"{base_url}/{ep.url}"
        mime = mimetypes.guess_type(ep.path.name)[0] or "audio/mpeg"
        item_title = escape(f"[{ep.notebook}] {ep.title}")
        items.append(
            "    <item>\n"
            f"      <title>{item_title}</title>\n"
            f"      <description><![CDATA[NotebookLM 음성개요 — 노트북: {html.escape(ep.notebook)}]]></description>\n"
            f"      <pubDate>{format_datetime(ep.pub_date)}</pubDate>\n"
            f'      <enclosure url="{escape(ep_url)}" length="{ep.size_bytes}" type="{mime}"/>\n'
            f'      <guid isPermaLink="false">{escape(ep.guid)}</guid>\n'
            f"      <itunes:duration>{fmt_duration(ep.duration_sec)}</itunes:duration>\n"
            "      <itunes:explicit>false</itunes:explicit>\n"
            "    </item>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"\n'
        '     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"\n'
        '     xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{escape(pod['title'])}</title>\n"
        f"    <link>{escape(base_url)}</link>\n"
        f'    <atom:link href="{escape(base_url)}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        f"    <language>{pod.get('language', 'ko')}</language>\n"
        f"    <description>{escape(pod['description'])}</description>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        f"    <itunes:author>{escape(pod['author'])}</itunes:author>\n"
        f"    <itunes:summary>{escape(pod['description'])}</itunes:summary>\n"
        "    <itunes:owner>\n"
        f"      <itunes:name>{escape(pod['author'])}</itunes:name>\n"
        f"      <itunes:email>{escape(pod.get('email', 'noreply@example.com'))}</itunes:email>\n"
        "    </itunes:owner>\n"
        f'    <itunes:image href="{escape(cover_url)}"/>\n'
        f'    <itunes:category text="{escape(pod.get("category", "Education"))}"/>\n'
        "    <itunes:explicit>false</itunes:explicit>\n"
        + "\n".join(items) + "\n"
        "  </channel>\n"
        "</rss>\n"
    )


def build_index_html(config: dict, episodes: list[Episode]) -> str:
    pod = config["podcast"]
    title = html.escape(pod["title"])
    base_url = pod["base_url"].rstrip("/")
    rows = []
    for ep in sorted(episodes, key=lambda e: e.pub_date, reverse=True):
        rows.append(
            "<tr>"
            f"<td>{ep.pub_date.strftime('%Y-%m-%d')}</td>"
            f"<td>{html.escape(ep.notebook)}</td>"
            f'<td><a href="{html.escape(ep.url)}">{html.escape(ep.title)}</a></td>'
            f"<td>{fmt_duration(ep.duration_sec)}</td>"
            f"<td>{ep.size_bytes // 1024 // 1024} MB</td>"
            "</tr>"
        )
    rows_html = "\n".join(rows) or "<tr><td colspan='5'>아직 에피소드가 없습니다.</td></tr>"
    return (
        "<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">\n"
        f"<title>{title}</title>\n"
        "<style>body{font-family:-apple-system,system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;color:#222}"
        ".feed-url{background:#f4f4f4;padding:8px 12px;border-radius:6px;font-family:monospace;word-break:break-all}"
        "table{width:100%;border-collapse:collapse;margin-top:24px}"
        "th,td{padding:8px 6px;border-bottom:1px solid #eee;text-align:left;font-size:14px}"
        "th{background:#fafafa}</style></head><body>\n"
        f"<h1>{title}</h1>\n<p>아래 RSS 주소를 팟캐스트 앱에 등록하세요:</p>\n"
        f'<p class="feed-url">{base_url}/feed.xml</p>\n'
        "<table><thead><tr><th>날짜</th><th>노트북</th><th>제목</th><th>길이</th><th>크기</th></tr></thead>\n"
        f"<tbody>\n{rows_html}\n</tbody></table>\n</body></html>\n"
    )


def generate(config_path: Path) -> None:
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    episodes_dir = Path(config.get("episodes_dir", "episodes"))
    public_dir = Path(config.get("public_dir", "docs"))
    public_episodes = public_dir / "episodes"
    public_episodes.mkdir(parents=True, exist_ok=True)

    audio_files = []
    for ext in AUDIO_EXTS:
        audio_files.extend(episodes_dir.glob(ext))
    episodes = [parse_episode(p) for p in sorted(audio_files)]
    for ep in episodes:
        target = public_episodes / ep.path.name
        try:
            target_size = target.stat().st_size if target.exists() else -1
        except OSError:
            target_size = -1
        try:
            src_size = ep.path.stat().st_size
        except OSError:
            src_size = ep.size_bytes  # parse_episode 가 이미 잡아둠
        if target_size != src_size:
            shutil.copy2(_long_path(ep.path), _long_path(target))

    # episodes/ 에서 사라진 mp3/m4a 가 docs/episodes/ 에 남아 artifact 를
    # 부풀리지 않도록 청소. feed.xml 미참조 = 누구도 듣지 않는 데이터.
    keep = {ep.path.name for ep in episodes}
    removed = 0
    for ext in AUDIO_EXTS:
        for f in public_episodes.glob(ext):
            if f.name not in keep:
                try:
                    os.remove(_long_path(f))
                    removed += 1
                except OSError:
                    pass
    if removed:
        print(f"[rss] docs/episodes/ 고아 {removed}개 정리")

    (public_dir / "feed.xml").write_text(build_rss(config, episodes), encoding="utf-8")
    (public_dir / "index.html").write_text(build_index_html(config, episodes), encoding="utf-8")
    print(f"[rss] {len(episodes)}개 에피소드로 feed.xml 생성 완료")
    print(f"[rss] {public_dir / 'feed.xml'}")
    print(f"[rss] {public_dir / 'index.html'}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    args = p.parse_args()
    generate(Path(args.config))
