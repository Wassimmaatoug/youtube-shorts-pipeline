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
# access_type=offline + prompt=consent force Google to issue a refresh_token
# every time, even if you've authorized this app before.
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

if not creds.refresh_token:
    raise SystemExit(
        "No refresh_token was returned. Revoke this app's access at "
        "https://myaccount.google.com/permissions and run this script again."
    )

# Write each value to its own file, containing ONLY that value, nothing else.
# This avoids any risk of grabbing the wrong text when copying from a
# terminal (wrapped lines, labels, extra whitespace, etc.).
with open("client_id.txt", "w") as f:
    f.write(creds.client_id)
with open("client_secret.txt", "w") as f:
    f.write(creds.client_secret)
with open("refresh_token.txt", "w") as f:
    f.write(creds.refresh_token)

print("\nWrote client_id.txt, client_secret.txt, refresh_token.txt in this folder.")
print("Open EACH file individually, select all (Ctrl+A / Cmd+A), copy, and paste")
print("as the matching GitHub secret. Do not copy from this terminal output.\n")
print(f"client_id length:     {len(creds.client_id)}")
print(f"client_secret length: {len(creds.client_secret)}")
print(f"refresh_token length: {len(creds.refresh_token)}")
