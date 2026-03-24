"""
fetch_mixed_footage.py — Fetch mixed footage for script segments

Priority per segment:
  0. Real image: CC-licensed photo from Wikimedia Commons / Openverse (hook, context, point_N only)
  1. News image: scrape og:image from a relevant news article (saved as .jpg)
  2. Video clips (per query, waterfall across sources):
       a. Original query → Pexels
       b. Original query → Pixabay
       c. Simplified query → Pexels → Pixabay  (only if a+b both failed)
       d. Channel name → Pexels → Pixabay       (last resort)
  3. Pexels photo: static image from Pexels Photos API (if no video found)
  4. Empty (non-fatal): segment skipped in assembly

Real/news images are JPEGs — assemble_video.py handles them with slow Ken Burns zoom.
Video clips are standard .mp4 — assembled with rapid cuts as usual.

Usage:
    python3 tools/fetch_mixed_footage.py \\
        --script-file .tmp/scripts/video_1_script.json \\
        --output-dir .tmp/footage/video_1/

Output:
    .tmp/footage/video_1/clip_001_news.jpg   (news image, if found)
    .tmp/footage/video_1/clip_001_0.mp4      (video clip)
    .tmp/footage/video_1/clip_001_photo.jpg  (Pexels photo fallback)
    .tmp/footage/video_1/footage_manifest.json

Exit code: 0 on success, 1 if no clips fetched at all
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

PEXELS_VIDEOS_API = "https://api.pexels.com/videos"
PEXELS_PHOTOS_API = "https://api.pexels.com/v1"
PIXABAY_VIDEOS_API = "https://pixabay.com/api/videos/"
QUERIES_PER_SEGMENT = 3
MIN_IMAGE_WIDTH = 800

FETCH_REAL_IMAGES_TOOL = os.path.join(os.path.dirname(__file__), "fetch_real_images.py")
# Segment types where a real image is likely to be meaningful
REAL_IMAGE_SEGMENT_TYPES = {"hook", "context", "point_1", "point_2", "point_3", "point_4"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# News image fetching
# ---------------------------------------------------------------------------

def find_article_image_and_url(news_query: str, newsapi_key: str = None) -> tuple:
    """
    Find a relevant news article image URL and article URL for the query.
    Tries NewsAPI first (returns urlToImage + url), then Google News RSS (url only).
    Returns (image_url, article_url) — either may be "" on failure.
    """
    image_url = ""
    article_url = ""

    # Try NewsAPI — returns urlToImage (actual Reuters/AP photo) directly
    if newsapi_key:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={"q": news_query, "language": "en", "pageSize": 3,
                        "sortBy": "publishedAt"},
                headers={"X-Api-Key": newsapi_key},
                timeout=15,
            )
            resp.raise_for_status()
            articles = resp.json().get("articles", [])
            for article in articles:
                url = article.get("url", "")
                if url and "removed" not in url.lower():
                    article_url = url
                    image_url = article.get("urlToImage", "") or ""
                    break
        except Exception as e:
            print(f"    [NewsAPI search] {e}", file=sys.stderr)

    # Try Google News RSS for article URL (if NewsAPI didn't find one)
    if not article_url:
        try:
            import feedparser
            encoded = requests.utils.quote(news_query)
            rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:3]:
                url = entry.get("link", "")
                if url:
                    article_url = url
                    break
        except Exception as e:
            print(f"    [Google News RSS] {e}", file=sys.stderr)

    return image_url, article_url


def fetch_real_article_html(article_url: str) -> tuple:
    """
    Fetch article HTML, following Google News redirects to the actual source article.
    Returns (html, final_url).
    """
    try:
        resp = requests.get(article_url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        final_url = resp.url
        html = resp.text

        # If we're still on Google's domain, try to find the actual source article
        if "news.google.com" in final_url or "google.com/articles" in final_url:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            # Check canonical link first
            canonical = soup.find("link", rel="canonical")
            if canonical and canonical.get("href") and "google" not in canonical.get("href", ""):
                real_url = canonical["href"]
                try:
                    real_resp = requests.get(real_url, headers=HEADERS, timeout=15)
                    real_resp.raise_for_status()
                    return real_resp.text, real_url
                except Exception:
                    pass
            # Try first external link in page
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if href.startswith("http") and "google" not in href and len(href) > 30:
                    try:
                        real_resp = requests.get(href, headers=HEADERS, timeout=15)
                        real_resp.raise_for_status()
                        return real_resp.text, href
                    except Exception:
                        continue

        return html, final_url
    except Exception as e:
        print(f"    [fetch_real_article_html] {e}", file=sys.stderr)
        return "", article_url


def extract_og_image(html: str, base_url: str = "") -> str:
    """Extract og:image URL from HTML. Returns URL or ""."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")

        # og:image (most reliable for news sites)
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            url = og["content"].strip()
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/") and base_url:
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                url = f"{parsed.scheme}://{parsed.netloc}{url}"
            return url

        # twitter:image fallback
        tw = soup.find("meta", attrs={"name": "twitter:image"})
        if tw and tw.get("content"):
            return tw["content"].strip()

        # First large inline image fallback
        for img in soup.find_all("img", src=True):
            src = img.get("src", "")
            width = img.get("width", "0")
            try:
                if int(str(width)) >= MIN_IMAGE_WIDTH:
                    if src.startswith("//"):
                        src = "https:" + src
                    return src
            except (ValueError, TypeError):
                pass

    except Exception:
        pass

    return ""


def download_and_validate_image(image_url: str, output_path: str) -> bool:
    """
    Download image URL, validate it's a real image ≥ MIN_IMAGE_WIDTH px wide,
    save as JPEG. Returns True on success.
    """
    try:
        from PIL import Image

        resp = requests.get(image_url, headers=HEADERS, timeout=20, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and "octet" not in content_type:
            return False

        raw = resp.content
        img = Image.open(io.BytesIO(raw))

        if img.width < MIN_IMAGE_WIDTH:
            img.close()
            return False

        # Convert to RGB (handles PNG with transparency, etc.)
        if img.mode != "RGB":
            img = img.convert("RGB")

        img.save(output_path, "JPEG", quality=92)
        img.close()
        return True

    except Exception as e:
        print(f"    [image download] {e}", file=sys.stderr)
        return False


def fetch_news_image(news_query: str, output_path: str, newsapi_key: str = None) -> bool:
    """
    Full pipeline: find article image → download + validate.
    Priority: 1) NewsAPI urlToImage (actual Reuters/AP photo)
              2) og:image scraped from real article (following Google redirects)
    Returns True if a valid image was saved to output_path.
    Non-fatal: all exceptions caught.
    """
    if not news_query or not news_query.strip():
        return False

    # Skip if already exists
    if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
        print(f"    News image already exists, skipping.", file=sys.stderr)
        return True

    try:
        print(f"    Finding article for: '{news_query}'", file=sys.stderr)
        direct_image_url, article_url = find_article_image_and_url(news_query, newsapi_key)

        # 1. Try NewsAPI's urlToImage directly (Reuters/AP quality photo)
        if direct_image_url:
            print(f"    Trying NewsAPI image: {direct_image_url[:80]}...", file=sys.stderr)
            if download_and_validate_image(direct_image_url, output_path):
                print(f"    Saved NewsAPI image: {os.path.basename(output_path)}", file=sys.stderr)
                return True

        # 2. Fallback: scrape og:image from article, following Google redirects
        if not article_url:
            print(f"    No article URL found.", file=sys.stderr)
            return False

        print(f"    Scraping article: {article_url[:80]}...", file=sys.stderr)
        article_html, final_url = fetch_real_article_html(article_url)
        if not article_html:
            return False

        image_url = extract_og_image(article_html, final_url)
        if not image_url:
            print(f"    No og:image found in article.", file=sys.stderr)
            return False

        print(f"    Downloading image: {image_url[:80]}...", file=sys.stderr)
        success = download_and_validate_image(image_url, output_path)
        if success:
            print(f"    Saved news image: {os.path.basename(output_path)}", file=sys.stderr)
        return success

    except Exception as e:
        print(f"    [fetch_news_image] Failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Pexels video fetching (from fetch_pexels_footage.py)
# ---------------------------------------------------------------------------

def search_pexels_videos(api_key: str, query: str, per_page: int = 10) -> list:
    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": per_page, "orientation": "landscape", "size": "large"}
    resp = requests.get(f"{PEXELS_VIDEOS_API}/search", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("videos", [])


def search_pexels_photos(api_key: str, query: str, per_page: int = 5) -> list:
    """Search Pexels Photos API."""
    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": per_page, "orientation": "landscape"}
    resp = requests.get(f"{PEXELS_PHOTOS_API}/search", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("photos", [])


def pick_best_video_clip(videos: list, min_duration: int = 10):
    """Pick best video clip — prefer long HD clips."""
    results = pick_best_n_video_clips(videos, n=1, min_duration=min_duration)
    return (results[0][0], results[0][1]) if results else (None, None)


def pick_best_n_video_clips(videos: list, n: int = 2, min_duration: int = 10) -> list:
    """
    Pick top-n distinct video clips — prefer long HD clips.
    Returns list of (video, url) tuples, up to n entries.
    """
    def score(v):
        dur = v.get("duration", 0)
        best_w = max((f.get("width", 0) or 0 for f in v.get("video_files", [])), default=0)
        return (1 if dur >= min_duration else 0, best_w, dur)

    if not videos:
        return []

    results = []
    seen_ids = set()
    for v in sorted(videos, key=score, reverse=True):
        vid_id = v.get("id")
        if vid_id in seen_ids:
            continue
        seen_ids.add(vid_id)
        files = v.get("video_files", [])
        if not files:
            continue
        hd = [f for f in files if (f.get("width") or 0) >= 1280]
        target = sorted(hd or files, key=lambda f: f.get("width", 0) or 0, reverse=True)
        url = target[0].get("link")
        if url:
            results.append((v, url))
        if len(results) >= n:
            break

    return results


def simplify_query(query: str) -> str:
    stopwords = {"beautiful", "amazing", "inspiring", "motivating", "happy", "sad",
                 "successful", "positive", "negative", "bright", "dark", "young", "old",
                 "slow", "fast", "busy", "calm", "peaceful", "energetic"}
    words = [w for w in query.split() if w.lower() not in stopwords]
    return " ".join(words) if words else query


def get_pexels_queries(seg: dict) -> list[str]:
    queries = seg.get("pexels_search_queries")
    if isinstance(queries, list) and queries:
        return [q.strip() for q in queries[:QUERIES_PER_SEGMENT] if q.strip()]
    single = seg.get("pexels_search_query", "").strip()
    return [single] if single else []


def search_pixabay_videos(api_key: str, query: str, per_page: int = 5) -> list:
    """Search Pixabay for videos matching a query."""
    params = {
        "key": api_key,
        "q": query,
        "video_type": "film",
        "orientation": "horizontal",
        "per_page": per_page,
        "safesearch": "true",
    }
    resp = requests.get(PIXABAY_VIDEOS_API, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("hits", [])


def pick_best_pixabay_clips(hits: list, n: int = 2, min_duration: int = 10) -> list:
    """
    Pick top-n distinct Pixabay clips — prefer long HD clips.
    Returns list of (hit, url) tuples. Prefers large > medium > small quality.
    """
    def score(h):
        dur = h.get("duration", 0)
        sizes = h.get("videos", {})
        best_w = max((sizes.get(k, {}).get("width", 0) or 0 for k in sizes), default=0)
        return (1 if dur >= min_duration else 0, best_w, dur)

    results = []
    for h in sorted(hits, key=score, reverse=True):
        for size_key in ("large", "medium", "small"):
            url = h.get("videos", {}).get(size_key, {}).get("url", "")
            if url:
                results.append((h, url))
                break
        if len(results) >= n:
            break
    return results


def _try_pexels(api_key: str, query: str) -> list:
    """Search Pexels with rate-limit handling. Returns video list."""
    try:
        videos = search_pexels_videos(api_key, query)
        time.sleep(0.3)
        return videos
    except requests.HTTPError as e:
        if e.response.status_code == 429:
            print("    Pexels rate limit hit. Waiting 60s...", file=sys.stderr)
            time.sleep(60)
            try:
                return search_pexels_videos(api_key, query)
            except Exception:
                return []
        print(f"    Pexels search failed: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"    Pexels search error: {e}", file=sys.stderr)
        return []


def _try_pixabay(api_key: str, query: str) -> list:
    """Search Pixabay. Returns hits list."""
    try:
        hits = search_pixabay_videos(api_key, query)
        time.sleep(0.3)
        return hits
    except Exception as e:
        print(f"    Pixabay search error: {e}", file=sys.stderr)
        return []


def search_videos_all_sources(pexels_key: str, pixabay_key: str,
                               query: str, channel_name: str) -> tuple:
    """
    Waterfall search across Pexels and Pixabay.

    Order per attempt:
      1. Original query → Pexels
      2. Original query → Pixabay
      3. Simplified query → Pexels → Pixabay  (skipped if identical to original)
      4. Channel name → Pexels → Pixabay

    Returns (source, results) where source is 'pexels' or 'pixabay',
    or (None, []) if nothing found anywhere.
    """
    simplified = simplify_query(query)
    attempts = [query, simplified, channel_name] if simplified != query else [query, channel_name]

    for attempt_query in attempts:
        print(f"    Searching Pexels: '{attempt_query}'", file=sys.stderr)
        videos = _try_pexels(pexels_key, attempt_query)
        if videos:
            return "pexels", videos

        if pixabay_key:
            print(f"    Searching Pixabay: '{attempt_query}'", file=sys.stderr)
            hits = _try_pixabay(pixabay_key, attempt_query)
            if hits:
                print(f"    [Pixabay fallback] Found clip for query: '{attempt_query}'",
                      file=sys.stderr)
                return "pixabay", hits

    return None, []


def download_file(url: str, output_path: str, max_retries: int = 3) -> bool:
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
                print(f"    Download attempt {attempt+1} failed: {e}. Retrying...", file=sys.stderr)
                time.sleep(2)
            else:
                print(f"    Download failed after {max_retries} attempts: {e}", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Per-segment processing
# ---------------------------------------------------------------------------

def process_segment(seg: dict, output_dir: str, pexels_api_key: str,
                    newsapi_key: str, channel_name: str,
                    pixabay_api_key: str = "") -> list[str]:
    """
    Returns list of filenames downloaded for this segment (mix of .jpg and .mp4).
    Priority:
      1. News image
      2. Video clips (per query): original → Pexels → Pixabay, then simplified, then channel name
      3. Pexels photo fallback (if no video found at all)
    """
    seg_id = seg.get("segment_id", 0)
    seg_type = seg.get("type", "")
    duration = seg.get("duration_estimate", 15)
    downloaded = []

    # --- 0. Try real image (Wikimedia Commons / Openverse) ---
    # Try queries in order: thumbnail_person_query (entity-focused, best for Wikimedia),
    # then news_search_query (more specific, better for Openverse).
    news_query = (seg.get("news_search_query") or "").strip()
    person_query = (seg.get("_thumbnail_person_query") or "").strip()
    real_queries = [q for q in [person_query, news_query] if q]

    if seg_type in REAL_IMAGE_SEGMENT_TYPES and real_queries:
        real_filename = f"clip_{seg_id:03d}_real.jpg"
        real_path = os.path.join(output_dir, real_filename)
        if os.path.exists(real_path) and os.path.getsize(real_path) > 10000:
            downloaded.append(real_filename)
            print(f"  Segment {seg_id}: real image already cached", file=sys.stderr)
        else:
            for rq in real_queries:
                try:
                    result = subprocess.run(
                        [sys.executable, FETCH_REAL_IMAGES_TOOL,
                         "--query", rq,
                         "--output-file", real_path,
                         "--orientation", "landscape"],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0 and os.path.exists(real_path):
                        downloaded.append(real_filename)
                        print(f"  Segment {seg_id}: real image saved ({real_filename})",
                              file=sys.stderr)
                        break
                    else:
                        if result.stderr:
                            for line in result.stderr.strip().splitlines():
                                print(f"  {line}", file=sys.stderr)
                except Exception as e:
                    print(f"  Segment {seg_id}: real image fetch error: {e}", file=sys.stderr)

    # --- 1. Try news image ---
    if news_query:
        news_filename = f"clip_{seg_id:03d}_news.jpg"
        news_path = os.path.join(output_dir, news_filename)
        if fetch_news_image(news_query, news_path, newsapi_key):
            downloaded.append(news_filename)

    # --- 2. Try video clips (2 per query, across sources) ---
    pexels_queries = get_pexels_queries(seg)
    clip_counter = 0  # global index across all queries for this segment

    for q_idx, query in enumerate(pexels_queries):
        print(f"  Segment {seg_id} q{q_idx}: '{query}'", file=sys.stderr)
        source, results = search_videos_all_sources(
            pexels_api_key, pixabay_api_key, query, channel_name
        )

        if not results:
            clip_counter += 2  # keep numbering predictable
            continue

        if source == "pexels":
            clips = pick_best_n_video_clips(results, n=3, min_duration=8)
        else:
            clips = pick_best_pixabay_clips(results, n=3, min_duration=8)

        for item, clip_url in clips:
            clip_filename = f"clip_{seg_id:03d}_{clip_counter}.mp4"
            clip_path = os.path.join(output_dir, clip_filename)
            clip_counter += 1

            # Resume support
            if os.path.exists(clip_path) and os.path.getsize(clip_path) > 10000:
                downloaded.append(clip_filename)
                continue

            dur = item.get("duration", "?")
            print(f"    [{source}] Downloading {clip_filename} ({dur}s)...", file=sys.stderr)
            if download_file(clip_url, clip_path):
                downloaded.append(clip_filename)

        # Advance counter to fill any unfilled slots
        slots_used = len(clips)
        while slots_used < 2:
            clip_counter += 1
            slots_used += 1

    # --- 3. Pexels photo fallback (if no video found) ---
    if not any(f.endswith(".mp4") for f in downloaded):
        fallback_query = pexels_queries[0] if pexels_queries else channel_name
        photo_filename = f"clip_{seg_id:03d}_photo.jpg"
        photo_path = os.path.join(output_dir, photo_filename)

        if not (os.path.exists(photo_path) and os.path.getsize(photo_path) > 10000):
            print(f"  Segment {seg_id}: trying Pexels photo for '{fallback_query}'...",
                  file=sys.stderr)
            try:
                photos = search_pexels_photos(pexels_api_key, fallback_query)
                time.sleep(0.3)
                if photos:
                    photo_url = photos[0].get("src", {}).get("original", "")
                    if photo_url:
                        from PIL import Image
                        resp = requests.get(photo_url, headers=HEADERS, timeout=30)
                        resp.raise_for_status()
                        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                        img.save(photo_path, "JPEG", quality=92)
                        downloaded.append(photo_filename)
                        print(f"    Saved Pexels photo: {photo_filename}", file=sys.stderr)
            except Exception as e:
                print(f"    Pexels photo fallback failed: {e}", file=sys.stderr)
        else:
            downloaded.append(photo_filename)

    return downloaded


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch mixed footage (news images + Pexels) for script segments"
    )
    parser.add_argument("--script-file", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    if not os.path.exists(args.script_file):
        print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.script_file) as f:
        script = json.load(f)

    pexels_api_key = os.getenv("PEXELS_API_KEY")
    if not pexels_api_key:
        print("ERROR: PEXELS_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    newsapi_key = os.getenv("NEWSAPI_KEY", "") or None
    pixabay_api_key = os.getenv("PIXABAY_API_KEY", "") or ""
    if not pixabay_api_key:
        print("INFO: PIXABAY_API_KEY not set — Pixabay fallback disabled", file=sys.stderr)
    channel_name = os.getenv("CHANNEL_NAME", "trending topics")

    segments = script.get("segments", [])
    if not segments:
        print("ERROR: Script has no segments.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    manifest = {}
    failed_segments = []

    # Inject script-level thumbnail_person_query into each segment for real image fallback
    person_query = script.get("thumbnail_person_query", "")

    for seg in segments:
        seg_id = seg.get("segment_id", 0)
        print(f"\nProcessing segment {seg_id}...", file=sys.stderr)

        if person_query:
            seg = dict(seg)   # don't mutate original
            seg["_thumbnail_person_query"] = person_query

        clips = process_segment(seg, args.output_dir, pexels_api_key, newsapi_key, channel_name, pixabay_api_key)

        if clips:
            manifest[str(seg_id)] = clips if len(clips) > 1 else clips[0]
        else:
            print(f"  WARNING: No clips for segment {seg_id}.", file=sys.stderr)
            failed_segments.append(seg_id)

    # Save manifest
    manifest_path = os.path.join(args.output_dir, "footage_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    total = len(segments)
    fetched = len(manifest)

    # Count real/news images vs video clips
    real_count = sum(
        1 for clips in manifest.values()
        for c in (clips if isinstance(clips, list) else [clips])
        if "_real.jpg" in c
    )
    news_count = sum(
        1 for clips in manifest.values()
        for c in (clips if isinstance(clips, list) else [clips])
        if "_news.jpg" in c
    )
    video_count = sum(
        1 for clips in manifest.values()
        for c in (clips if isinstance(clips, list) else [clips])
        if c.endswith(".mp4")
    )

    print(f"\nFetched {fetched}/{total} segments → {args.output_dir}", file=sys.stderr)
    print(f"  Real images: {real_count} | News images: {news_count} | Pexels videos: {video_count}",
          file=sys.stderr)
    if failed_segments:
        print(f"  Failed segments: {failed_segments}", file=sys.stderr)

    if fetched == 0:
        print("ERROR: No clips fetched at all.", file=sys.stderr)
        sys.exit(1)

    print(args.output_dir)


if __name__ == "__main__":
    main()
