# -*- coding: utf-8 -*-
"""
음성 전사 헬퍼 (faster-whisper, GPU 우선 → CPU 폴백)

사용:  python transcribe.py <입력파일> --out <출력경로(확장자 제외)> [--model large-v3] [--lang ko]
출력:  <out>.srt (자막), <out>.txt (대본 텍스트)
진행:  stdout 에 "PROGRESS <처리초> <전체초>" / "DEVICE <cuda|cpu>" / "DONE <세그먼트수>"
"""
import os
import sys
import glob
import site
import argparse


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", required=True, help="출력 경로(확장자 제외)")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--lang", default="ko")
    ap.add_argument("--beam", type=int, default=5)
    a = ap.parse_args()

    _setup_cuda_dlls()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from faster_whisper import WhisperModel

    device, compute = "cuda", "float16"
    try:
        model = WhisperModel(a.model, device="cuda", compute_type="float16")
    except Exception as e:  # noqa
        sys.stderr.write("GPU 사용 불가, CPU로 전환: %s\n" % e)
        device, compute = "cpu", "int8"
        model = WhisperModel(a.model, device="cpu", compute_type="int8")
    print("DEVICE %s" % device, flush=True)

    segments, info = model.transcribe(
        a.input, language=a.lang, beam_size=a.beam, vad_filter=True)
    total = float(getattr(info, "duration", 0) or 0)

    srt_path = a.out + ".srt"
    txt_path = a.out + ".txt"
    n = 0
    with open(srt_path, "w", encoding="utf-8") as srt, \
            open(txt_path, "w", encoding="utf-8") as txt:
        for seg in segments:
            n += 1
            line = seg.text.strip()
            srt.write("%d\n%s --> %s\n%s\n\n" %
                      (n, _srt_ts(seg.start), _srt_ts(seg.end), line))
            txt.write(line + "\n")
            srt.flush()
            txt.flush()
            if total:
                print("PROGRESS %.1f %.1f" % (seg.end, total), flush=True)
    print("DONE %d" % n, flush=True)


if __name__ == "__main__":
    main()
