"""
로컬 관리자 페이지 — mp3 업로드 → RSS 재생성 → git commit & push 를 한 번에.

실행:  python src/admin.py         (또는 프로젝트 루트의 admin.bat 더블클릭)
접속:  http://127.0.0.1:8080

127.0.0.1 에만 바인딩되므로 외부에서 접근할 수 없습니다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

from rss_generator import AUDIO_EXTS, FILENAME_RE, fmt_duration, generate, parse_episode

ROOT = Path(__file__).resolve().parent.parent
EPISODES_DIR = ROOT / "episodes"
CONFIG_PATH = ROOT / "config.yaml"
NOTEBOOK_MAP_PATH = ROOT / ".notebook-map.json"
NOTEBOOK_URL_TEMPLATE = "https://notebooklm.google.com/notebook/{id}"

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.secret_key = "notebooklm-podcast-admin-local"
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

JOB: dict = {
    "running": False,
    "log": [],
    "returncode": None,
    "started_at": None,
    "finished_at": None,
    "notebooks_found": None,
    "episodes_added": None,
    "session_expired": False,
}
JOB_LOCK = threading.Lock()
MAX_LOG_LINES = 500

LIST_JOB: dict = {
    "running": False,
    "log": [],
    "returncode": None,
    "started_at": None,
    "finished_at": None,
    "notebooks": [],
    "session_expired": False,
}
LIST_JOB_LOCK = threading.Lock()


_WIN_INVALID = re.compile(r'[\\/:*?"<>|]')
_DISCOVER_COUNT_RE = re.compile(r"\[discover\] 노트북 (\d+)개 발견")
_EPISODE_COUNT_RE = re.compile(r"\[done\] 새로 받은 에피소드: (\d+)개")
_SESSION_EXPIRED_MARKER = "[discover] 세션 만료"
_NOTEBOOK_LIST_RE = re.compile(r"^\s*•\s+(.+?)\s+\(([^)]+?)…?\)\s*$")


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


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    JOB["log"].append(f"[{ts}] {msg}")
    if len(JOB["log"]) > MAX_LOG_LINES:
        del JOB["log"][: len(JOB["log"]) - MAX_LOG_LINES]

    m = _DISCOVER_COUNT_RE.search(msg)
    if m:
        JOB["notebooks_found"] = int(m.group(1))
    m = _EPISODE_COUNT_RE.search(msg)
    if m:
        JOB["episodes_added"] = int(m.group(1))
    if _SESSION_EXPIRED_MARKER in msg:
        JOB["session_expired"] = True


def _list_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    LIST_JOB["log"].append(f"[{ts}] {msg}")
    if len(LIST_JOB["log"]) > MAX_LOG_LINES:
        del LIST_JOB["log"][: len(LIST_JOB["log"]) - MAX_LOG_LINES]

    m = _NOTEBOOK_LIST_RE.match(msg)
    if m:
        LIST_JOB["notebooks"].append({"name": m.group(1).strip(), "id": m.group(2).strip()})
    if _SESSION_EXPIRED_MARKER in msg:
        LIST_JOB["session_expired"] = True


def _run_list_notebooks() -> None:
    try:
        _list_log(f"subprocess: {sys.executable} src/downloader.py --list")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            [sys.executable, "src/downloader.py", "--config", "config.yaml", "--list"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _list_log(line)
        proc.wait()
        LIST_JOB["returncode"] = proc.returncode
        if proc.returncode != 0:
            _list_log(f"[FAIL] downloader.py --list exit code {proc.returncode}")
    except Exception as e:
        _list_log(f"[EXCEPTION] {type(e).__name__}: {e}")
        LIST_JOB["returncode"] = -1
    finally:
        LIST_JOB["finished_at"] = time.time()
        LIST_JOB["running"] = False


def _run_auto_download() -> None:
    try:
        _log("git pull --rebase --autostash origin main")
        rc, out = run_git("pull", "--rebase", "--autostash", "origin", "main")
        if out:
            _log(out)
        if rc != 0:
            _log("[FAIL] git pull 실패 — 중단")
            JOB["returncode"] = rc
            return

        _log(f"subprocess: {sys.executable} src/main.py")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            [sys.executable, "src/main.py", "--config", "config.yaml"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _log(line)
        proc.wait()
        if proc.returncode != 0:
            _log(f"[FAIL] main.py exit code {proc.returncode}")
            JOB["returncode"] = proc.returncode
            return

        _log(f"subprocess: {sys.executable} src/sync_with_notebooklm.py")
        proc = subprocess.Popen(
            [sys.executable, "src/sync_with_notebooklm.py", "--config", "config.yaml"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _log(line)
        proc.wait()
        if proc.returncode != 0:
            _log(f"[WARN] sync_with_notebooklm.py exit={proc.returncode} — 계속 진행")

        _log(f"subprocess: {sys.executable} src/main.py --skip-download (RSS 재생성)")
        proc = subprocess.Popen(
            [sys.executable, "src/main.py", "--config", "config.yaml", "--skip-download"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _log(line)
        proc.wait()
        if proc.returncode != 0:
            _log(f"[FAIL] RSS 재생성 exit={proc.returncode}")
            JOB["returncode"] = proc.returncode
            return

        _log("git add episodes/ docs/")
        rc, out = run_git("add", "episodes/", "docs/")
        if out:
            _log(out)
        if rc != 0:
            _log("[FAIL] git add 실패")
            JOB["returncode"] = rc
            return

        rc, _ = run_git("diff", "--cached", "--quiet")
        if rc == 0:
            _log("[DONE] 변경사항 없음 — 새 에피소드가 없거나 이미 동기화됨")
            JOB["returncode"] = 0
            return

        msg = f"chore: auto-download {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        _log(f"git commit -m \"{msg}\"")
        rc, out = run_git("commit", "-m", msg)
        if out:
            _log(out)
        if rc != 0:
            _log("[FAIL] git commit 실패")
            JOB["returncode"] = rc
            return

        _log("git push")
        rc, out = run_git("push")
        if out:
            _log(out)
        if rc != 0:
            _log("[FAIL] git push 실패")
            JOB["returncode"] = rc
            return

        _log("[DONE] 완료 — GitHub Actions가 Pages를 재배포합니다.")
        JOB["returncode"] = 0
    except Exception as e:
        _log(f"[EXCEPTION] {type(e).__name__}: {e}")
        JOB["returncode"] = -1
    finally:
        JOB["finished_at"] = time.time()
        JOB["running"] = False


def _load_notebook_map() -> dict:
    if not NOTEBOOK_MAP_PATH.exists():
        return {}
    try:
        return json.loads(NOTEBOOK_MAP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _notebook_url_for(filename: str, nb_map: dict) -> str | None:
    """파일명의 가운데 슬러그를 키로 .notebook-map.json 에서 ID 를 lookup."""
    m = FILENAME_RE.match(filename)
    if not m:
        return None
    info = nb_map.get(m.group(2))
    if not info or not info.get("id"):
        return None
    return NOTEBOOK_URL_TEMPLATE.format(id=info["id"])


@app.route("/", methods=["GET"])
def index():
    audio_files = []
    for ext in AUDIO_EXTS:
        audio_files.extend(EPISODES_DIR.glob(ext))
    episodes = sorted(
        (parse_episode(p) for p in audio_files),
        key=lambda e: e.pub_date,
        reverse=True,
    )
    nb_map = _load_notebook_map()
    notebook_urls = {
        ep.path.name: url
        for ep in episodes
        if (url := _notebook_url_for(ep.path.name, nb_map))
    }
    return render_template(
        "admin.html",
        today=date.today().strftime("%Y-%m-%d"),
        job=JOB,
        list_job=LIST_JOB,
        episodes=episodes,
        notebook_urls=notebook_urls,
        fmt_duration=fmt_duration,
    )


@app.route("/delete", methods=["POST"])
def delete():
    filename = request.form.get("filename", "")
    if "/" in filename or "\\" in filename or ".." in filename:
        flash(f"잘못된 파일명: {filename}", "error")
        return redirect(url_for("index"))
    if not filename.lower().endswith((".mp3", ".m4a")):
        flash("mp3/m4a 파일만 삭제 가능합니다.", "error")
        return redirect(url_for("index"))

    target = EPISODES_DIR / filename
    docs_target = ROOT / "docs" / "episodes" / filename

    if not target.exists():
        flash(f"파일이 존재하지 않습니다: {filename}", "error")
        return redirect(url_for("index"))

    rc, out = run_git("pull", "--rebase", "--autostash", "origin", "main")
    if rc != 0:
        flash(f"git pull --rebase 실패 (원격과 충돌 가능):\n{out}", "error")
        return redirect(url_for("index"))

    target.unlink()
    docs_target.unlink(missing_ok=True)

    try:
        generate(CONFIG_PATH)
    except Exception as e:
        flash(f"RSS 재생성 실패: {e}", "error")
        return redirect(url_for("index"))

    logs: list[str] = []
    for args in (
        ("add", "episodes/", "docs/"),
        ("commit", "-m", f"remove episode: {filename}"),
        ("push",),
    ):
        rc, out = run_git(*args)
        logs.append(f"$ git {' '.join(args)}\n{out}")
        if rc != 0:
            flash("git 명령 실패:\n\n" + "\n\n".join(logs), "error")
            return redirect(url_for("index"))

    flash(f"삭제 + 배포 완료: {filename}\n\n" + "\n\n".join(logs), "success")
    return redirect(url_for("index"))


@app.route("/delete-batch", methods=["POST"])
def delete_batch():
    filenames = request.form.getlist("filenames")
    if not filenames:
        flash("선택된 파일이 없습니다.", "error")
        return redirect(url_for("index"))

    valid: list[str] = []
    for fn in filenames:
        if "/" in fn or "\\" in fn or ".." in fn:
            flash(f"잘못된 파일명: {fn}", "error")
            return redirect(url_for("index"))
        if not fn.lower().endswith((".mp3", ".m4a")):
            flash(f"mp3/m4a 파일만 삭제 가능: {fn}", "error")
            return redirect(url_for("index"))
        if not (EPISODES_DIR / fn).exists():
            flash(f"파일이 없습니다: {fn}", "error")
            return redirect(url_for("index"))
        valid.append(fn)

    rc, out = run_git("pull", "--rebase", "--autostash", "origin", "main")
    if rc != 0:
        flash(f"git pull --rebase 실패:\n{out}", "error")
        return redirect(url_for("index"))

    for fn in valid:
        (EPISODES_DIR / fn).unlink(missing_ok=True)
        (ROOT / "docs" / "episodes" / fn).unlink(missing_ok=True)

    try:
        generate(CONFIG_PATH)
    except Exception as e:
        flash(f"RSS 재생성 실패: {e}", "error")
        return redirect(url_for("index"))

    msg = (
        f"remove episode: {valid[0]}"
        if len(valid) == 1
        else f"remove {len(valid)} episodes"
    )
    logs: list[str] = []
    for args in (
        ("add", "episodes/", "docs/"),
        ("commit", "-m", msg),
        ("push",),
    ):
        rc, out = run_git(*args)
        logs.append(f"$ git {' '.join(args)}\n{out}")
        if rc != 0:
            flash("git 명령 실패:\n\n" + "\n\n".join(logs), "error")
            return redirect(url_for("index"))

    flash(f"{len(valid)}개 삭제 + 배포 완료", "success")
    return redirect(url_for("index"))


@app.route("/rename", methods=["POST"])
def rename():
    old = request.form.get("old", "").strip()
    new_date = request.form.get("date", "").replace("-", "")
    new_notebook = sanitize(request.form.get("notebook", ""))
    new_title = sanitize(request.form.get("title", ""))

    if "/" in old or "\\" in old or ".." in old:
        flash(f"잘못된 파일명: {old}", "error")
        return redirect(url_for("index"))
    if not old.lower().endswith((".mp3", ".m4a")):
        flash("mp3/m4a 파일만 이름 변경 가능합니다.", "error")
        return redirect(url_for("index"))
    if not (new_date and new_notebook and new_title):
        flash("날짜·노트북·제목을 모두 채워주세요.", "error")
        return redirect(url_for("index"))
    if len(new_date) != 8 or not new_date.isdigit():
        flash(f"날짜 형식이 잘못됐습니다: {new_date}", "error")
        return redirect(url_for("index"))

    src = EPISODES_DIR / old
    if not src.exists():
        flash(f"파일이 없습니다: {old}", "error")
        return redirect(url_for("index"))

    ext = src.suffix.lower()
    new_name = f"{new_date}__{new_notebook}__{new_title}{ext}"
    if new_name == old:
        flash("변경사항 없음", "success")
        return redirect(url_for("index"))

    new_path = EPISODES_DIR / new_name
    if new_path.exists():
        flash(f"같은 이름의 파일이 이미 있습니다: {new_name}", "error")
        return redirect(url_for("index"))

    rc, out = run_git("pull", "--rebase", "--autostash", "origin", "main")
    if rc != 0:
        flash(f"git pull --rebase 실패:\n{out}", "error")
        return redirect(url_for("index"))

    src.rename(new_path)
    docs_old = ROOT / "docs" / "episodes" / old
    docs_new = ROOT / "docs" / "episodes" / new_name
    if docs_old.exists():
        docs_old.rename(docs_new)

    try:
        generate(CONFIG_PATH)
    except Exception as e:
        flash(f"RSS 재생성 실패: {e}", "error")
        return redirect(url_for("index"))

    logs: list[str] = []
    for args in (
        ("add", "episodes/", "docs/"),
        ("commit", "-m", f"rename episode: {old} -> {new_name}"),
        ("push",),
    ):
        rc, out = run_git(*args)
        logs.append(f"$ git {' '.join(args)}\n{out}")
        if rc != 0:
            flash("git 명령 실패:\n\n" + "\n\n".join(logs), "error")
            return redirect(url_for("index"))

    flash(f"이름 변경 + 배포 완료: {new_name}", "success")
    return redirect(url_for("index"))


@app.route("/auto-download", methods=["POST"])
def auto_download():
    with JOB_LOCK:
        if JOB["running"]:
            flash("이미 자동 다운로드가 실행 중입니다.", "error")
            return redirect(url_for("index"))
        JOB["running"] = True
        JOB["log"] = []
        JOB["returncode"] = None
        JOB["started_at"] = time.time()
        JOB["finished_at"] = None
        JOB["notebooks_found"] = None
        JOB["episodes_added"] = None
        JOB["session_expired"] = False

    threading.Thread(target=_run_auto_download, daemon=True).start()
    return redirect(url_for("index"))


@app.route("/list-notebooks", methods=["POST"])
def list_notebooks():
    with LIST_JOB_LOCK:
        if LIST_JOB["running"]:
            flash("이미 노트북 목록 조회가 실행 중입니다.", "error")
            return redirect(url_for("index"))
        LIST_JOB["running"] = True
        LIST_JOB["log"] = []
        LIST_JOB["returncode"] = None
        LIST_JOB["started_at"] = time.time()
        LIST_JOB["finished_at"] = None
        LIST_JOB["notebooks"] = []
        LIST_JOB["session_expired"] = False

    threading.Thread(target=_run_list_notebooks, daemon=True).start()
    return redirect(url_for("index"))


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

    if not file.filename.lower().endswith((".mp3", ".m4a")):
        flash("mp3/m4a 파일만 업로드 가능합니다.", "error")
        return redirect(url_for("index"))

    ext = Path(file.filename).suffix.lower()
    filename = f"{date_str}__{notebook}__{title}{ext}"
    target = EPISODES_DIR / filename
    EPISODES_DIR.mkdir(exist_ok=True)

    if target.exists():
        flash(f"같은 이름의 파일이 이미 있습니다: {filename}", "error")
        return redirect(url_for("index"))

    rc, out = run_git("pull", "--rebase", "--autostash", "origin", "main")
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


def _open_browser_when_ready() -> None:
    import socket
    import time
    for _ in range(40):
        try:
            with socket.create_connection(("127.0.0.1", 8080), timeout=0.2):
                webbrowser.open("http://127.0.0.1:8080")
                return
        except OSError:
            time.sleep(0.25)


if __name__ == "__main__":
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    app.run(host="127.0.0.1", port=8080, debug=False, use_reloader=False)
