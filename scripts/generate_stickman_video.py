"""
Fully automated stickman-explainer video generator.

Reuses the LLM/TTS/image/caption/upload plumbing from generate_ai_video.py
and adds: a consistent stickman character design, a fixed scene structure
(base design + N scenes with pose/action/prop), and a 16:9 canvas instead
of vertical Shorts.

Usage:
    python generate_stickman_video.py --topic "why we procrastinate" --privacy unlisted
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from generate_ai_video import (  # noqa: E402
    call_llm, generate_narration, get_duration, run,
    build_srt, burn_captions, generate_description, generate_hashtags,
    fetch_and_validate_image,
)

STICK_W, STICK_H = 1920, 1080


def generate_structured_content(topic, num_scenes=10):
    prompt = f"""You are a professional AI YouTube content creator specializing in simple
stickman animations. Create a ~55-60 second video script on: {topic}

Split it into {num_scenes} scenes (roughly 5-6 seconds of narration each).
First design ONE consistent stickman character description (style, line
weight, expression style, background style) that every scene will reuse.
Then for each scene give: a pose/action/expression/prop description, a short
motion note (only arms/head/expression/props may move; body stays mostly
static; slow and minimal), and a 1-2 sentence voiceover line in natural,
conversational English with a clear hook at scene 1 and a takeaway at the
final scene.

Output ONLY valid JSON, no markdown fences, no commentary, in exactly this
shape:
{{
  "base_style": "<consistent stickman design description>",
  "scenes": [
    {{"pose_description": "...", "motion": "...", "voiceover": "..."}}
  ]
}}"""
    text = call_llm(prompt)
    if not text:
        raise RuntimeError(
            "Every script-writing source failed. Not generating anything for "
            "this run — check GEMINI_API_KEY, or retry later."
        )
    # Strip markdown code fences if the model added them despite instructions.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Script output wasn't valid JSON: {e}\nRaw output:\n{text[:1000]}")
    if not data.get("scenes"):
        raise RuntimeError("Script JSON had no scenes.")
    return data


def generate_stickman_image(base_style, pose_description, path):
    """Uses the shared validated fetcher (see generate_ai_video.py) so a
    broken/tiny image from the generator raises loudly instead of getting
    silently force-stretched into an unrecognizable blur, which is what
    happened before this fix."""
    import urllib.parse
    full_prompt = (
        f"Use the same stickman character as before. {base_style} {pose_description} "
        f"Clean minimal white background, flat vector illustration style, "
        f"no text, no watermark, no logo."
    )
    url = ("https://image.pollinations.ai/prompt/" +
           urllib.parse.quote(full_prompt) +
           f"?width={STICK_W}&height={STICK_H}&nologo=true&model=flux&enhance=true")
    fetch_and_validate_image(url, path, min_w=STICK_W * 0.5, min_h=STICK_H * 0.5)


def make_scene_clip(image_path, audio_path, duration, out_path, zoom_in=True):
    frames = max(int(duration * 25), 25)
    direction = "min(zoom+0.0008,1.25)" if zoom_in else "if(lte(zoom,1.0),1.25,max(1.0,zoom-0.0008))"
    vf = (f"scale=3840:2160:force_original_aspect_ratio=increase,"
          f"crop=3840:2160,"
          f"zoompan=z='{direction}':d={frames}:s={STICK_W}x{STICK_H}:fps=25")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--voice", default="en-US-AriaNeural")
    ap.add_argument("--privacy", default="unlisted", choices=["unlisted", "public", "private"])
    ap.add_argument("--num-scenes", type=int, default=10)
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(__file__))
    from upload_youtube import upload_short

    workdir = tempfile.mkdtemp()
    try:
        content = generate_structured_content(args.topic, args.num_scenes)
        base_style = content["base_style"]
        scenes = content["scenes"]

        clip_paths = []
        scenes_with_durations = []
        for i, scene in enumerate(scenes, 1):
            audio_path = os.path.join(workdir, f"scene_{i}.mp3")
            image_path = os.path.join(workdir, f"scene_{i}.png")
            clip_path = os.path.join(workdir, f"scene_{i}.mp4")

            voiceover = scene.get("voiceover", "").strip()
            pose = scene.get("pose_description", "").strip()
            if not voiceover or not pose:
                print(f"Scene {i} missing content, skipping.")
                continue

            generate_narration(voiceover, audio_path, args.voice)
            dur = get_duration(audio_path)
            generate_stickman_image(base_style, pose, image_path)
            make_scene_clip(image_path, audio_path, dur, clip_path, zoom_in=(i % 2 == 1))

            clip_paths.append(clip_path)
            scenes_with_durations.append((voiceover, dur))

        if not clip_paths:
            print("No usable scenes were produced.")
            sys.exit(1)

        total_duration = sum(d for _, d in scenes_with_durations)
        print(f"Total narrated duration: {total_duration:.1f}s")

        combined_path = os.path.join(workdir, "combined.mp4")
        concat_clips(clip_paths, combined_path, workdir)

        srt_path = os.path.join(workdir, "captions.srt")
        build_srt(scenes_with_durations, srt_path)

        final_path = os.path.join(workdir, "final.mp4")
        burn_captions(combined_path, srt_path, final_path, video_w=STICK_W, video_h=STICK_H)

        full_script = " ".join(v for v, _ in scenes_with_durations)
        title = args.topic.strip()[:95]
        description_text = generate_description(args.topic, full_script)
        hashtags = generate_hashtags(args.topic, extra=())  # not a vertical Short, skip #Shorts
        desc = f"{description_text}\n\n{hashtags}".strip()

        video_id = upload_short(final_path, title, desc, privacy=args.privacy)
        print(f"Uploaded: https://youtube.com/watch?v={video_id}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
