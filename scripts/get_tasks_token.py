"""
Helper script to generate OAuth2 Refresh Token for personal @gmail.com Google Tasks access.
Run this script using the virtual environment:
    ./venv/bin/python scripts/get_tasks_token.py
"""

import sys
import os
import glob
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar"
]

def find_client_secret_file():
    patterns = [
        "secrets/client_secret*.json",
        "client_secret*.json",
        "secrets/*.json",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        for m in matches:
            try:
                with open(m, "r") as f:
                    data = json.load(f)
                    if "installed" in data or "web" in data:
                        return m, data
            except Exception:
                continue
    return None, None

def main():
    print("=" * 70)
    print(" Google OAuth 2.0 Refresh Token Generator (For Personal Gmail Tasks)")
    print("=" * 70)

    secret_file, secret_data = find_client_secret_file()
    client_id = None
    client_secret = None

    if secret_file and secret_data:
        cfg = secret_data.get("installed") or secret_data.get("web")
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        print(f" Detected OAuth client file: {secret_file}")
        print(f" Client ID: {client_id[:20]}...")
    else:
        print("\nInstructions:")
        print("1. Go to Google Cloud Console -> APIs & Services -> Credentials.")
        print("2. Create Credentials -> OAuth Client ID -> Application Type: 'Desktop app'.")
        print("3. Download or view your Client ID and Client Secret.\n")
        client_id = input("Enter your OAuth Client ID: ").strip()
        client_secret = input("Enter your OAuth Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Error: Both Client ID and Client Secret are required.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]
        }
    }

    try:
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        print("\nOpening local authorization server... Please authorize in your browser window.")
        print("If a browser doesn't open automatically, look for the authorization URL below.")
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

        print("\n" + "=" * 70)
        print(" SUCCESS! Generated Refresh Token:")
        print("=" * 70)
        print(f"GOOGLE_CLIENT_ID={client_id}")
        print(f"GOOGLE_CLIENT_SECRET={client_secret}")
        print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
        print("=" * 70)

        # Update .env if present
        env_path = ".env"
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                env_content = f.read()

            def update_or_append(content, key, val):
                lines = content.splitlines()
                found = False
                new_lines = []
                for line in lines:
                    if line.strip().startswith(f"{key}=") or line.strip().startswith(f"# {key}="):
                        new_lines.append(f"{key}={val}")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f"{key}={val}")
                return "\n".join(new_lines) + "\n"

            env_content = update_or_append(env_content, "GOOGLE_CLIENT_ID", client_id)
            env_content = update_or_append(env_content, "GOOGLE_CLIENT_SECRET", client_secret)
            env_content = update_or_append(env_content, "GOOGLE_REFRESH_TOKEN", creds.refresh_token)

            with open(env_path, "w", encoding="utf-8") as f:
                f.write(env_content)
            print(" Automatically updated .env with your OAuth credentials!")

    except Exception as e:
        print(f"\nAuthorization failed: {e}")

if __name__ == "__main__":
    main()
