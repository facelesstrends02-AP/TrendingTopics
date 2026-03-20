"""
upload_to_youtube.py — Upload a video to YouTube using OAuth2 (resumable upload)

Usage:
    python3 tools/upload_to_youtube.py \
        --video-file .tmp/output/video_1_final.mp4 \
        --script-file .tmp/scripts/video_1_script.json \
        --privacy unlisted

    # Or pass title/description directly (for Shorts):
    python3 tools/upload_to_youtube.py \
        --video-file .tmp/shorts/video_1_short_0.mp4 \
        --title "BREAKING: Major Event Explained #Shorts" \
        --description "Quick breakdown of today's top story." \
        --privacy private

Output (stdout): "<video_id> <video_url>" on a single line
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/spreadsheets",
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "token.json")

# YouTube API upload quota: 1600 units per upload
# Daily quota: 10,000 units → max ~6 uploads/day


def get_youtube_service():
    if not os.path.exists(TOKEN_PATH):
        print("ERROR: token.json not found. Run setup.sh first.", file=sys.stderr)
        sys.exit(1)
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_video(service, video_path, title, description, tags, category_id, privacy):
    """Upload video using resumable upload. Returns video_id."""
    body = {
        "snippet": {
            "title": title[:100],  # YouTube max title length
            "description": description[:5000],
            "tags": tags[:500],  # YouTube tags limit
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10MB chunks
    )

    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    retry_count = 0
    max_retries = 5

    print(f"Uploading '{title}'...", file=sys.stderr)
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"  Upload progress: {pct}%", file=sys.stderr)
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retry_count < max_retries:
                retry_count += 1
                wait = 2 ** retry_count
                print(f"  Server error {e.resp.status}, retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
            elif e.resp.status == 403:
                error_reason = json.loads(e.content).get("error", {}).get("errors", [{}])[0].get("reason", "")
                if "quotaExceeded" in error_reason:
                    print("ERROR: YouTube API quota exceeded. Wait 24h or reduce upload frequency.", file=sys.stderr)
                else:
                    print(f"ERROR: Forbidden: {e}", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"ERROR: Upload failed: {e}", file=sys.stderr)
                sys.exit(1)

    return response["id"]


def main():
    parser = argparse.ArgumentParser(description="Upload video to YouTube")
    parser.add_argument("--video-file", required=True)
    parser.add_argument("--script-file", default=None, help="Script JSON (provides title/description/tags; optional if --title given)")
    parser.add_argument("--title", default=None, help="Video title (required if --script-file omitted)")
    parser.add_argument("--description", default=None, help="Video description override")
    parser.add_argument("--privacy", default="unlisted", choices=["public", "unlisted", "private"])
    args = parser.parse_args()

    if args.script_file is None and args.title is None:
        print("ERROR: Must provide either --script-file or --title", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.video_file):
        print(f"ERROR: Video not found: {args.video_file}", file=sys.stderr)
        sys.exit(1)

    if args.script_file is not None:
        if not os.path.exists(args.script_file):
            print(f"ERROR: Script not found: {args.script_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.script_file) as f:
            script = json.load(f)
        title = args.title or script.get("title", "Untitled Video")
        description = args.description or script.get("description", "")
        tags = script.get("tags", [])
        category_id = script.get("category_id", "26")
    else:
        title = args.title
        description = args.description or ""
        tags = []
        category_id = "26"

    if not description:
        description = f"{title}\n\nSubscribe for more {os.getenv('NICHE', 'trending news')} content.\n"

    service = get_youtube_service()

    video_id = upload_video(
        service=service,
        video_path=args.video_file,
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy=args.privacy,
    )

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"Upload complete: {video_url}", file=sys.stderr)
    print(f"{video_id} {video_url}")


if __name__ == "__main__":
    main()
