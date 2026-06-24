# -*- coding: utf-8 -*-
"""
음성 전사 헬퍼 (faster-whisper, GPU 우선 → CPU 폴백) + 이어하기(resume)

사용:  python transcribe.py <입력파일> --out <출력경로(확장자 제외)> [--model large-v3] [--lang ko] [--restart]
출력:  <out>.srt (자막), <out>.txt (대본 텍스트)
진행:  stdout 에 "DEVICE <cuda|cpu>" / "RESUME <초>" / "PROGRESS <처리초> <전체초>" / "DONE <세그먼트수>"

이어하기: <out>.srt 가 이미 있으면 마지막 자막 시각부터 이어서 전사하고 뒤에 덧붙입니다.
          처음부터 다시 하려면 --restart (또는 _대본 파일 삭제).
"""
import os
import re
import sys
import glob
import site
import argparse
import subprocess


def _setup_cuda_dlls():
    """pip nvidia-* 패키지의 CUDA DLL을 로드 가능하게 PATH/검색경로에 등록."""
    dirs = []
    roots = set(site.getsitepackages() + [site.getusersitepackages()])
    for sp in roots:
        dirs += glob.glob(os.path.join(sp, "nvidia", "*", "bin"))
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
        for d in dirs:
            try:
                os.add_dll_directory(d)
            except OSError:
                pass


def _find_ffmpeg():
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin",
                         "ffmpeg" + (".exe" if os.name == "nt" else ""))
    if os.path.exists(local):
        return local
    from shutil import which
    return which("ffmpeg") or "ffmpeg"


def _srt_ts(t):
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def _resume_point(srt_path):
    """기존 srt에서 (마지막 끝시각 초, 마지막 자막번호)를 구함. 없으면 (0,0)."""
    if not os.path.exists(srt_path):
        return 0.0, 0
    last_end, last_idx = 0.0, 0
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    last_idx = max(last_idx, int(line))
                m = re.search(r"-->\s*(\d+):(\d+):(\d+),(\d+)", line)
                if m:
                    h, mn, s, ms = map(int, m.groups())
                    last_end = h * 3600 + mn * 60 + s + ms / 1000.0
    except OSError:
        return 0.0, 0
    return last_end, last_idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", required=True, help="출력 경로(확장자 제외)")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--lang", default="ko")
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--restart", action="store_true", help="이어하기 없이 처음부터")
    a = ap.parse_args()

    _setup_cuda_dlls()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    srt_path, txt_path = a.out + ".srt", a.out + ".txt"
    offset, base_idx = (0.0, 0) if a.restart else _resume_point(srt_path)
    if a.restart:
        for p in (srt_path, txt_path):
            try:
                os.remove(p)
            except OSError:
                pass

    # 이어하기면 offset 이후 구간만 ffmpeg로 잘라서 전사
    audio_input = a.input
    tmp_wav = None
    if offset > 1.0:
        print("RESUME %.1f" % offset, flush=True)
        tmp_wav = a.out + "._resume.wav"
        ff = _find_ffmpeg()
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-ss", "%.3f" % offset,
                            "-i", a.input, "-vn", "-ac", "1", "-ar", "16000",
                            "-c:a", "pcm_s16le", tmp_wav],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(tmp_wav):
            sys.stderr.write("이어하기용 오디오 자르기 실패, 처음부터 진행\n")
            offset, base_idx, tmp_wav = 0.0, 0, None
        else:
            audio_input = tmp_wav

    from faster_whisper import WhisperModel
    device = "cuda"
    try:
        model = WhisperModel(a.model, device="cuda", compute_type="float16")
    except Exception as e:  # noqa
        sys.stderr.write("GPU 사용 불가, CPU로 전환: %s\n" % e)
        device = "cpu"
        model = WhisperModel(a.model, device="cpu", compute_type="int8")
    print("DEVICE %s" % device, flush=True)

    segments, info = model.transcribe(
        audio_input, language=a.lang, beam_size=a.beam, vad_filter=True)
    total = offset + float(getattr(info, "duration", 0) or 0)

    mode = "a" if (offset > 1.0 and base_idx > 0) else "w"
    n = base_idx
    with open(srt_path, mode, encoding="utf-8") as srt, \
            open(txt_path, mode, encoding="utf-8") as txt:
        for seg in segments:
            n += 1
            line = seg.text.strip()
            st, en = seg.start + offset, seg.end + offset
            srt.write("%d\n%s --> %s\n%s\n\n" % (n, _srt_ts(st), _srt_ts(en), line))
            txt.write(line + "\n")
            srt.flush()
            txt.flush()
            if total:
                print("PROGRESS %.1f %.1f" % (en, total), flush=True)

    if tmp_wav:
        try:
            os.remove(tmp_wav)
        except OSError:
            pass
    print("DONE %d" % n, flush=True)


if __name__ == "__main__":
    main()
