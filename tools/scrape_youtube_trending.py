"""
scrape_youtube_trending.py — Fetch trending YouTube videos for a niche using YouTube Data API v3

Usage:
    python3 tools/scrape_youtube_trending.py --niche "Self Development" --max-results 50

Output: .tmp/scraped_videos.json
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
OUTPUT_PATH = os.path.join(PROJECT_ROOT, ".tmp", "scraped_videos.json")

YT_API_BASE = "https://www.googleapis.com/youtube/v3"


def search_videos(api_key, query, published_after, max_results=50):
    """Search for videos using YouTube Data API v3."""
    params = {
        "key": api_key,
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": "viewCount",
        "publishedAfter": published_after,
        "maxResults": min(max_results, 50),
        "relevanceLanguage": "en",
        "videoDuration": "medium",  # 4-20 minutes — typical YouTube content
    }
    resp = requests.get(f"{YT_API_BASE}/search", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def get_video_stats(api_key, video_ids):
    """Batch fetch statistics and details for a list of video IDs."""
    if not video_ids:
        return {}
    params = {
        "key": api_key,
        "part": "statistics,snippet,contentDetails",
        "id": ",".join(video_ids),
    }
    resp = requests.get(f"{YT_API_BASE}/videos", params=params, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return {item["id"]: item for item in items}


def search_top_channels(api_key, niche, max_results=5):
    """Find top channel IDs for a niche."""
    params = {
        "key": api_key,
        "part": "snippet",
        "q": niche,
        "type": "channel",
        "order": "viewCount",
        "maxResults": max_results,
    }
    resp = requests.get(f"{YT_API_BASE}/search", params=params, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [item["snippet"]["channelId"] for item in items]


def get_channel_recent_videos(api_key, channel_id, max_results=5, published_after=None):
    """Get recent videos from a specific channel."""
    params = {
        "key": api_key,
        "part": "snippet",
        "channelId": channel_id,
        "type": "video",
        "order": "date",
        "maxResults": max_results,
    }
    if published_after:
        params["publishedAfter"] = published_after
    resp = requests.get(f"{YT_API_BASE}/search", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def normalize_video(video_id, snippet, stats):
    """Convert raw API data into a clean dict."""
    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""),
        "description": snippet.get("description", "")[:500],
        "tags": snippet.get("tags", [])[:10],
        "published_at": snippet.get("publishedAt", ""),
        "views": int(stats.get("viewCount", 0) or 0),
        "likes": int(stats.get("likeCount", 0) or 0),
        "comments": int(stats.get("commentCount", 0) or 0),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Scrape YouTube trending videos for a niche")
    parser.add_argument("--niche", required=True, help='Niche to search, e.g. "Self Development"')
    parser.add_argument("--max-results", type=int, default=50)
    args = parser.parse_args()

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("ERROR: YOUTUBE_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    # Look back 14 days for recent content
    published_after = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Searching YouTube for: '{args.niche}' (last 14 days)...", file=sys.stderr)

    all_videos = []
    seen_ids = set()

    # --- Main niche search ---
    search_queries = [
        args.niche,
        f"{args.niche} tips",
        f"{args.niche} motivation",
        f"how to {args.niche.lower()}",
    ]

    for query in search_queries:
        try:
            items = search_videos(api_key, query, published_after, max_results=25)
            for item in items:
                vid_id = item.get("id", {}).get("videoId")
                if vid_id and vid_id not in seen_ids:
                    seen_ids.add(vid_id)
                    all_videos.append(vid_id)
        except requests.HTTPError as e:
            print(f"WARNING: Search failed for '{query}': {e}", file=sys.stderr)
            if "quotaExceeded" in str(e):
                print("ERROR: YouTube API quota exceeded.", file=sys.stderr)
                sys.exit(1)

    # --- Top channel recent videos ---
    print(f"Finding top channels for '{args.niche}'...", file=sys.stderr)
    try:
        channel_ids = search_top_channels(api_key, args.niche, max_results=5)
        for channel_id in channel_ids:
            try:
                items = get_channel_recent_videos(api_key, channel_id, max_results=5, published_after=published_after)
                for item in items:
                    vid_id = item.get("id", {}).get("videoId")
                    if vid_id and vid_id not in seen_ids:
                        seen_ids.add(vid_id)
                        all_videos.append(vid_id)
            except Exception as e:
                print(f"WARNING: Could not fetch channel {channel_id}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: Channel search failed: {e}", file=sys.stderr)

    if not all_videos:
        print("ERROR: No videos found.", file=sys.stderr)
        sys.exit(1)

    # Trim to max_results
    all_videos = all_videos[:args.max_results]

    # --- Batch fetch stats ---
    print(f"Fetching stats for {len(all_videos)} videos...", file=sys.stderr)
    stats_map = {}
    batch_size = 50
    for i in range(0, len(all_videos), batch_size):
        batch = all_videos[i:i + batch_size]
        try:
            stats_map.update(get_video_stats(api_key, batch))
        except Exception as e:
            print(f"WARNING: Stats fetch failed for batch {i}: {e}", file=sys.stderr)

    # --- Build final output ---
    results = []
    for vid_id in all_videos:
        item = stats_map.get(vid_id)
        if not item:
            continue
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        results.append(normalize_video(vid_id, snippet, stats))

    # Sort by views descending
    results.sort(key=lambda x: x["views"], reverse=True)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Scraped {len(results)} videos → {OUTPUT_PATH}", file=sys.stderr)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
