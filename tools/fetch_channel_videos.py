"""
fetch_channel_videos.py — Fetch all public video IDs from the authenticated YouTube channel

Uses the uploads playlist (1 quota unit per page) rather than search.list (100 units).
Filters to public videos only so unlisted/private drafts are excluded.

Usage:
    python3 tools/fetch_channel_videos.py --output-file .tmp/channel_videos.json

Output JSON: list of {youtube_video_id, title, published_at, public_url}

Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

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


def get_uploads_playlist_id(youtube):
    """Get the uploads playlist ID for the authenticated channel."""
    response = youtube.channels().list(
        part="contentDetails",
        mine=True,
    ).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("No channel found for authenticated account")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def get_all_video_ids(youtube, playlist_id):
    """Paginate through the uploads playlist and return all video IDs."""
    video_ids = []
    next_page_token = None
    while True:
        kwargs = dict(part="contentDetails", playlistId=playlist_id, maxResults=50)
        if next_page_token:
            kwargs["pageToken"] = next_page_token
        response = youtube.playlistItems().list(**kwargs).execute()
        for item in response.get("items", []):
            vid_id = item["contentDetails"].get("videoId")
            if vid_id:
                video_ids.append(vid_id)
        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break
    return video_ids


def filter_public_videos(youtube, video_ids):
    """Return only public videos with their metadata, batched in 50s."""
    public = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        response = youtube.videos().list(
            part="snippet,status",
            id=",".join(batch),
        ).execute()
        for item in response.get("items", []):
            if item.get("status", {}).get("privacyStatus") == "public":
                snippet = item.get("snippet", {})
                vid_id = item["id"]
                public.append({
                    "youtube_video_id": vid_id,
                    "title": snippet.get("title", ""),
                    "published_at": snippet.get("publishedAt", ""),
                    "public_url": f"https://www.youtube.com/watch?v={vid_id}",
                })
    return public


def main():
    parser = argparse.ArgumentParser(description="Fetch all public videos from the channel")
    parser.add_argument("--output-file", required=True, help="Output JSON file path")
    args = parser.parse_args()

    print("Fetching channel videos from YouTube...", file=sys.stderr)

    youtube = get_youtube_service()

    try:
        playlist_id = get_uploads_playlist_id(youtube)
        print(f"  Uploads playlist: {playlist_id}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Could not get uploads playlist: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        all_ids = get_all_video_ids(youtube, playlist_id)
        print(f"  Total videos in uploads: {len(all_ids)}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Could not list playlist items: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        public_videos = filter_public_videos(youtube, all_ids)
        print(f"  Public videos: {len(public_videos)}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Could not filter public videos: {e}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_file)), exist_ok=True)
    with open(args.output_file, "w") as f:
        json.dump(public_videos, f, indent=2)

    for v in public_videos:
        print(f"  ✓ {v['youtube_video_id']} — {v['title'][:60]}", file=sys.stderr)

    print(args.output_file)


if __name__ == "__main__":
    main()
