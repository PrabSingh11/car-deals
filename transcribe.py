#!/usr/bin/env python3
"""
car-deals TikTok transcriber.

Give it TikTok creator handles and/or direct video URLs. For each video it:
  1. tries to grab TikTok's own auto-captions (instant, free), else
  2. downloads the audio and transcribes it locally with faster-whisper.

Transcripts are written to transcripts/<handle>/<video_id>.txt
A combined transcripts/ALL.md is also kept, newest first.

Usage:
  python3 transcribe.py                      # read creators from creators.txt
  python3 transcribe.py @somehandle          # one creator
  python3 transcribe.py https://tiktok.com/@x/video/123   # one video
  python3 transcribe.py --per-creator 5      # how many recent vids per creator (default 10)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "audio"
TRANS = ROOT / "transcripts"
CREATORS_FILE = ROOT / "creators.txt"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
COOKIES = ROOT / "cookies.txt"
THROTTLE = float(os.environ.get("THROTTLE", "8"))  # seconds between TikTok hits (avoid 429)

AUDIO.mkdir(exist_ok=True)
TRANS.mkdir(exist_ok=True)

_model = None


def log(*a):
    print(*a, flush=True)


def get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log(f"[whisper] loading model '{WHISPER_MODEL}' (first run downloads it)...")
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def embed_url(vid_id):
    # TikTok blocks the normal per-video API from server IPs; the embed
    # endpoint still serves the media, so route single videos through it.
    return f"https://www.tiktok.com/embed/v2/{vid_id}"


def parse_video(url):
    m = re.search(r"@([\w.\-]+)/video/(\d+)", url)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"/video/(\d+)|/(\d{15,})", url)
    vid = (m.group(1) or m.group(2)) if m else None
    return None, vid


def ytdlp_json(url, extra=None):
    cmd = ["yt-dlp", "-J", "--no-warnings"]
    if COOKIES.exists():
        cmd += ["--cookies", str(COOKIES)]
    if extra:
        cmd += extra
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        log(f"[yt-dlp] error on {url}:\n{out.stderr.strip()[:500]}")
        return None
    return json.loads(out.stdout)


def list_creator_videos(handle, limit):
    handle = handle.lstrip("@")
    url = f"https://www.tiktok.com/@{handle}"
    log(f"[creator] fetching up to {limit} recent videos from @{handle} ...")
    time.sleep(THROTTLE)
    data = ytdlp_json(url, ["--flat-playlist", "-I", f"1:{limit}"])
    if not data:
        return []
    entries = data.get("entries") or []
    vids = []
    for e in entries:
        vid_url = e.get("url") or e.get("webpage_url")
        if vid_url:
            vids.append(vid_url)
    return vids


def transcribe_audio(path):
    model = get_model()
    segments, info = model.transcribe(str(path), vad_filter=True)
    parts = [s.text.strip() for s in segments]
    return " ".join(p for p in parts if p).strip()


def try_captions(info):
    """Return TikTok's own caption text if available, else None."""
    subs = {}
    subs.update(info.get("subtitles") or {})
    subs.update(info.get("automatic_captions") or {})
    if not subs:
        return None
    # prefer english-ish tracks
    key = next((k for k in subs if k.lower().startswith("en")), None) or next(iter(subs), None)
    tracks = subs.get(key) or []
    vtt = next((t for t in tracks if t.get("ext") == "vtt"), tracks[0] if tracks else None)
    if not vtt or not vtt.get("url"):
        return None
    try:
        import urllib.request
        raw = urllib.request.urlopen(vtt["url"], timeout=30).read().decode("utf-8", "ignore")
    except Exception as e:
        log(f"[captions] fetch failed: {e}")
        return None
    lines = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln or ln == "WEBVTT" or "-->" in ln or ln.isdigit():
            continue
        ln = re.sub(r"<[^>]+>", "", ln)
        if ln and (not lines or lines[-1] != ln):
            lines.append(ln)
    text = " ".join(lines).strip()
    return text or None


def process_video(url, handle_hint=None):
    handle, vid_id = parse_video(url)
    handle = (handle or handle_hint or "unknown").lstrip("@")
    if not vid_id:
        log(f"[skip] couldn't parse a video id from {url}")
        return
    out_dir = TRANS / handle
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"{vid_id}.txt"
    if out_file.exists():
        log(f"[skip] already have {handle}/{vid_id}")
        return

    # Pull media through the embed endpoint (normal API is IP-blocked here).
    emb = embed_url(vid_id)
    info = ytdlp_json(emb)
    title = ""
    if info:
        node = info
        if info.get("_type") == "playlist" and info.get("entries"):
            node = info["entries"][0]
        title = (node.get("title") or node.get("description") or "").strip()
        if title.lower().startswith("tiktok embed"):
            title = ""

    source = "whisper"
    audio_path = AUDIO / f"{vid_id}.m4a"
    log(f"[audio] downloading {handle}/{vid_id} ...")
    time.sleep(THROTTLE)
    dl = subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "m4a", "--no-warnings"]
        + (["--cookies", str(COOKIES)] if COOKIES.exists() else [])
        + ["-o", str(AUDIO / f"{vid_id}.%(ext)s"), emb],
        capture_output=True, text=True)
    if dl.returncode != 0 or not audio_path.exists():
        log(f"[audio] download failed for {url}:\n{dl.stderr.strip()[:400]}")
        return
    log(f"[whisper] transcribing {handle}/{vid_id} ...")
    text = transcribe_audio(audio_path)
    try:
        audio_path.unlink()
    except OSError:
        pass

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (f"Creator : @{handle}\n"
              f"Video   : {url}\n"
              f"Caption : {title}\n"
              f"Source  : {source}\n"
              f"Pulled  : {ts}\n"
              + "-" * 60 + "\n\n")
    out_file.write_text(header + (text or "(no speech detected)") + "\n", encoding="utf-8")
    log(f"[done] {out_file.relative_to(ROOT)}  ({len(text or '')} chars via {source})")

    # prepend to combined file
    all_md = TRANS / "ALL.md"
    block = (f"## @{handle} — {title or vid_id}\n"
             f"{url} · {ts} · via {source}\n\n{text or '(no speech detected)'}\n\n---\n\n")
    prev = all_md.read_text(encoding="utf-8") if all_md.exists() else ""
    all_md.write_text(block + prev, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*", help="handles (@x) or video URLs")
    ap.add_argument("--per-creator", type=int, default=10)
    args = ap.parse_args()

    targets = args.targets
    if not targets:
        if CREATORS_FILE.exists():
            targets = [ln.strip() for ln in CREATORS_FILE.read_text().splitlines()
                       if ln.strip() and not ln.startswith("#")]
        if not targets:
            log("No targets given and creators.txt is empty. "
                "Add TikTok handles (one per line) to creators.txt.")
            sys.exit(1)

    video_urls, creators = [], []
    for t in targets:
        if "tiktok.com" in t and "/video/" in t:
            video_urls.append(t)
        else:
            creators.append(t)

    for c in creators:
        video_urls.extend(list_creator_videos(c, args.per_creator))

    log(f"[plan] {len(video_urls)} videos to process")
    for u in video_urls:
        try:
            process_video(u)
        except Exception as e:
            log(f"[error] {u}: {e}")


if __name__ == "__main__":
    main()
