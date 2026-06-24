# -*- coding: utf-8 -*-
"""
라이브 아카이버 - 로컬 웹 서버 (표준 라이브러리만 사용)

YouTube 라이브 링크를 붙여넣으면 yt-dlp로 실시간 아카이빙합니다.
- yt-dlp / ffmpeg 바이너리 자동 다운로드
- --live-from-start 로 라이브를 처음부터 녹화
- 예약 방송 자동 대기(--wait-for-video)
- 여러 방송 동시 녹화 + 실시간 진행률/로그
- 그레이스풀 정지(정지해도 지금까지 받은 분량을 합쳐 저장)

실행:  python server.py
"""

import os
import re
import sys
import json
import time
import uuid
import glob
import shutil
import signal
import zipfile
import platform
import threading
import subprocess
import webbrowser
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# 플랫폼 감지
# ---------------------------------------------------------------------------
SYS = platform.system()            # Windows / Darwin / Linux
MACHINE = platform.machine().lower()  # amd64 / x86_64 / arm64 / aarch64 ...
IS_WIN = SYS == "Windows"
IS_MAC = SYS == "Darwin"
IS_LINUX = SYS == "Linux"
EXE = ".exe" if IS_WIN else ""
IS_ARM = MACHINE in ("arm64", "aarch64", "armv8", "armv8l")

# ---------------------------------------------------------------------------
# 경로/상수
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
STATIC_DIR = os.path.join(BASE_DIR, "static")
REC_DIR = os.path.join(BASE_DIR, "recordings")

# bin/ 에 받을 파일 경로 (다운로드 대상)
YTDLP_DL = os.path.join(BIN_DIR, "yt-dlp" + EXE)
FFMPEG_DL = os.path.join(BIN_DIR, "ffmpeg" + EXE)


def _ytdlp_url():
    base = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
    if IS_WIN:
        return base + "yt-dlp.exe"
    if IS_MAC:
        return base + "yt-dlp_macos"          # universal (intel + apple silicon)
    if IS_ARM:
        return base + "yt-dlp_linux_aarch64"
    return base + "yt-dlp_linux"


def _ffmpeg_spec():
    """(다운로드 URL, 압축 내부에서 찾을 파일 suffix) 반환."""
    if IS_WIN:
        return ("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
                "/bin/ffmpeg.exe")
    mr = "https://ffmpeg.martin-riedl.de/redirect/latest/"
    if IS_MAC:
        # macOS는 amd64 빌드만 제공 (Apple Silicon은 Rosetta로 실행)
        return (mr + "macos/amd64/release/ffmpeg.zip", "ffmpeg")
    arch = "arm64" if IS_ARM else "amd64"
    return (mr + "linux/%s/release/ffmpeg.zip" % arch, "ffmpeg")


def ytdlp_path():
    """bin/ 에 받은 것 우선, 없으면 시스템 PATH(brew/apt 등)에서 검색."""
    if os.path.exists(YTDLP_DL):
        return YTDLP_DL
    return shutil.which("yt-dlp")


def ffmpeg_path():
    if os.path.exists(FFMPEG_DL):
        return FFMPEG_DL
    return shutil.which("ffmpeg")


HOST = "127.0.0.1"
PORT = 8731

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROG_TAG = "@@P@@"

# 화질 → yt-dlp 포맷 셀렉터
QMAP = {
    "best": "bv*+ba/b",
    "1080p60": "bv*[height<=1080]+ba/b[height<=1080]",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
    "720p60": "bv*[height<=720]+ba/b[height<=720]",
    "720p": "bv*[height<=720]+ba/b[height<=720]",
    "480p": "bv*[height<=480]+ba/b[height<=480]",
    "360p": "bv*[height<=360]+ba/b[height<=360]",
    "audio_only": "ba/b",
}

for d in (BIN_DIR, STATIC_DIR, REC_DIR):
    os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------------------
# 바이너리 자동 설치
# ---------------------------------------------------------------------------
setup_state = {"running": False, "percent": 0, "message": "",
               "error": None, "done": False}
_setup_lock = threading.Lock()


def _download(url, dest, label, base_pct, span_pct):
    req = urllib.request.Request(url, headers={"User-Agent": "live-archiver"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        read = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                frac = (read / total) if total else 0
                setup_state["percent"] = int(base_pct + span_pct * frac)
                setup_state["message"] = "%s 다운로드 중... %.1f / %.1f MB" % (
                    label, read / 1048576, (total / 1048576) if total else 0)


def _extract_member(zip_path, suffix, dest):
    with zipfile.ZipFile(zip_path) as z:
        target = None
        for name in z.namelist():
            if name.replace("\\", "/").lower().endswith(suffix):
                target = name
                break
        if not target:
            raise RuntimeError("압축 안에서 %s 를 찾지 못했습니다" % suffix)
        with z.open(target) as src, open(dest, "wb") as out:
            out.write(src.read())


def _make_executable(path):
    if not IS_WIN:
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass


def _do_setup():
    try:
        if ytdlp_path() is None:
            setup_state["message"] = "yt-dlp 다운로드 준비..."
            _download(_ytdlp_url(), YTDLP_DL, "yt-dlp", 0, 25)
            _make_executable(YTDLP_DL)

        if ffmpeg_path() is None:
            url, suffix = _ffmpeg_spec()
            tmp_ff = os.path.join(BIN_DIR, "_ffmpeg.zip")
            setup_state["message"] = "ffmpeg 다운로드 준비..."
            _download(url, tmp_ff, "ffmpeg", 25, 70)
            setup_state["message"] = "ffmpeg 압축 해제 중..."
            _extract_member(tmp_ff, suffix, FFMPEG_DL)
            _make_executable(FFMPEG_DL)
            os.remove(tmp_ff)

        setup_state["percent"] = 100
        setup_state["message"] = "설치 완료"
        setup_state["done"] = True
    except Exception as e:  # noqa
        setup_state["error"] = str(e)
        setup_state["message"] = "설치 실패: %s" % e
    finally:
        setup_state["running"] = False


def start_setup():
    with _setup_lock:
        if setup_state["running"]:
            return
        if binaries_ready():
            setup_state.update(percent=100, done=True, message="이미 설치됨")
            return
        setup_state.update(running=True, percent=0, error=None,
                           done=False, message="시작 중...")
        threading.Thread(target=_do_setup, daemon=True).start()


def binaries_ready():
    return ytdlp_path() is not None and ffmpeg_path() is not None


# ---------------------------------------------------------------------------
# 작업(녹화) 관리
# ---------------------------------------------------------------------------
class Job:
    def __init__(self, opts):
        self.id = uuid.uuid4().hex[:8]
        self.url = opts.get("url", "").strip()
        self.quality = opts.get("quality", "best")
        self.opts = opts
        self.status = "starting"  # starting/waiting/recording/muxing/finished/error/stopped
        self.progress = ""
        self.title = ""
        self.created = time.time()
        self.started_recording = None
        self.log = deque(maxlen=600)
        self.proc = None
        self.final_file = ""
        self.returncode = None
        self._stopping = False
        self._prefixes = set()  # 출력 파일 접두사(조각 복구용)

    def to_dict(self):
        return {
            "id": self.id, "url": self.url, "quality": self.quality,
            "status": self.status, "progress": self.progress, "title": self.title,
            "created": self.created, "started_recording": self.started_recording,
            "final_file": self.final_file, "returncode": self.returncode,
        }


jobs = {}
jobs_order = []
_jobs_lock = threading.Lock()


def _build_command(job):
    o = job.opts
    fmt = QMAP.get(o.get("quality", "best"), "bv*+ba/b")
    is_audio = o.get("quality") == "audio_only"

    ff = ffmpeg_path()
    ff_loc = os.path.dirname(ff) if ff else BIN_DIR
    cmd = [ytdlp_path(),
           "--live-from-start",
           "--encoding", "utf-8",
           "--no-colors",
           "--newline",
           "--ffmpeg-location", ff_loc,
           "--retries", "infinite",
           "--fragment-retries", "infinite",
           "-N", str(int(o.get("threads", 4) or 4)),
           "-f", fmt,
           "--progress-template",
           "download:" + PROG_TAG + "%(progress._downloaded_bytes_str)s @ "
           "%(progress._speed_str)s  [%(info.format_id)s]"]

    if o.get("wait", True):
        cmd += ["--wait-for-video", str(int(o.get("retry", 30) or 30))]
    else:
        cmd += ["--no-wait-for-video"]

    if not is_audio:
        cmd += ["--merge-output-format", "mp4"]
    if o.get("thumbnail", True):
        cmd += ["--embed-thumbnail"]
    if o.get("metadata", True):
        cmd += ["--embed-metadata"]

    browser = (o.get("browser") or "").strip()
    if browser and browser != "none":
        cmd += ["--cookies-from-browser", browser]
    cookies = (o.get("cookies") or "").strip()
    if cookies:
        cmd += ["--cookies", cookies]

    tmpl = (o.get("output") or "%(upload_date)s_%(title)s [%(id)s].%(ext)s").strip()
    cmd += ["-o", tmpl]
    cmd += ["--", job.url]   # '--' 로 옵션 파싱 종료 → URL이 옵션으로 오인되지 않게
    return cmd


def _classify(job, line):
    if not line:
        return
    # 진행률 라인 (동시 다운로드 시 "1: @@P@@..." 처럼 접두사가 붙을 수 있음)
    idx = line.find(PROG_TAG)
    if idx != -1:
        job.progress = "녹화 중 · " + line[idx + len(PROG_TAG):].strip()
        if job.status in ("starting", "waiting"):
            job.status = "recording"
        if job.started_recording is None:
            job.started_recording = time.time()
        return  # 진행률은 로그에 쌓지 않음

    job.log.append(line)
    low = line.lower()
    if "[youtube]" in low and "downloading" in low and not job.title:
        pass
    if low.startswith("[info]") and "downloading 1 video" in low:
        pass
    # 출력 대상 파일에서 접두사/제목 추출 (조각 복구에 사용)
    m = re.search(r"\[download\] destination:\s*(.+)", line, re.I)
    if m:
        dest = m.group(1).strip()
        pm = re.search(r"^(.*?)\.f\d+\.", dest)
        if pm:
            job._prefixes.add(pm.group(1))
            if not job.title:
                job.title = os.path.basename(pm.group(1))

    if ("waiting for video" in low or "this live event will begin" in low
            or "premieres in" in low or "will begin in" in low
            or "the live event will begin" in low or "is not yet" in low):
        if job.status == "starting":
            job.status = "waiting"
        job.progress = line.strip()
    elif ("[merger]" in low or "merging formats" in low or "[fixupm" in low
          or "[ffmpeg]" in low or "post-process" in low
          or "[fixup" in low or "deleting original" in low):
        job.status = "muxing"
        job.progress = "병합 중..."
    elif low.startswith("[download]") and "destination" in low:
        if job.status in ("starting", "waiting"):
            job.status = "recording"

    m2 = re.search(r'merging formats into "(.+)"', line, re.I)
    if m2:
        job.final_file = os.path.basename(m2.group(1))
    m3 = re.search(r"\[download\]\s+(.+?)\s+has already been downloaded", line, re.I)
    if m3:
        job.final_file = os.path.basename(m3.group(1))


def _video_id(url):
    m = re.search(r"(?:v=|/live/|youtu\.be/|/shorts/|/embed/|/watch/)"
                  r"([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def _finalize_fragments(job):
    """정지로 중단된 경우, 디스크에 남은 조각(.part-FragN)을 이어붙여 mp4로 복구.
    stdout 인코딩에 의존하지 않고 실제 파일명을 영상 ID로 매칭한다."""
    produced = []
    vid = _video_id(job.url)
    try:
        files = os.listdir(REC_DIR)
    except OSError:
        return produced

    # prefix -> {fmt: {"ext":.., "frags":[(n,fn)], "part":path|None}}
    # 라이브: 개별 조각(.part-FragN) / VOD: .part 에 바로 누적 — 둘 다 처리
    fragpat = re.compile(r"^(.*)\.f(\d+)\.(\w+)\.part-Frag(\d+)$")
    partpat = re.compile(r"^(.*)\.f(\d+)\.(\w+)\.part$")
    groups = {}
    for f in files:
        mm = fragpat.match(f)
        if mm:
            prefix, fmt, ext, n = mm.group(1), mm.group(2), mm.group(3), int(mm.group(4))
            if vid and vid not in prefix:
                continue
            g = groups.setdefault(prefix, {}).setdefault(
                fmt, {"ext": ext, "frags": [], "part": None})
            g["frags"].append((n, f))
            continue
        pm = partpat.match(f)
        if pm:
            prefix, fmt, ext = pm.group(1), pm.group(2), pm.group(3)
            if vid and vid not in prefix:
                continue
            g = groups.setdefault(prefix, {}).setdefault(
                fmt, {"ext": ext, "frags": [], "part": None})
            g["part"] = os.path.join(REC_DIR, f)

    for prefix, fmts in groups.items():
        fmt_files = []
        for fmt, g in fmts.items():
            ext, frags, part = g["ext"], g["frags"], g["part"]
            out = os.path.join(REC_DIR, "%s.f%s.%s" % (prefix, fmt, ext))
            try:
                if frags:                      # 라이브: 조각 이어붙이기
                    frags.sort()
                    with open(out, "wb") as w:
                        if part and os.path.exists(part) and os.path.getsize(part) > 0:
                            with open(part, "rb") as r:
                                shutil.copyfileobj(r, w)
                        for _, fn in frags:
                            with open(os.path.join(REC_DIR, fn), "rb") as r:
                                shutil.copyfileobj(r, w)
                    fmt_files.append(out)
                elif part and os.path.exists(part) and os.path.getsize(part) > 1024:
                    # VOD: .part 가 이미 받은 데이터 → 그대로 사용
                    fmt_files.append(part)
            except OSError as e:
                job.log.append("[복구 오류] %s" % e)

        if not fmt_files:
            continue

        final = os.path.join(REC_DIR, prefix + ".mp4")
        cmd = [ffmpeg_path() or "ffmpeg", "-y", "-loglevel", "error"]
        for ff in fmt_files:
            cmd += ["-i", ff]
        cmd += ["-c", "copy", final]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode == 0 and os.path.exists(final):
                produced.append(final)
                fb = os.path.basename(final)
                # concat로 만든 중간 포맷 파일 삭제
                for ff in fmt_files:
                    try:
                        os.remove(ff)
                    except OSError:
                        pass
                # 시작 시점에 있던 조각/중간파일 정리 (최종본은 유지)
                for f in files:
                    if f == fb:
                        continue
                    if vid and vid not in f:
                        continue
                    if (".part-Frag" in f or f.endswith(".ytdl")
                            or re.search(r"\.f\d+\.\w+(\.part)?$", f)):
                        try:
                            os.remove(os.path.join(REC_DIR, f))
                        except OSError:
                            pass
            else:
                job.log.append("[복구 ffmpeg 실패] " + (r.stderr or "")[:200])
        except Exception as e:  # noqa
            job.log.append("[복구 실행 오류] %s" % e)
    return produced


def _reader(job):
    try:
        for raw in job.proc.stdout:
            line = ANSI_RE.sub("", raw).rstrip("\r\n")
            _classify(job, line)
    except Exception as e:  # noqa
        job.log.append("[reader error] %s" % e)
    rc = job.proc.wait()
    job.returncode = rc

    if job._stopping and rc != 0:
        # yt-dlp가 스스로 합치지 못하고 종료됨 → 조각에서 복구
        job.status = "muxing"
        job.progress = "정지: 녹화분을 합치는 중..."
        produced = _finalize_fragments(job)
        if produced:
            job.final_file = os.path.basename(produced[0])
            job.status = "finished"
            job.progress = "완료 (정지 저장) · " + job.final_file
        else:
            job.status = "stopped"
            job.progress = "정지됨 (합칠 조각을 찾지 못함)"
    elif rc == 0:
        job.status = "finished"
        job.progress = "완료" + (" · " + job.final_file if job.final_file else "")
    else:
        job.status = "error"
        if not job.progress:
            job.progress = job.log[-1] if job.log else "오류"


def start_job(opts):
    job = Job(opts)
    if not job.url:
        raise ValueError("URL이 비어 있습니다")
    cmd = _build_command(job)
    job.log.append("$ yt-dlp " + " ".join(cmd[1:]))

    env = dict(os.environ)
    env["PATH"] = BIN_DIR + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"          # yt-dlp가 stdout에 UTF-8로 출력하도록
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WIN else 0

    job.proc = subprocess.Popen(
        cmd, cwd=REC_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        env=env, creationflags=creationflags)
    threading.Thread(target=_reader, args=(job,), daemon=True).start()

    with _jobs_lock:
        jobs[job.id] = job
        jobs_order.append(job.id)
    return job


def stop_job(job_id):
    job = jobs.get(job_id)
    if not job or not job.proc or job.proc.poll() is not None:
        return False
    job._stopping = True
    job.log.append("[정지 요청] 지금까지 받은 분량을 저장합니다...")
    job.progress = "정지 처리 중..."
    try:
        if IS_WIN:
            os.kill(job.proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            job.proc.send_signal(signal.SIGINT)
    except Exception as e:  # noqa
        job.log.append("[정지 오류] %s" % e)
        try:
            job.proc.terminate()
        except Exception:
            pass
    return True


def kill_job(job_id):
    job = jobs.get(job_id)
    if not job or not job.proc:
        return False
    try:
        job.proc.terminate()
    except Exception:
        pass
    return True


def remove_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return False
    if job.proc and job.proc.poll() is None:
        return False
    with _jobs_lock:
        jobs.pop(job_id, None)
        if job_id in jobs_order:
            jobs_order.remove(job_id)
    return True


# ---------------------------------------------------------------------------
# Claude 분석용 압축 (500MB 미만으로 재인코딩)
# ---------------------------------------------------------------------------
ANALYSIS_SUFFIX = "_분석용"
compress_tasks = {}   # id -> dict
_ctask_lock = threading.Lock()


def list_recordings():
    out = []
    try:
        names = os.listdir(REC_DIR)
    except OSError:
        return out
    for f in sorted(names):
        if not f.lower().endswith((".mp4", ".mkv", ".webm", ".m4a")):
            continue
        p = os.path.join(REC_DIR, f)
        if not os.path.isfile(p):
            continue
        out.append({
            "name": f,
            "size_mb": round(os.path.getsize(p) / 1048576, 1),
            "is_analysis": ANALYSIS_SUFFIX in f,
        })
    return out


def _media_duration(path):
    """ffmpeg로 영상 길이(초) 추출."""
    r = subprocess.run([ffmpeg_path() or "ffmpeg", "-i", path],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        return 0.0
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


def _compress_worker(task):
    src = task["src"]
    target_mb = task["target_mb"]
    try:
        dur = _media_duration(src)
        task["duration"] = dur
        if dur <= 0:
            raise RuntimeError("영상 길이를 읽지 못했습니다")

        # 컨테이너 오버헤드 6% 여유 → 총 비트레이트(kbps)
        total_kbps = (target_mb * 8192 * 0.94) / dur
        a_kbps = 96 if total_kbps > 320 else (64 if total_kbps > 180 else 48)
        # 상한 2500k: 짧은 영상이 불필요하게 커지지 않게 (긴 영상은 자동으로 더 낮음)
        v_kbps = int(max(80, min(2500, total_kbps - a_kbps)))
        height = 360 if v_kbps < 250 else 480

        out = task["out"]
        ff = ffmpeg_path() or "ffmpeg"
        cmd = [ff, "-y", "-i", src,
               "-vf", "scale=-2:'min(%d,ih)'" % height,
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
               "-b:v", "%dk" % v_kbps,
               "-maxrate", "%dk" % int(v_kbps * 1.5),
               "-bufsize", "%dk" % (v_kbps * 2),
               "-c:a", "aac", "-b:a", "%dk" % a_kbps,
               "-movflags", "+faststart",
               "-progress", "pipe:1", "-nostats", out]
        task["status"] = "compressing"
        task["detail"] = "%dp · 영상 %dk / 음성 %dk" % (height, v_kbps, a_kbps)

        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True,
                             encoding="utf-8", errors="replace")
        task["proc"] = p
        for line in p.stdout:
            line = line.strip()
            mm = re.match(r"out_time=(\d+):(\d+):([\d.]+)", line)
            if mm and dur:
                cur = int(mm.group(1)) * 3600 + int(mm.group(2)) * 60 + float(mm.group(3))
                task["percent"] = min(99, int(cur / dur * 100))
        rc = p.wait()
        if rc == 0 and os.path.exists(out):
            mb = os.path.getsize(out) / 1048576
            task["size_mb"] = round(mb, 1)
            task["percent"] = 100
            task["status"] = "done"
            task["detail"] = "완료 · %.1f MB (%s)" % (mb, os.path.basename(out))
        else:
            task["status"] = "error"
            task["detail"] = "압축 실패 (ffmpeg rc=%s)" % rc
    except Exception as e:  # noqa
        task["status"] = "error"
        task["detail"] = "오류: %s" % e
    finally:
        task.pop("proc", None)


def start_compress(filename, target_mb=480):
    src = os.path.join(REC_DIR, filename)
    if not os.path.isfile(src):
        raise ValueError("파일을 찾을 수 없습니다")
    stem = os.path.splitext(filename)[0]
    out = os.path.join(REC_DIR, stem + ANALYSIS_SUFFIX + ".mp4")
    tid = uuid.uuid4().hex[:8]
    task = {"id": tid, "src": src, "out": out, "file": filename,
            "out_name": os.path.basename(out), "target_mb": target_mb,
            "status": "starting", "percent": 0, "detail": "준비 중...",
            "created": time.time()}
    with _ctask_lock:
        compress_tasks[tid] = task
    threading.Thread(target=_compress_worker, args=(task,), daemon=True).start()
    return task


def compress_public(task):
    return {k: task[k] for k in
            ("id", "file", "out_name", "status", "percent", "detail",
             "size_mb", "target_mb", "created") if k in task}


# ---------------------------------------------------------------------------
# HTTP 핸들러
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "live-archiver/2.0"

    def log_message(self, *a):
        pass

    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send_file(os.path.join(STATIC_DIR, "index.html"),
                                   "text/html; charset=utf-8")
        if path == "/app.js":
            return self._send_file(os.path.join(STATIC_DIR, "app.js"),
                                   "application/javascript; charset=utf-8")
        if path == "/style.css":
            return self._send_file(os.path.join(STATIC_DIR, "style.css"),
                                   "text/css; charset=utf-8")
        if path == "/api/status":
            return self._send_json({
                "ready": binaries_ready(),
                "ytdlp": ytdlp_path() is not None,
                "ffmpeg": ffmpeg_path() is not None,
                "setup": setup_state, "rec_dir": REC_DIR})
        if path == "/api/jobs":
            with _jobs_lock:
                items = [jobs[i].to_dict() for i in jobs_order if i in jobs]
            return self._send_json({"jobs": items})
        if path.startswith("/api/jobs/") and path.endswith("/log"):
            job = jobs.get(path.split("/")[3])
            if not job:
                return self._send_json({"error": "not found"}, 404)
            return self._send_json({"log": list(job.log), "progress": job.progress})
        if path == "/api/recordings":
            return self._send_json({"files": list_recordings()})
        if path == "/api/compress":
            with _ctask_lock:
                items = [compress_public(t) for t in compress_tasks.values()]
            items.sort(key=lambda x: x.get("created", 0))
            return self._send_json({"tasks": items})
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/setup":
            start_setup()
            return self._send_json({"ok": True, "setup": setup_state})
        if path == "/api/jobs":
            if not binaries_ready():
                return self._send_json({"error": "바이너리가 설치되지 않았습니다"}, 400)
            try:
                job = start_job(self._body_json())
            except Exception as e:  # noqa
                return self._send_json({"error": str(e)}, 400)
            return self._send_json({"ok": True, "job": job.to_dict()})
        if path == "/api/compress":
            body = self._body_json()
            try:
                tmb = int(body.get("target_mb", 480) or 480)
                task = start_compress(body.get("file", ""), max(50, min(490, tmb)))
            except Exception as e:  # noqa
                return self._send_json({"error": str(e)}, 400)
            return self._send_json({"ok": True, "task": compress_public(task)})
        if path.startswith("/api/jobs/") and path.endswith("/stop"):
            return self._send_json({"ok": stop_job(path.split("/")[3])})
        if path.startswith("/api/jobs/") and path.endswith("/kill"):
            return self._send_json({"ok": kill_job(path.split("/")[3])})
        if path.startswith("/api/jobs/") and path.endswith("/remove"):
            return self._send_json({"ok": remove_job(path.split("/")[3])})
        if path == "/api/open-folder":
            try:
                if IS_WIN:
                    os.startfile(REC_DIR)  # noqa
                elif IS_MAC:
                    subprocess.Popen(["open", REC_DIR])
                else:
                    subprocess.Popen(["xdg-open", REC_DIR])
                return self._send_json({"ok": True})
            except Exception as e:  # noqa
                return self._send_json({"ok": False, "error": str(e)})
        self.send_error(404)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d/" % (HOST, PORT)
    print("=" * 56)
    print("  라이브 아카이버 (yt-dlp) 실행 중")
    print("  브라우저: %s" % url)
    print("  저장 폴더: %s" % REC_DIR)
    print("  (종료: 이 창에서 Ctrl+C)")
    print("=" * 56)
    if not binaries_ready():
        start_setup()
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다...")
        for jid in list(jobs.keys()):
            stop_job(jid)
        srv.shutdown()


if __name__ == "__main__":
    main()
