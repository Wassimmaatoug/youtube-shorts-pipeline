# 🎬 YouTube Shorts Auto-Pipeline

Turns a long-form YouTube video into vertical Shorts and uploads them to your channel — running entirely on GitHub Actions' free tier. This README reflects the actual working setup, including every gotcha that came up along the way.

## How it works

```
YouTube URL ──▶ yt-dlp download ──▶ highlight detection ──▶ vertical crop ──▶ YouTube upload
                (with cookies +          (silence + scene-
                 JS challenge solver)      change heuristics)
```

1. **Download** — `yt-dlp` pulls the source video, authenticated with your cookies and a JS challenge solver (Deno) to get past YouTube's bot checks.
2. **Detect highlights** — `ffmpeg` finds candidate segments:
   - `silencedetect` keeps only segments where someone is actually talking
   - scene-change detection scores segments with more visual cuts higher
3. **Cut & reformat** — top-scoring, non-overlapping 20–58s segments get cropped to 1080×1920 vertical.
4. **Upload** — each clip goes to your channel via the YouTube Data API as unlisted/public Shorts.

## Two ways to run it

| Workflow | Trigger | Action needed from you |
|---|---|---|
| `.github/workflows/shorts_manual.yml` | Manual, you paste a link | One click per video |
| `.github/workflows/shorts_watch.yml` | Cron, every 6h | **None** — polls a channel's RSS feed and auto-processes new uploads |

---

## Full setup (do these in order)

### 1. Google Cloud project + OAuth client

1. [Google Cloud Console](https://console.cloud.google.com) → create a new project.
2. **APIs & Services → Library** → search "YouTube Data API v3" → **Enable**.
   > Skipping this causes: `YouTube Data API v3 has not been used in project ... or it is disabled`.
3. **APIs & Services → OAuth consent screen** → External → add yourself under **Test users**. Leave publishing status as "Testing" — fine for personal use, just means you may see an "unverified app" warning during login (expected, click through it).
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID** → Application type **Desktop app** → download the JSON → save it as `client_secret.json` next to `scripts/` locally.

### 2. Generate your refresh token (run locally, never in CI)

```bash
pip install google-auth-oauthlib
python scripts/get_refresh_token.py
```

- This opens a browser once for you to approve access.
- It writes **three files** in the same folder: `client_id.txt`, `client_secret.txt`, `refresh_token.txt` — each containing only that one value. Open each in a plain text editor, select all, copy — this avoids any terminal line-wrapping/copy errors.
- If you ever re-run this and get `invalid_request: Missing required parameter: refresh_token`, it means Google didn't re-issue one because you'd already granted access before. Fix: revoke access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions), then run the script again — it forces `prompt=consent` so this shouldn't recur.

### 3. Add GitHub repository secrets

**Settings → Secrets and variables → Actions → New repository secret.** You need all of these as **separate** secrets (a common mistake is only adding some of them):

| Secret name | From | Shape |
|---|---|---|
| `YT_CLIENT_ID` | `client_id.txt` | ends in `.apps.googleusercontent.com` |
| `YT_CLIENT_SECRET` | `client_secret.txt` | starts with `GOCSPX-`, ~24-35 chars |
| `YT_REFRESH_TOKEN` | `refresh_token.txt` | often starts with `1//`, 100+ chars |
| `YTDLP_COOKIES` | see step 4 below | multi-line Netscape cookie file |
| `WATCH_CHANNEL_ID` | your channel's URL | starts with `UC`, 24 chars — only needed for auto-watch mode |

Paste **only the value itself** — no label, no quotes, no extra whitespace.

### 4. Export cookies (needed to get past YouTube's bot detection)

YouTube blocks unauthenticated requests from datacenter IPs like GitHub Actions runners with "Sign in to confirm you're not a bot." Cookies from a real logged-in session fix this — but they rotate and expire, so:

1. Create a **dedicated browser profile used only for this** (Chrome: profile icon → Add → new profile). Never browse YouTube/Google in it day-to-day — that's what triggers rotation.
2. Install the **"Get cookies.txt LOCALLY"** extension in that profile.
3. Log into YouTube in that profile, go to youtube.com, immediately click the extension → export `cookies.txt`. Don't browse around first.
4. Open the file, select all, copy, paste as the `YTDLP_COOKIES` secret **right away**.
5. Close that profile until next time.

> **This will need repeating.** Google rotates these tokens on its own schedule (sometimes within a day), not just from browsing activity. When a run fails with `"cookies are no longer valid"`, that's your signal — repeat steps 1-4.

### 5. Grant the repo write access (auto-watch mode only)

`shorts_watch.yml` commits a small state file (`state/processed.json`) after each run, so it needs push access:

**Settings → Actions → General → Workflow permissions → "Read and write permissions" → Save.**

Without this, shorts get created fine but the workflow fails at the final `git push` step.

---

## Usage

**Manual mode** — Actions tab → *Create YouTube Shorts (manual link)* → Run workflow → paste a URL. Works with any public YouTube video, from any channel.

**Auto-watch mode** — set `WATCH_CHANNEL_ID` once (find it via your channel's "Share channel → Copy channel ID"); the scheduled workflow checks for new uploads every 6 hours and processes them automatically, no further action needed — aside from keeping cookies fresh (see above).

---

## Repo structure

```
scripts/
  pipeline.py           # download → detect → cut → upload
  upload_youtube.py      # YouTube API auth + upload (has debug logging for secret lengths)
  watch_channel.py       # RSS polling for the zero-action mode
  get_refresh_token.py   # one-time local OAuth setup, writes client_id.txt/client_secret.txt/refresh_token.txt
.github/workflows/
  shorts_manual.yml      # paste-a-link trigger
  shorts_watch.yml       # scheduled auto-watch trigger
state/processed.json     # tracks videos watch_channel.py has already handled
requirements.txt
```

## Tuning

| Setting | Location | Effect |
|---|---|---|
| `MIN_CLIP` / `MAX_CLIP` | `pipeline.py` | Clip length range (default 20–58s) |
| `threshold` in `scene_changes()` | `pipeline.py` | Sensitivity to visual cuts |
| `noise_db` / `min_silence` in `detect_silence()` | `pipeline.py` | How strict "silence" is |
| `MAX_PER_RUN` | `watch_channel.py` | New videos processed per scheduled run |

## Using other channels' videos

`shorts_manual.yml` works with any public URL as-is — no code change needed. For legal safety when the source isn't your own content, see the options ranked by how automation-friendly they are:

1. **Creative Commons (CC BY) videos** — filter YouTube search by Features → Creative Commons; must credit the creator.
2. **Public domain content** — e.g. NASA's channel, Archive.org.
3. **Direct permission** — ask the creator, get it in writing, credit them.
4. **Fair use** — riskiest, a legal defense argued after a claim, not automation-friendly.

## Limits (this is what "free" actually costs)

- **GitHub Actions**: unlimited minutes on standard runners for public repos; ~2,000 min/month free for private repos. 6-hour hard cap per job.
- **YouTube Data API quota**: 10,000 units/day by default; one upload = 1,600 units → **max ~6 Shorts/day** without a (free) quota increase request.
- **Content rights**: automated matching doesn't check intent — strikes land on *your* channel regardless of framing.
- **yt-dlp vs. YouTube is an ongoing arms race** — expect occasional breakage as YouTube changes its bot-detection. When that happens: check the failing step's log, it's almost always fixed by `pip install -U yt-dlp` picking up the latest release, or a small `--extractor-args` tweak.

## License

Add a license of your choice (MIT is a common default for personal tooling like this).
