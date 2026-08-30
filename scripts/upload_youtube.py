"""
Upload helper. Auth comes entirely from environment variables (GitHub Actions
secrets) — no browser interaction needed at runtime. The refresh token is
generated ONCE, locally, via get_refresh_token.py.
"""
import os

import google.oauth2.credentials
import googleapiclient.discovery
import googleapiclient.http
import yt_dlp


def get_service():
    client_id = os.environ.get("YT_CLIENT_ID", "")
    client_secret = os.environ.get("YT_CLIENT_SECRET", "")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN", "")

    # Sanity-check lengths only (never print the actual secret values).
    print(f"[auth debug] YT_CLIENT_ID length={len(client_id)} "
          f"ends_correctly={client_id.endswith('.apps.googleusercontent.com')}")
    print(f"[auth debug] YT_CLIENT_SECRET length={len(client_secret)}")
    print(f"[auth debug] YT_REFRESH_TOKEN length={len(refresh_token)}")

    missing = [name for name, val in [
        ("YT_CLIENT_ID", client_id),
        ("YT_CLIENT_SECRET", client_secret),
        ("YT_REFRESH_TOKEN", refresh_token),
    ] if not val or len(val) < 10]
    if missing:
        raise RuntimeError(
            f"These secrets are missing or look too short to be real: {missing}. "
            "Check GitHub repo Settings -> Secrets and variables -> Actions."
        )

    creds = google.oauth2.credentials.Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return googleapiclient.discovery.build("youtube", "v3", credentials=creds)


def get_video_title(url, cookies_path=None):
    opts = {"quiet": True}
    if cookies_path:
        opts["cookiefile"] = cookies_path
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("title", "Short")


def upload_short(path, title, description, privacy="unlisted"):
    yt = get_service()
    body = {
        "snippet": {"title": title, "description": description, "categoryId": "22"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = googleapiclient.http.MediaFileUpload(path, chunksize=-1, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    return response.get("id")
