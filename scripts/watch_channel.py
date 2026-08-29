"""
Polls a channel's public RSS feed (no API quota cost) for new uploads and
runs the pipeline on any video not yet processed. State is committed back
to the repo by the workflow so re-runs don't reprocess old videos.
"""
import json
import os
import subprocess

import feedparser

STATE_FILE = "state/processed.json"
CHANNEL_ID = os.environ["WATCH_CHANNEL_ID"]
MAX_PER_RUN = 1  # keep this low to respect the ~6 uploads/day API quota


def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return {"processed": []}


def save_state(state):
    os.makedirs("state", exist_ok=True)
    json.dump(state, open(STATE_FILE, "w"), indent=2)


def main():
    feed = feedparser.parse(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    )
    state = load_state()
    processed = set(state["processed"])
    new_entries = [e for e in feed.entries if e.yt_videoid not in processed]

    for e in new_entries[:MAX_PER_RUN]:
        print(f"New video found: {e.title} ({e.link})")
        subprocess.run(
            ["python", "scripts/pipeline.py", "--url", e.link,
             "--num-clips", "3", "--privacy", "unlisted"],
            check=True,
        )
        processed.add(e.yt_videoid)

    state["processed"] = list(processed)
    save_state(state)


if __name__ == "__main__":
    main()
