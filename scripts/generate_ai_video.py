"""
Generates a short vertical video from a text prompt, entirely with free
services, then uploads it via the same YouTube auth as pipeline.py:

  1. Script: expand the prompt into a short narration (Pollinations free
     text API; falls back to using the prompt itself if that call fails).
  2. Per sentence/scene: free TTS narration (edge-tts) + a free AI image
     (Pollinations image API) matching that line.
  3. Each scene becomes a Ken Burns (pan/zoom) clip sized to its audio's
     duration, all cut together with ffmpeg.
  4. Captions are burned in from a generated SRT.
  5. Upload, reusing upload_youtube.py.

Usage:
    python generate_ai_video.py --prompt "the history of coffee" --privacy unlisted
"""
import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

import edge_tts
import requests

FPS = 25
IMG_W, IMG_H = 1080, 1920


def run(cmd):
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
    return r


def get_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def generate_script(topic, max_words=70):
    try:
        prompt = (f"Write a short, engaging {max_words}-word narration script for a "
                  f"vertical short video about: {topic}. Only output the narration "
                  f"text itself, no titles, no formatting, no quotation marks.")
        url = "https://text.pollinations.ai/" + urllib.parse.quote(prompt)
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        text = r.text.strip()
        if text:
            return text
    except Exception as e:
        print(f"Script generation failed, falling back to prompt as-is: {e}")
    return topic


def split_scenes(script):
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())
    return [s.strip() for s in sentences if s.strip()]


async def tts_save(text, path, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(path)


def generate_narration(text, path, voice):
    asyncio.run(tts_save(text, path, voice))


def generate_image(prompt, path, style_suffix=", vertical, cinematic, high detail"):
    url = ("https://image.pollinations.ai/prompt/" +
           urllib.parse.quote(prompt + style_suffix) +
           f"?width={IMG_W}&height={IMG_H}&nologo=true")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)


def make_scene_clip(image_path, audio_path, duration, out_path):
    frames = max(int(duration * FPS), FPS)
    vf = (f"scale=2160:3840:force_original_aspect_ratio=increase,"
          f"crop=2160:3840,"
          f"zoompan=z='min(zoom+0.0012,1.4)':d={frames}:s={IMG_W}x{IMG_H}:fps={FPS}")
    silent = out_path + ".silent.mp4"
    run(["ffmpeg", "-y", "-loop", "1", "-i", image_path, "-vf", vf,
         "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", silent])
    run(["ffmpeg", "-y", "-i", silent, "-i", audio_path,
         "-c:v", "copy", "-c:a", "aac", "-shortest", out_path])
    os.remove(silent)


def concat_clips(clip_paths, out_path, workdir):
    list_path = os.path.join(workdir, "concat_list.txt")
    with open(list_path, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", out_path])


def srt_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def build_srt(scenes_with_durations, out_path):
    lines = []
    t = 0.0
    for i, (text, dur) in enumerate(scenes_with_durations, 1):
        start, end = t, t + dur
        lines.append(str(i))
        lines.append(f"{srt_timestamp(start)} --> {srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
        t = end
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def burn_captions(video_path, srt_path, out_path):
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    vf = (f"subtitles={srt_escaped}:force_style='FontSize=14,PrimaryColour=&H00FFFFFF,"
          f"OutlineColour=&H00000000,BorderStyle=3,Outline=1,Alignment=2,MarginV=80'")
    run(["ffmpeg", "-y", "-i", video_path, "-vf", vf,
         "-c:a", "copy", out_path])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--voice", default="en-US-AriaNeural")
    ap.add_argument("--privacy", default="unlisted", choices=["unlisted", "public", "private"])
    ap.add_argument("--max-words", type=int, default=70)
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(__file__))
    from upload_youtube import upload_short

    workdir = tempfile.mkdtemp()
    try:
        script = generate_script(args.prompt, args.max_words)
        scenes = split_scenes(script)
        if not scenes:
            print("No scenes produced from the script.")
            sys.exit(1)

        clip_paths = []
        scenes_with_durations = []
        for i, text in enumerate(scenes, 1):
            audio_path = os.path.join(workdir, f"scene_{i}.mp3")
            image_path = os.path.join(workdir, f"scene_{i}.png")
            clip_path = os.path.join(workdir, f"scene_{i}.mp4")

            generate_narration(text, audio_path, args.voice)
            dur = get_duration(audio_path)
            generate_image(text, image_path)
            make_scene_clip(image_path, audio_path, dur, clip_path)

            clip_paths.append(clip_path)
            scenes_with_durations.append((text, dur))

        total_duration = sum(d for _, d in scenes_with_durations)
        print(f"Total narrated duration: {total_duration:.1f}s "
              f"({'fits' if total_duration <= 59 else 'EXCEEDS'} Shorts length)")

        combined_path = os.path.join(workdir, "combined.mp4")
        concat_clips(clip_paths, combined_path, workdir)

        srt_path = os.path.join(workdir, "captions.srt")
        build_srt(scenes_with_durations, srt_path)

        final_path = os.path.join(workdir, "final.mp4")
        burn_captions(combined_path, srt_path, final_path)

        title = args.prompt.strip()[:90]
        if total_duration <= 59:
            title += " #Shorts"
        desc = (f"AI-generated video.\nPrompt: {args.prompt}\n\n"
                f"Script:\n{script}")
        video_id = upload_short(final_path, title, desc, privacy=args.privacy)
        print(f"Uploaded: https://youtube.com/watch?v={video_id}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
