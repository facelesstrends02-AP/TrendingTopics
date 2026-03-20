"""
upload_thumbnail.py — Upload a custom thumbnail to a YouTube video

Uses the YouTube Data API v3 thumbnails.set endpoint.
Requires the video to already exist (uploaded, unlisted or public).
Quota cost: 50 units per call.

Usage:
    python3 tools/upload_thumbnail.py \
        --video-id abc123XYZ \
        --thumbnail-file .tmp/thumbnails/video_1_thumbnail.jpg

Exit code: 0 on success, 1 on failure
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
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


def main():
    parser = argparse.ArgumentParser(description="Upload custom thumbnail to YouTube video")
    parser.add_argument("--video-id", required=True, help="YouTube video ID")
    parser.add_argument("--thumbnail-file", required=True, help="Path to JPEG thumbnail file")
    args = parser.parse_args()

    if not os.path.exists(args.thumbnail_file):
        print(f"ERROR: Thumbnail file not found: {args.thumbnail_file}", file=sys.stderr)
        sys.exit(1)

    # YouTube thumbnail requirements: JPEG/PNG, max 2MB, min 640x360
    size_bytes = os.path.getsize(args.thumbnail_file)
    if size_bytes > 2 * 1024 * 1024:
        print(f"ERROR: Thumbnail file too large ({size_bytes // 1024}KB, max 2MB)", file=sys.stderr)
        sys.exit(1)

    youtube = get_youtube_service()

    print(f"Uploading thumbnail to video {args.video_id}...", file=sys.stderr)
    media = MediaFileUpload(args.thumbnail_file, mimetype="image/jpeg", resumable=False)

    try:
        youtube.thumbnails().set(
            videoId=args.video_id,
            media_body=media,
        ).execute()
    except Exception as e:
        print(f"ERROR: Thumbnail upload failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Thumbnail uploaded successfully to video {args.video_id}", file=sys.stderr)
    print(f"thumbnail_uploaded:{args.video_id}")


if __name__ == "__main__":
    main()
