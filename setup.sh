#!/bin/bash
# One-time setup script for the YouTube Channel Automation system
# Run this once after cloning / setting up the project

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== YouTube Channel Automation - Setup ==="
echo ""

# 1. Check for ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo "[1/5] Installing ffmpeg via Homebrew..."
    if ! command -v brew &>/dev/null; then
        echo "ERROR: Homebrew not found. Install it from https://brew.sh first."
        exit 1
    fi
    brew install ffmpeg
else
    echo "[1/5] ffmpeg already installed: $(ffmpeg -version 2>&1 | head -1)"
fi

# 2. Create virtual environment & install Python dependencies
echo ""
if [ ! -d "venv" ]; then
    echo "[2/5] Creating virtual environment & installing dependencies..."
    python3 -m venv venv
else
    echo "[2/5] Virtual environment exists. Installing dependencies..."
fi
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Create .tmp subdirectories
echo ""
echo "[3/5] Creating .tmp directories..."
mkdir -p .tmp/scripts .tmp/audio .tmp/footage .tmp/output .tmp/thumbnails

# 4. Check .env
echo ""
echo "[4/5] Checking .env..."
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found. Create it from the template in the README."
    exit 1
fi

REQUIRED_KEYS=(
    "ANTHROPIC_API_KEY"
    "OPENAI_API_KEY"
    "YOUTUBE_API_KEY"
    "PEXELS_API_KEY"
    "GMAIL_FROM"
    "APPROVAL_EMAIL"
    "NICHE"
    "CHANNEL_NAME"
)

MISSING=0
for key in "${REQUIRED_KEYS[@]}"; do
    value=$(grep "^${key}=" .env | cut -d'=' -f2-)
    if [ -z "$value" ]; then
        echo "  MISSING: $key"
        MISSING=1
    else
        echo "  OK: $key"
    fi
done

if [ "$MISSING" -eq 1 ]; then
    echo ""
    echo "ERROR: Fill in missing keys in .env before continuing."
    exit 1
fi

# 5. Run OAuth flow (opens browser)
echo ""
echo "[5/5] Running Google OAuth authentication flow..."
echo "      This will open your browser. Sign in and grant all requested permissions."
echo "      A token.json file will be created automatically."
echo ""
# Ensure venv is active for the OAuth step
source venv/bin/activate
python3 - <<'PYEOF'
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import json

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

creds = None
token_path = "token.json"
creds_path = "credentials.json"

if not os.path.exists(creds_path):
    print("ERROR: credentials.json not found.")
    print("Download it from Google Cloud Console > APIs & Services > Credentials.")
    sys.exit(1)

if os.path.exists(token_path):
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)

if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        print("Token refreshed successfully.")
    else:
        flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
        creds = flow.run_local_server(port=0)
        print("OAuth flow completed. token.json created.")
    with open(token_path, "w") as f:
        f.write(creds.to_json())
else:
    print("Existing valid token found.")

print("Authentication successful!")
PYEOF

echo ""
echo "=== Setup Complete ==="
echo ""
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"
echo "Next steps:"
echo "  1. Activate the venv:  source venv/bin/activate"
echo "  2. Run a manual test:  python3 agents/idea_agent.py"
echo "  3. Set up cron:        crontab -e"
echo "     Add these lines:"
echo "     30 14 * * 0  cd $SCRIPT_DIR && $VENV_PYTHON agents/analytics_agent.py >> .tmp/cron.log 2>&1"
echo "     30 15 * * 0  cd $SCRIPT_DIR && $VENV_PYTHON agents/idea_agent.py >> .tmp/cron.log 2>&1"
echo "     */30 * * * * cd $SCRIPT_DIR && $VENV_PYTHON agents/approval_poller.py >> .tmp/cron.log 2>&1"
echo ""
echo "  4. Grant cron Full Disk Access:"
echo "     System Preferences > Privacy & Security > Full Disk Access > add /usr/sbin/cron"
