"""
fetch_pexels_footage.py — Download stock video clips from Pexels for each script segment

Usage:
    python3 tools/fetch_pexels_footage.py \
        --script-file .tmp/scripts/video_1_script.json \
        --output-dir .tmp/footage/video_1/

Output:
    .tmp/footage/video_1/clip_001_0.mp4  (up to 3 per segment)
    .tmp/footage/video_1/footage_manifest.json
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

PEXELS_API_BASE = "https://api.pexels.com/videos"
QUERIES_PER_SEGMENT = 3  # max clips to fetch per segment


def search_pexels_videos(api_key, query, per_page=5):
    """Search Pexels for videos matching a query."""
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "landscape",
        "size": "large",  # min 1920x1080
    }
    resp = requests.get(f"{PEXELS_API_BASE}/search", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("videos", [])


def pick_best_clip(videos, min_duration=10):
    """
    Pick the best video clip:
    - Prefer duration >= min_duration seconds
    - Prefer highest resolution (HD/Full HD)
    - Fall back to best available if none meet criteria
    """
    def score(video):
        duration = video.get("duration", 0)
        # Find best quality file
        best_width = 0
        for f in video.get("video_files", []):
            w = f.get("width", 0) or 0
            if w > best_width:
                best_width = w
        duration_ok = 1 if duration >= min_duration else 0
        return (duration_ok, best_width, duration)

    if not videos:
        return None, None

    videos_sorted = sorted(videos, key=score, reverse=True)
    best_video = videos_sorted[0]

    # Get best quality file URL
    files = best_video.get("video_files", [])
    if not files:
        return None, None

    # Prefer HD (1920x1080), fallback to largest available
    hd_files = [f for f in files if f.get("width", 0) and f.get("width", 0) >= 1280]
    target_files = hd_files if hd_files else files
    target_files.sort(key=lambda f: f.get("width", 0) or 0, reverse=True)

    return best_video, target_files[0].get("link")


def simplify_query(query):
    """Remove adjectives and adverbs from query as fallback."""
    # Simple heuristic: remove common descriptive words
    stopwords = {"beautiful", "amazing", "inspiring", "motivating", "happy", "sad",
                 "successful", "positive", "negative", "bright", "dark", "young", "old",
                 "slow", "fast", "busy", "calm", "peaceful", "energetic"}
    words = query.split()
    filtered = [w for w in words if w.lower() not in stopwords]
    return " ".join(filtered) if filtered else query


def get_queries_for_segment(seg):
    """
    Return a list of search queries for a segment.
    Supports both new pexels_search_queries (list) and old pexels_search_query (str).
    """
    queries = seg.get("pexels_search_queries")
    if isinstance(queries, list) and queries:
        return [q.strip() for q in queries[:QUERIES_PER_SEGMENT] if q.strip()]
    # Backward compat: old single-string format
    single = seg.get("pexels_search_query", "").strip()
    return [single] if single else []


def search_with_fallback(api_key, query, niche):
    """
    Search Pexels with fallback strategies.
    Returns list of video results (may be empty).
    """
    videos = []

    # Try original query first
    try:
        videos = search_pexels_videos(api_key, query, per_page=5)
        time.sleep(0.3)  # Respect rate limits
    except requests.HTTPError as e:
        if e.response.status_code == 429:
            print("  Rate limit hit. Waiting 60 seconds...", file=sys.stderr)
            time.sleep(60)
            try:
                videos = search_pexels_videos(api_key, query, per_page=5)
            except Exception:
                pass
        else:
            print(f"  WARNING: Pexels search failed: {e}", file=sys.stderr)

    # Fallback: simplified query
    if not videos:
        simplified = simplify_query(query)
        if simplified != query:
            print(f"  Fallback query: '{simplified}'", file=sys.stderr)
            try:
                videos = search_pexels_videos(api_key, simplified, per_page=5)
                time.sleep(0.3)
            except Exception as e:
                print(f"  WARNING: Fallback search failed: {e}", file=sys.stderr)

    # Last resort: use niche keyword
    if not videos:
        print(f"  Last resort query: '{niche}'", file=sys.stderr)
        try:
            videos = search_pexels_videos(api_key, niche, per_page=5)
            time.sleep(0.3)
        except Exception:
            pass

    return videos


def download_clip(url, output_path, max_retries=3):
    """Download a video clip to disk."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Download attempt {attempt+1} failed: {e}. Retrying...", file=sys.stderr)
                time.sleep(2)
            else:
                print(f"  ERROR: Download failed after {max_retries} attempts: {e}", file=sys.stderr)
                return False
    return False


def main():
    parser = argparse.ArgumentParser(description="Fetch Pexels stock footage for script segments")
    parser.add_argument("--script-file", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    if not os.path.exists(args.script_file):
        print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.script_file) as f:
        script = json.load(f)

    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        print("ERROR: PEXELS_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    niche = os.getenv("CHANNEL_NAME", "trending topics")
    segments = script.get("segments", [])
    if not segments:
        print("ERROR: Script has no segments.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    manifest = {}
    failed_segments = []

    # Cache: query -> clip_path (avoid re-downloading same query)
    query_cache = {}

    for seg in segments:
        seg_id = seg.get("segment_id", 0)
        queries = get_queries_for_segment(seg)

        if not queries:
            print(f"  Segment {seg_id}: no pexels search query, skipping.", file=sys.stderr)
            continue

        downloaded_clips = []

        for q_idx, query in enumerate(queries):
            clip_filename = f"clip_{seg_id:03d}_{q_idx}.mp4"
            clip_path = os.path.join(args.output_dir, clip_filename)

            # Skip if clip already exists (resume support)
            if os.path.exists(clip_path) and os.path.getsize(clip_path) > 10000:
                downloaded_clips.append(clip_filename)
                query_cache[query] = clip_path
                print(f"  Segment {seg_id} clip {q_idx}: already exists, skipping.", file=sys.stderr)
                continue

            # Use cache if same query was already fetched
            if query in query_cache and os.path.exists(query_cache[query]):
                import shutil
                shutil.copy2(query_cache[query], clip_path)
                downloaded_clips.append(clip_filename)
                print(f"  Segment {seg_id} clip {q_idx}: reused cached clip for '{query}'", file=sys.stderr)
                continue

            print(f"  Segment {seg_id} clip {q_idx}: searching Pexels for '{query}'...", file=sys.stderr)

            videos = search_with_fallback(api_key, query, niche)

            if not videos:
                print(f"  WARNING: No footage found for segment {seg_id} query {q_idx}.", file=sys.stderr)
                continue

            duration_needed = seg.get("duration_estimate", 15)
            video, clip_url = pick_best_clip(videos, min_duration=duration_needed)

            if not clip_url:
                print(f"  WARNING: Could not get clip URL for segment {seg_id} query {q_idx}.", file=sys.stderr)
                continue

            print(f"  Downloading ({video.get('duration', '?')}s clip)...", file=sys.stderr)
            success = download_clip(clip_url, clip_path)

            if success:
                downloaded_clips.append(clip_filename)
                query_cache[query] = clip_path
                print(f"  Segment {seg_id} clip {q_idx}: saved {clip_filename}", file=sys.stderr)

        # Store in manifest: list if multiple clips, string if only one (backward compat)
        if len(downloaded_clips) == 1:
            manifest[str(seg_id)] = downloaded_clips[0]
        elif len(downloaded_clips) > 1:
            manifest[str(seg_id)] = downloaded_clips
        else:
            print(f"  WARNING: No clips downloaded for segment {seg_id}. Will be skipped in assembly.", file=sys.stderr)
            failed_segments.append(seg_id)

    # Save manifest
    manifest_path = os.path.join(args.output_dir, "footage_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    total = len(segments)
    fetched = len(manifest)
    print(f"\nFetched {fetched}/{total} segments → {args.output_dir}", file=sys.stderr)
    if failed_segments:
        print(f"Failed segments: {failed_segments}", file=sys.stderr)

    if fetched == 0:
        print("ERROR: No clips fetched at all.", file=sys.stderr)
        sys.exit(1)

    print(args.output_dir)


if __name__ == "__main__":
    main()
