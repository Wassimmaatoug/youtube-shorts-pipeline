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
    creds = google.oauth2.credentials.Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return googleapiclient.discovery.build("youtube", "v3", credentials=creds)


def get_video_title(url):
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
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
