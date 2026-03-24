"""
fetch_real_images.py — Fetch a real CC-licensed image from Wikimedia Commons or Openverse

Searches Wikimedia Commons first (no API key, best coverage for politicians/officials/events).
Falls back to Openverse if Wikimedia returns nothing usable.

Filters by orientation (landscape for full video, portrait for Shorts) and minimum quality.
Exits 0 with image saved, or exits 1 if no suitable image found (non-fatal — caller falls back).

Usage:
    python3 tools/fetch_real_images.py \
        --query "Donald Trump press conference 2026" \
        --output-file .tmp/footage/video_1/clip_001_real.jpg \
        --orientation landscape

    python3 tools/fetch_real_images.py \
        --query "Jerome Powell Federal Reserve" \
        --output-file .tmp/footage/video_1/clip_001_real.jpg \
        --orientation portrait

Exit code: 0 = image saved to --output-file, 1 = no suitable image found
"""

import argparse
import io
import os
import sys
import time

import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

WIKIMEDIA_API   = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API   = "https://api.openverse.org/v1/images/"

MIN_SHORT_SIDE  = 800   # px — reject tiny images
IMAGE_EXTS      = {".jpg", ".jpeg", ".png"}

HEADERS = {
    "User-Agent": (
        "TrendingTopicsBot/1.0 (automated video production; "
        "contact via GitHub) python-requests"
    )
}


# ─── Wikimedia Commons ────────────────────────────────────────────────────────

def search_wikimedia(query: str, orientation: str) -> str | None:
    """Search Wikimedia Commons. Returns direct image URL or None."""
    params = {
        "action":       "query",
        "generator":    "search",
        "gsrsearch":    query,
        "gsrnamespace": "6",          # File namespace only
        "gsrlimit":     "20",
        "prop":         "imageinfo",
        "iiprop":       "url|dimensions|mime",
        "format":       "json",
    }
    try:
        resp = requests.get(WIKIMEDIA_API, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
    except Exception as e:
        print(f"  Wikimedia search failed: {e}", file=sys.stderr)
        return None

    candidates = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        url   = info.get("url", "")
        mime  = info.get("mime", "")
        w     = info.get("width", 0)
        h     = info.get("height", 0)

        if not url or not mime.startswith("image/"):
            continue
        ext = os.path.splitext(url.lower())[1]
        if ext not in IMAGE_EXTS:
            continue
        if min(w, h) < MIN_SHORT_SIDE:
            continue
        if not _orientation_ok(w, h, orientation):
            continue

        candidates.append((w * h, url))   # sort by resolution descending

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


# ─── Openverse ────────────────────────────────────────────────────────────────

def search_openverse(query: str, orientation: str) -> str | None:
    """Search Openverse. Returns direct image URL or None.

    Uses OPENVERSE_API_KEY from .env if available (100 req/min).
    Falls back to anonymous (5 req/hr) — only used as last resort so usually fine.
    """
    api_key = os.getenv("OPENVERSE_API_KEY", "")
    headers = dict(HEADERS)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    params = {
        "q":         query,
        "license":   "by,by-sa,cc0,by-nc,by-nc-sa",
        "page_size": "20",
    }
    try:
        resp = requests.get(OPENVERSE_API, params=params, headers=headers, timeout=15)
        if resp.status_code == 429:
            print("  Openverse rate limited (anonymous) — skipping", file=sys.stderr)
            return None
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        print(f"  Openverse search failed: {e}", file=sys.stderr)
        return None

    for item in results:
        url = item.get("url", "")
        w   = item.get("width") or 0
        h   = item.get("height") or 0

        if not url:
            continue
        ext = os.path.splitext(url.lower().split("?")[0])[1]
        if ext not in IMAGE_EXTS:
            continue
        if min(w, h) < MIN_SHORT_SIDE:
            continue
        if w and h and not _orientation_ok(w, h, orientation):
            continue

        return url

    return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _orientation_ok(w: int, h: int, orientation: str) -> bool:
    if orientation == "portrait":
        return h > w
    elif orientation == "landscape":
        return w >= h
    return True   # "any"


def download_and_save(url: str, output_path: str) -> bool:
    """Download image URL, convert to RGB JPEG, save to output_path."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        img.save(output_path, "JPEG", quality=92, optimize=True)
        w, h = img.size
        print(f"  Real image saved: {os.path.basename(output_path)} ({w}×{h})",
              file=sys.stderr)
        return True
    except Exception as e:
        print(f"  Download/save failed: {e}", file=sys.stderr)
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch a real CC-licensed image from Wikimedia Commons or Openverse"
    )
    parser.add_argument("--query",       required=True,  help="Search query")
    parser.add_argument("--output-file", required=True,  help="Output JPEG path")
    parser.add_argument("--orientation", default="landscape",
                        choices=["landscape", "portrait", "any"],
                        help="Required orientation (default: landscape)")
    args = parser.parse_args()

    print(f"  Searching Wikimedia Commons: '{args.query}' [{args.orientation}]",
          file=sys.stderr)
    url = search_wikimedia(args.query, args.orientation)

    if not url:
        print(f"  Wikimedia: no match — trying Openverse...", file=sys.stderr)
        time.sleep(0.3)
        url = search_openverse(args.query, args.orientation)

    if not url:
        print(f"  No real image found for: '{args.query}'", file=sys.stderr)
        sys.exit(1)

    if download_and_save(url, args.output_file):
        print(args.output_file)
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
