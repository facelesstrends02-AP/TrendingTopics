"""
fetch_video_analytics.py — Fetch performance stats for published YouTube videos

Uses YouTube Data API v3 videos.list to get views, likes, comments per video.
Computes engagement_rate = (likes + comments) / views.

Usage:
    python3 tools/fetch_video_analytics.py \
        --video-ids "abc123,def456,ghi789" \
        --output-file .tmp/analytics_2026-03-09.json

Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

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


def fetch_stats(youtube, video_ids):
    """Fetch statistics and snippet for a list of video IDs (batched in 50s)."""
    results = []
    # YouTube API allows up to 50 IDs per request
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        response = youtube.videos().list(
            part="statistics,snippet",
            id=",".join(batch),
        ).execute()

        for item in response.get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})

            views = int(stats.get("viewCount", 0))
            likes = int(stats.get("likeCount", 0))
            comments = int(stats.get("commentCount", 0))
            engagement_rate = round((likes + comments) / views, 4) if views > 0 else 0.0

            published_at = snippet.get("publishedAt", "")
            published_week = published_at[:10] if published_at else ""

            results.append({
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "published_at": published_at,
                "published_week": published_week,
                "view_count": views,
                "like_count": likes,
                "comment_count": comments,
                "engagement_rate": engagement_rate,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })

    return results


def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube video analytics")
    parser.add_argument("--video-ids", required=True, help="Comma-separated YouTube video IDs")
    parser.add_argument("--output-file", required=True, help="Output JSON file path")
    args = parser.parse_args()

    video_ids = [v.strip() for v in args.video_ids.split(",") if v.strip()]
    if not video_ids:
        print("ERROR: No video IDs provided", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching analytics for {len(video_ids)} video(s)...", file=sys.stderr)

    youtube = get_youtube_service()

    try:
        analytics = fetch_stats(youtube, video_ids)
    except Exception as e:
        print(f"ERROR: Failed to fetch video stats: {e}", file=sys.stderr)
        sys.exit(1)

    if not analytics:
        print("WARNING: No analytics data returned (videos may be private or deleted)", file=sys.stderr)

    # Sort by views descending
    analytics.sort(key=lambda v: v["view_count"], reverse=True)

    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
    with open(args.output_file, "w") as f:
        json.dump(analytics, f, indent=2)

    print(f"Analytics for {len(analytics)} video(s) saved to {args.output_file}", file=sys.stderr)
    for v in analytics:
        print(
            f"  {v['view_count']:,} views | {v['engagement_rate']:.2%} engagement | \"{v['title'][:50]}\"",
            file=sys.stderr,
        )

    print(args.output_file)


if __name__ == "__main__":
    main()
