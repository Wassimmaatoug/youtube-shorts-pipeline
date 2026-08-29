# 🎬 YouTube Shorts Auto-Pipeline

Turn a long-form YouTube video into vertical Shorts and auto-upload them to your channel — running entirely on GitHub Actions' free tier, no paid infra, no server to maintain.

## How it works

```
YouTube URL ──▶ yt-dlp download ──▶ highlight detection ──▶ vertical crop ──▶ YouTube upload
                                     (silence + scene-change
                                      heuristics via ffmpeg)
```

1. **Download** — `yt-dlp` pulls the source video.
2. **Detect highlights** — `ffmpeg` finds candidate segments:
   - `silencedetect` keeps only segments where someone is actually talking
   - scene-change detection scores segments with more visual cuts higher (proxy for "energetic" moments)
3. **Cut & reformat** — top-scoring, non-overlapping 20–58s segments get cropped to 1080×1920 vertical.
4. **Upload** — each clip goes to your channel via the YouTube Data API as unlisted/public Shorts.

## Two ways to run it

| Workflow | Trigger | Action needed from you |
|---|---|---|
| `.github/workflows/shorts_manual.yml` | Manual, you paste a link | One click per video |
| `.github/workflows/shorts_watch.yml` | Cron, every 6h | **None** — polls a channel's RSS feed and auto-processes new uploads |

## Setup (one-time, ~10 minutes)

Google requires human consent for upload permissions, so this step can't be automated away — it only needs doing once.

1. **Google Cloud Console** → new project → enable **YouTube Data API v3**.
2. **OAuth consent screen** → External → add yourself under **Test users** (leave publishing status as "Testing" for personal use).
3. **Credentials** → Create OAuth client ID → Application type **Desktop app** → download JSON as `client_secret.json`, place it in `scripts/`.
4. Locally:
   ```bash
   pip install google-auth-oauthlib
   python scripts/get_refresh_token.py
   ```
   Approve access in the browser (you'll see an "unverified app" warning — click **Advanced → Go to youtube_shorts (unsafe)**, that's expected while the app is in Testing mode). The script prints your credentials.
5. In your GitHub repo → **Settings → Secrets and variables → Actions**, add:
   - `YT_CLIENT_ID`
   - `YT_CLIENT_SECRET`
   - `YT_REFRESH_TOKEN`
   - `WATCH_CHANNEL_ID` *(only needed for the auto-watch workflow)*
6. Push. From here it's hands-off.

> ⚠️ While the OAuth app stays in "Testing" mode, refresh tokens expire after 7 days — you'll need to re-run step 4 weekly, or publish the app in the OAuth consent screen for long-term unattended use.

## Repo structure

```
scripts/
  pipeline.py           # download → detect → cut → upload
  upload_youtube.py      # YouTube API auth + upload
  watch_channel.py       # RSS polling for the zero-action mode
  get_refresh_token.py   # one-time local OAuth setup
.github/workflows/
  shorts_manual.yml      # paste-a-link trigger
  shorts_watch.yml       # scheduled auto-watch trigger
state/processed.json     # tracks videos watch_channel.py has already handled
requirements.txt
```

## Usage

**Manual mode** — GitHub repo → Actions → *Create YouTube Shorts (manual link)* → Run workflow → paste a URL.

**Auto-watch mode** — set `WATCH_CHANNEL_ID` once; the scheduled workflow checks for new uploads every 6 hours and processes them automatically.

## Tuning

| Setting | Location | Effect |
|---|---|---|
| `MIN_CLIP` / `MAX_CLIP` | `pipeline.py` | Clip length range (default 20–58s) |
| `threshold` in `scene_changes()` | `pipeline.py` | Sensitivity to visual cuts |
| `noise_db` / `min_silence` in `detect_silence()` | `pipeline.py` | How strict "silence" is — tighten if background music is being read as speech |
| `MAX_PER_RUN` | `watch_channel.py` | How many new videos to process per scheduled run |

## Limits (this is what "free" actually costs)

- **GitHub Actions**: unlimited minutes on standard runners for public repos; ~2,000 min/month free for private repos. 6-hour hard cap per job.
- **YouTube Data API quota**: 10,000 units/day by default; one upload = 1,600 units → **max ~6 Shorts/day** without requesting a (free) quota increase.
- **Content rights**: if you're clipping videos you don't own, you're relying on fair use or permission — YouTube's automated matching doesn't check intent, and strikes land on *your* channel.

## License

Add a license of your choice (MIT is a common default for personal tooling like this).
