"""
로컬 관리자 페이지 — mp3 업로드 → RSS 재생성 → git commit & push 를 한 번에.

실행:  python src/admin.py         (또는 프로젝트 루트의 admin.bat 더블클릭)
접속:  http://127.0.0.1:8080

127.0.0.1 에만 바인딩되므로 외부에서 접근할 수 없습니다.
"""
from __future__ import annotations

import re
import subprocess
import threading
import webbrowser
from datetime import date
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

from rss_generator import generate

ROOT = Path(__file__).resolve().parent.parent
EPISODES_DIR = ROOT / "episodes"
CONFIG_PATH = ROOT / "config.yaml"

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.secret_key = "notebooklm-podcast-admin-local"
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


_WIN_INVALID = re.compile(r'[\\/:*?"<>|]')


def sanitize(s: str) -> str:
    s = s.strip()
    s = _WIN_INVALID.sub("", s)
    s = s.replace("__", "_")
    s = re.sub(r"\s+", "-", s)
    return s


def run_git(*args: str) -> tuple[int, str]:
    r = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out.strip()


@app.route("/", methods=["GET"])
def index():
    return render_template("admin.html", today=date.today().strftime("%Y-%m-%d"))


@app.route("/upload", methods=["POST"])
def upload():
    date_str = request.form.get("date", "").replace("-", "")
    notebook = sanitize(request.form.get("notebook", ""))
    title = sanitize(request.form.get("title", ""))
    file = request.files.get("file")

    if not (date_str and notebook and title and file and file.filename):
        flash("모든 필드를 채우고 mp3 파일을 선택하세요.", "error")
        return redirect(url_for("index"))

    if len(date_str) != 8 or not date_str.isdigit():
        flash(f"날짜 형식이 잘못됐습니다: {date_str}", "error")
        return redirect(url_for("index"))

    if not file.filename.lower().endswith(".mp3"):
        flash("mp3 파일만 업로드 가능합니다.", "error")
        return redirect(url_for("index"))

    filename = f"{date_str}__{notebook}__{title}.mp3"
    target = EPISODES_DIR / filename
    EPISODES_DIR.mkdir(exist_ok=True)

    if target.exists():
        flash(f"같은 이름의 파일이 이미 있습니다: {filename}", "error")
        return redirect(url_for("index"))

    rc, out = run_git("pull", "--rebase", "origin", "main")
    if rc != 0:
        flash(f"git pull --rebase 실패 (원격과 충돌 가능):\n{out}", "error")
        return redirect(url_for("index"))

    file.save(str(target))

    try:
        generate(CONFIG_PATH)
    except Exception as e:
        target.unlink(missing_ok=True)
        flash(f"RSS 생성 실패 (파일 롤백됨): {e}", "error")
        return redirect(url_for("index"))

    logs: list[str] = []
    for args in (
        ("add", f"episodes/{filename}", "docs/"),
        ("commit", "-m", f"add episode: {filename}"),
        ("push",),
    ):
        rc, out = run_git(*args)
        logs.append(f"$ git {' '.join(args)}\n{out}")
        if rc != 0:
            flash("git 명령 실패:\n\n" + "\n\n".join(logs), "error")
            return redirect(url_for("index"))

    flash(
        f"업로드 + 배포 완료: {filename}\n\n" + "\n\n".join(logs),
        "success",
    )
    return redirect(url_for("index"))


def _open_browser() -> None:
    webbrowser.open("http://127.0.0.1:8080")


if __name__ == "__main__":
    threading.Timer(1.0, _open_browser).start()
    app.run(host="127.0.0.1", port=8080, debug=False, use_reloader=False)
