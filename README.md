# YouTube Shorts Auto-Pipeline (zero infra cost)

Turns a long video into vertical Shorts and uploads them to your YouTube
channel, running entirely on GitHub Actions' free tier.

## What it does
1. `yt-dlp` downloads the source video.
2. `ffmpeg` finds candidate segments using two free heuristics:
   - **silencedetect** → keeps only segments where someone is actually talking
   - **scene-change detection** → scores segments with more visual cuts higher (proxy for "energetic" moments)
3. Top-scoring, non-overlapping 20–58s segments get cropped to 1080x1920 (vertical).
4. Each clip is uploaded via the YouTube Data API as an unlisted/public Short.

Two ways to trigger it:
| Workflow | Trigger | Action needed from you |
|---|---|---|
| `shorts_manual.yml` | You run it, paste a link | One click per video |
| `shorts_watch.yml` | Cron, every 6h | **None** — polls a channel's RSS feed and auto-processes new uploads |

## One-time setup (~10 minutes, unavoidable — Google requires human consent for upload scopes)

1. **Google Cloud Console** → new project → enable **YouTube Data API v3**.
2. **OAuth consent screen** → External → add yourself as a test user (or verify the app if you want >100 users, not needed for personal use).
3. **Credentials** → Create OAuth client ID → Application type: **Desktop app** → download JSON as `client_secret.json`.
4. Locally:
   ```bash
   pip install google-auth-oauthlib
   python scripts/get_refresh_token.py
   ```
   This opens a browser once, then prints your `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
5. In your GitHub repo: **Settings → Secrets and variables → Actions**, add:
   - `YT_CLIENT_ID`
   - `YT_CLIENT_SECRET`
   - `YT_REFRESH_TOKEN`
   - `WATCH_CHANNEL_ID` (only if using the auto-watch workflow — found in a channel's page source or via `https://www.youtube.com/@handle/about`)
6. Push this repo to GitHub. Done — from here it's genuinely hands-off.

## Limits to know about (this is what "zero cost" actually means)
- **GitHub Actions**: free unlimited minutes on standard runners for **public** repos; ~2000 min/month free on private repos. Each job also has a 6-hour hard cap — fine for normal-length source videos, but very long sources (multi-hour streams) may not finish.
- **YouTube Data API quota**: 10,000 units/day by default; one upload = 1,600 units → **max ~6 Shorts/day** before you'd need to request a quota increase from Google (also free, but requires an audit form).
- **Content**: if you're clipping videos you don't own, you're relying on fair use / permission — YouTube's Content ID doesn't check intent, only the automated system's own matching, and strikes go against *your* channel.

## Files
```
scripts/pipeline.py          # download -> detect -> cut -> upload
scripts/upload_youtube.py    # YouTube API auth + upload
scripts/watch_channel.py     # RSS polling for the zero-action mode
scripts/get_refresh_token.py # one-time local OAuth setup
.github/workflows/           # the two triggers described above
state/processed.json         # tracks what watch_channel.py has already handled
```

## Tuning
- `MIN_CLIP` / `MAX_CLIP` in `pipeline.py` control clip length (default 20–58s).
- `threshold` in `scene_changes()` controls sensitivity to visual cuts.
- `noise_db` / `min_silence` in `detect_silence()` control how strict "silence" is — tighten if it's picking up background music as speech.
"# youtube-shorts-pipeline" 
