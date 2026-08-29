"""
RUN THIS ONCE, LOCALLY (never in CI). It opens a browser for you to approve
upload access, then prints the values to put into GitHub Secrets.

Prereqs:
  1. Google Cloud Console -> new project -> enable "YouTube Data API v3"
  2. OAuth consent screen -> External -> add yourself as a test user
  3. Credentials -> Create OAuth client ID -> Application type: Desktop app
  4. Download the JSON, save it next to this script as client_secret.json
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\nAdd these as GitHub repo secrets (Settings -> Secrets and variables -> Actions):\n")
print("YT_CLIENT_ID     =", creds.client_id)
print("YT_CLIENT_SECRET =", creds.client_secret)
print("YT_REFRESH_TOKEN =", creds.refresh_token)
