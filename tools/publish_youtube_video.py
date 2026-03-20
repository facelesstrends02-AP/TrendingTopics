"""
publish_youtube_video.py — Change a YouTube video from unlisted to public

Usage:
    python3 tools/publish_youtube_video.py --video-id dQw4w9WgXcQ

Output (stdout): Public YouTube URL
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


def wait_for_processing(service, video_id, max_wait=600):
    """Poll until video processing is complete. Returns True if ready."""
    print(f"Waiting for YouTube to finish processing video {video_id}...", file=sys.stderr)
    start = time.time()
    while time.time() - start < max_wait:
        resp = service.videos().list(part="processingDetails,status", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            print("WARNING: Video not found in API response.", file=sys.stderr)
            return False
        processing = items[0].get("processingDetails", {}).get("processingStatus", "")
        if processing == "succeeded":
            return True
        elif processing == "failed":
            print("ERROR: YouTube video processing failed.", file=sys.stderr)
            return False
        print(f"  Processing status: {processing}. Waiting 30s...", file=sys.stderr)
        time.sleep(30)
    print("WARNING: Timeout waiting for processing. Attempting publish anyway.", file=sys.stderr)
    return True


def main():
    parser = argparse.ArgumentParser(description="Publish YouTube video (unlisted → public or scheduled)")
    parser.add_argument("--video-id", required=True, help="YouTube video ID")
    parser.add_argument("--publish-at", default=None,
                        help="Schedule publish at this UTC ISO 8601 time (e.g. 2026-03-16T03:30:00Z). "
                             "If omitted, publishes immediately.")
    parser.add_argument("--skip-processing-check", action="store_true",
                        help="Skip waiting for YouTube processing")
    args = parser.parse_args()

    service = get_youtube_service()

    if not args.skip_processing_check:
        ready = wait_for_processing(service, args.video_id)
        if not ready:
            print(f"ERROR: Video {args.video_id} not ready to publish.", file=sys.stderr)
            sys.exit(1)

    if args.publish_at:
        print(f"Scheduling video {args.video_id} for {args.publish_at}...", file=sys.stderr)
        status_body = {
            "privacyStatus": "private",
            "publishAt": args.publish_at,
            "selfDeclaredMadeForKids": False,
        }
    else:
        print(f"Publishing video {args.video_id} immediately...", file=sys.stderr)
        status_body = {"privacyStatus": "public", "selfDeclaredMadeForKids": False}

    try:
        service.videos().update(
            part="status",
            body={"id": args.video_id, "status": status_body},
        ).execute()
    except HttpError as e:
        if e.resp.status == 403:
            print(f"ERROR: Permission denied. Ensure your OAuth has youtube scope.", file=sys.stderr)
        else:
            print(f"ERROR: YouTube API error: {e}", file=sys.stderr)
        sys.exit(1)

    public_url = f"https://www.youtube.com/watch?v={args.video_id}"
    if args.publish_at:
        print(f"Scheduled: {public_url} → goes public at {args.publish_at}", file=sys.stderr)
    else:
        print(f"Published: {public_url}", file=sys.stderr)
    print(public_url)


if __name__ == "__main__":
    main()
