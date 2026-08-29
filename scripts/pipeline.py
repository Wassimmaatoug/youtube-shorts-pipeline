"""
Core pipeline: download -> detect highlights (silence + scene-change heuristics)
-> cut vertical clips -> upload to YouTube as Shorts.

Usage:
    python pipeline.py --url "<youtube_url>" --num-clips 3 --privacy unlisted
"""
import argparse
import base64
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

MIN_CLIP = 20   # seconds, YouTube Shorts minimum useful length
MAX_CLIP = 58   # keep under 60s to stay in Shorts territory


def run(cmd):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, capture_output=True, text=True)


def write_cookies_file(outdir):
    """Cookie source, in priority order:
    - YTDLP_COOKIES: raw Netscape-format cookies.txt content, pasted as-is
    - YTDLP_COOKIES_B64: base64 of the same (kept for compatibility)
    Returns the path to a written cookies file, or None if neither is set."""
    raw = os.environ.get("YTDLP_COOKIES")
    b64 = os.environ.get("YTDLP_COOKIES_B64")
    if not raw and not b64:
        return None
    cookies_path = os.path.join(outdir, "cookies.txt")
    if raw:
        with open(cookies_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw.strip() + "\n")
    else:
        with open(cookies_path, "wb") as f:
            f.write(base64.b64decode(b64))
    return cookies_path


def download(url, outdir):
    out_tpl = os.path.join(outdir, "source.%(ext)s")
    cmd = [
        "yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]",
        "--merge-output-format", "mp4",
        # GitHub Actions IPs are commonly bot-checked by YouTube on the
        # default web client; the android client skips that JS challenge.
        "--extractor-args", "youtube:player_client=android,web",
        "-4",  # avoid flaky IPv6 on some runners
    ]
    cookies_path = write_cookies_file(outdir)
    if cookies_path:
        cmd += ["--cookies", cookies_path]
    cmd += ["-o", out_tpl, url]
    r = run(cmd)
    matches = glob.glob(os.path.join(outdir, "source.*"))
    if not matches:
        print("---- yt-dlp stderr ----")
        print(r.stderr)
        print("---- yt-dlp stdout ----")
        print(r.stdout)
        raise RuntimeError(f"yt-dlp failed to download the video (exit code {r.returncode})")
    return matches[0]


def get_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def detect_silence(path, noise_db="-30dB", min_silence=0.6):
    r = run(["ffmpeg", "-i", path, "-af",
             f"silencedetect=noise={noise_db}:d={min_silence}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    return starts, ends


def non_silent_segments(duration, starts, ends):
    silences = sorted(zip(starts, ends[:len(starts)]))
    segs, cursor = [], 0.0
    for s, e in silences:
        if s > cursor:
            segs.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        segs.append((cursor, duration))
    return segs


def scene_changes(path, threshold=0.35):
    r = run(["ffmpeg", "-i", path, "-vf",
             f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)]


def mean_volume(path, start, end):
    r = run(["ffmpeg", "-ss", str(start), "-to", str(end), "-i", path,
             "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume: (-?[0-9.]+) dB", r.stderr)
    return float(m.group(1)) if m else -100.0


def frange(a, b, step):
    x = a
    while x < b:
        yield x
        x += step


def score_segments(path, segs, scenes):
    scored = []
    for s, e in segs:
        length = e - s
        if length < MIN_CLIP:
            continue
        window_starts = [s] if length <= MAX_CLIP else list(frange(s, e - MIN_CLIP, MAX_CLIP))
        for ws in window_starts:
            we = min(ws + MAX_CLIP, e)
            if we - ws < MIN_CLIP:
                continue
            vol = mean_volume(path, ws, we)
            scene_count = sum(1 for t in scenes if ws <= t <= we)
            score = vol + scene_count * 2.0  # louder + more cuts = more "highlight-like"
            scored.append({"start": ws, "end": we, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def pick_non_overlapping(scored, n):
    picked = []
    for c in scored:
        if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in picked):
            picked.append(c)
        if len(picked) >= n:
            break
    return picked


def cut_vertical(path, start, end, out_path):
    dur = end - start
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    run(["ffmpeg", "-y", "-ss", str(start), "-t", str(dur), "-i", path,
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", out_path])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--num-clips", type=int, default=3)
    ap.add_argument("--privacy", default="unlisted", choices=["unlisted", "public", "private"])
    ap.add_argument("--title-prefix", default="")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(__file__))
    from upload_youtube import upload_short, get_video_title

    workdir = tempfile.mkdtemp()
    try:
        src = download(args.url, workdir)
        duration = get_duration(src)
        starts, ends = detect_silence(src)
        segs = non_silent_segments(duration, starts, ends)
        scenes = scene_changes(src)
        scored = score_segments(src, segs, scenes)
        picks = pick_non_overlapping(scored, args.num_clips)

        if not picks:
            print("No suitable segments found — video may be too short or too quiet.")
            sys.exit(1)

        base_title = args.title_prefix or get_video_title(args.url)
        for i, p in enumerate(picks, 1):
            clip_path = os.path.join(workdir, f"short_{i}.mp4")
            cut_vertical(src, p["start"], p["end"], clip_path)
            title = f"{base_title} #Shorts"[:95]
            desc = f"Auto-generated short.\nSource: {args.url}\nSegment: {p['start']:.0f}s-{p['end']:.0f}s"
            video_id = upload_short(clip_path, title, desc, privacy=args.privacy)
            print(f"Clip {i}: uploaded as https://youtube.com/watch?v={video_id}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
