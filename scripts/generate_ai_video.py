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


_GEMINI_MODEL_CACHE = {"name": None}


def pick_gemini_model(api_key):
    """Ask the API which models are actually available to this key, instead
    of hardcoding a model name that can go stale as Google renames/retires
    models. Caches the result for the life of this process."""
    if _GEMINI_MODEL_CACHE["name"]:
        return _GEMINI_MODEL_CACHE["name"]
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    r = requests.get(url, headers={"x-goog-api-key": api_key}, timeout=30)
    r.raise_for_status()
    models = r.json().get("models", [])
    candidates = [m["name"] for m in models
                  if "generateContent" in m.get("supportedGenerationMethods", [])]
    if not candidates:
        return None
    # Prefer a "flash" model: fast and on the most generous free tier.
    flash = [m for m in candidates if "flash" in m.lower()]
    chosen = flash[0] if flash else candidates[0]
    _GEMINI_MODEL_CACHE["name"] = chosen
    print(f"Using Gemini model: {chosen}")
    return chosen


def call_gemini(prompt_text, _retry_model=None):
    """Primary LLM source, if GEMINI_API_KEY is set. Free tier, no card required
    (get a key at https://aistudio.google.com/apikey). Returns None if unavailable
    or on any error — caller falls back to the next source.

    Google's model list frequently outlives actual availability; when a model
    is retired, their 404 response names the replacement directly (e.g. "use
    models/gemini-3.6-flash instead") — so on a 404 we parse that out and
    retry once with the suggested model, caching it for the rest of this run.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        model = _retry_model or os.environ.get("GEMINI_MODEL") or pick_gemini_model(api_key)
        if not model:
            print("Gemini: no models available to this API key.")
            return None
        model_path = model if model.startswith("models/") else f"models/{model}"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent"
        r = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt_text}]}]},
            timeout=60,
        )

        if r.status_code == 404 and _retry_model is None:
            matches = re.findall(r"models/[a-zA-Z0-9_.\-]+", r.text)
            replacement = next((m for m in matches if m != model_path), None)
            if replacement:
                print(f"Gemini: {model_path} unavailable, Google suggested {replacement} — retrying with it")
                _GEMINI_MODEL_CACHE["name"] = replacement
                return call_gemini(prompt_text, _retry_model=replacement)

        if r.status_code != 200:
            print(f"Gemini error response: {r.text[:500]}")
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"Gemini call failed, will try backup source: {e}")
        return None


def call_pollinations(prompt_text):
    """Backup LLM source. Was free/unauthenticated; Pollinations has since
    deprecated their legacy text API for this kind of request (confirmed via
    402 responses) — kept here only as a last-ditch attempt in case that
    changes again. Gemini should be treated as the real primary source."""
    try:
        url = "https://text.pollinations.ai/" + urllib.parse.quote(prompt_text)
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            print(f"Pollinations error response ({r.status_code}): {r.text[:300]}")
        r.raise_for_status()
        return r.text.strip()
    except Exception as e:
        print(f"Pollinations call failed: {e}")
        return None


def call_llm(prompt_text):
    """Tries Gemini first (if configured), then the free backup. Returns None
    only if every source failed."""
    return call_gemini(prompt_text) or call_pollinations(prompt_text)


def generate_script(topic, target_words=150):
    prompt = (f"Write an engaging narration script of about {target_words} words "
              f"for a vertical short video about: {topic}. Break it into 5-8 short, "
              f"punchy sentences (one idea per sentence). Only output the narration "
              f"text itself, no titles, no formatting, no quotation marks, no numbering.")

    text = call_llm(prompt)
    if text and len(text.split()) >= target_words * 0.5:
        return text

    if text:
        print(f"Script came back too short ({len(text.split())} words), retrying once...")
    text2 = call_llm(prompt)
    if text2 and (not text or len(text2.split()) > len(text.split())):
        text = text2

    if not text:
        raise RuntimeError(
            "Every script-writing source failed (Gemini and the free backup both "
            "returned nothing). Not uploading anything for this run — check that "
            "GEMINI_API_KEY is set and valid, or retry later if it's the free "
            "backup service having downtime."
        )
    return text


def generate_more_facts(topic, already_said, target_words=60):
    """Ask for additional distinct content when the video is running short.
    Returns empty string (not an error) if this fails — the caller treats
    that as 'couldn't extend further' rather than a fatal problem, since we
    already have a valid script at this point."""
    prompt = (f"Give {max(2, target_words // 25)} more short, interesting, distinct "
              f"facts or sentences about: {topic}. Do not repeat these already used "
              f"points: {already_said[:300]}. Only output the new sentences, no "
              f"numbering, no formatting.")
    return call_llm(prompt) or ""


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
    ap.add_argument("--target-words", type=int, default=150,
                     help="Roughly maps to seconds of narration at natural speaking pace.")
    ap.add_argument("--min-duration", type=float, default=50.0,
                     help="Keep requesting more content until narration reaches at least this many seconds.")
    ap.add_argument("--max-duration", type=float, default=58.0,
                     help="Stop adding scenes once this many seconds is reached, to stay a Short.")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(__file__))
    from upload_youtube import upload_short

    workdir = tempfile.mkdtemp()
    try:
        script = generate_script(args.prompt, args.target_words)
        scenes = split_scenes(script)
        if not scenes:
            print("No scenes produced from the script.")
            sys.exit(1)

        clip_paths = []
        scenes_with_durations = []

        def render_scene(text, index):
            audio_path = os.path.join(workdir, f"scene_{index}.mp3")
            image_path = os.path.join(workdir, f"scene_{index}.png")
            clip_path = os.path.join(workdir, f"scene_{index}.mp4")
            generate_narration(text, audio_path, args.voice)
            dur = get_duration(audio_path)
            generate_image(text, image_path)
            make_scene_clip(image_path, audio_path, dur, clip_path)
            return clip_path, dur

        i = 0
        for text in scenes:
            i += 1
            clip_path, dur = render_scene(text, i)
            clip_paths.append(clip_path)
            scenes_with_durations.append((text, dur))

        total_duration = sum(d for _, d in scenes_with_durations)

        # Keep the video from coming out too short: ask for more distinct
        # content and render additional scenes until we hit min_duration,
        # capped by max_duration and a retry limit so this can't loop forever.
        attempts = 0
        already_said = " ".join(t for t, _ in scenes_with_durations)
        while total_duration < args.min_duration and attempts < 4:
            attempts += 1
            print(f"Only {total_duration:.1f}s so far, requesting more content "
                  f"(attempt {attempts})...")
            more = generate_more_facts(args.prompt, already_said,
                                        target_words=int(args.min_duration - total_duration) * 3)
            more_scenes = split_scenes(more)
            if not more_scenes:
                print("No additional content came back; stopping extension attempts.")
                break
            for text in more_scenes:
                if total_duration >= args.max_duration:
                    break
                i += 1
                clip_path, dur = render_scene(text, i)
                clip_paths.append(clip_path)
                scenes_with_durations.append((text, dur))
                total_duration += dur
                already_said += " " + text

        print(f"Final narrated duration: {total_duration:.1f}s "
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
