"""
generate_thumbnail.py — Generate a clickbaity YouTube thumbnail from script metadata

Fetches a Pexels photo matching the script's topic, then composites:
  - Dark gradient overlay for text legibility
  - Bold white main title text (from thumbnail_text field)
  - Smaller hook subtext (from first segment overlay_text)

Output: 1280x720 JPEG (YouTube thumbnail spec)

Usage:
    python3 tools/generate_thumbnail.py \
        --script-file .tmp/scripts/video_1_script.json \
        --output-file .tmp/thumbnails/video_1_thumbnail.jpg

Exit code: 0 on success, 1 on failure
"""

import argparse
import io
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter, ImageFont

load_dotenv()

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_STRATEGY_PATH = os.path.join(PROJECT_ROOT, "channel_strategy.json")

# Font paths to try in order (macOS, Linux, fallback)
# Impact is listed first — matches channel strategy's "ultra-bold sans-serif" spec
FONT_PATHS_BOLD = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_PATHS_REGULAR = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def find_font(paths, size):
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def fetch_pexels_photo(query, api_key):
    """Fetch the best landscape photo from Pexels for the given query."""
    headers = {"Authorization": api_key}
    # Try the specific query first, then fallback to simpler terms
    queries_to_try = [query, query.split()[0] if query else "motivation", "success"]

    for q in queries_to_try:
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers=headers,
                params={"query": q, "per_page": 15, "orientation": "landscape"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            photos = data.get("photos", [])
            if photos:
                # Prefer large/original photos
                photo = photos[0]
                src = photo.get("src", {})
                url = src.get("large2x") or src.get("large") or src.get("original")
                if url:
                    return url
        except Exception as e:
            print(f"  Pexels query '{q}' failed: {e}", file=sys.stderr)
            continue

    return None


def download_image(url):
    """Download image from URL and return PIL Image."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def resize_and_crop(img, width, height):
    """Resize and center-crop image to exact dimensions."""
    img_ratio = img.width / img.height
    target_ratio = width / height

    if img_ratio > target_ratio:
        # Image is wider — scale by height
        new_h = height
        new_w = int(img.width * height / img.height)
    else:
        # Image is taller — scale by width
        new_w = width
        new_h = int(img.height * width / img.width)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center crop
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def load_strategy(path):
    """Load channel strategy JSON if it exists, return {} otherwise."""
    try:
        if path and os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def apply_dark_gradient(img, use_navy=False):
    """Apply a dark gradient overlay — heavier at bottom for text legibility.

    For news/current events content: uses a dark overlay from top (light)
    to bottom (heavy black) so bold text pops against any background image.
    """
    gradient = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)

    height = img.height
    # News style: strong top-to-bottom gradient, dark enough for bold white text
    for y in range(height):
        progress = max(0, (y / height - 0.15) / 0.85)
        alpha = int(min(200, progress * 200))
        draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))

    img_rgba = img.convert("RGBA")
    combined = Image.alpha_composite(img_rgba, gradient)
    return combined.convert("RGB")


def add_badge(img, label="EXPLAINED"):
    """Add a small bold badge (e.g. EXPLAINED / BREAKING) in the top-left corner."""
    draw = ImageDraw.Draw(img)
    badge_font = find_font(FONT_PATHS_BOLD, 32)
    bbox = draw.textbbox((0, 0), label, font=badge_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x, pad_y = 16, 10
    rect_x1, rect_y1 = 40, 40
    rect_x2 = rect_x1 + text_w + pad_x * 2
    rect_y2 = rect_y1 + text_h + pad_y * 2
    # Red background pill
    draw.rounded_rectangle([rect_x1, rect_y1, rect_x2, rect_y2], radius=6, fill=(220, 38, 38))
    # White text
    draw.text((rect_x1 + pad_x, rect_y1 + pad_y), label, font=badge_font, fill=(255, 255, 255))
    return img


def draw_text_with_stroke(draw, position, text, font, fill, stroke_fill, stroke_width):
    """Draw text with an outline/stroke for contrast."""
    x, y = position
    # Draw stroke by offsetting in all directions
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    # Draw main text
    draw.text((x, y), text, font=font, fill=fill)


def wrap_text(text, font, max_width, draw):
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]

    if current_line:
        lines.append(" ".join(current_line))

    return lines


def add_text_overlays(img, main_text, sub_text=None):
    """Add main title and optional subtext to the image."""
    draw = ImageDraw.Draw(img)
    width, height = img.size
    padding = 60
    max_text_width = width - padding * 2

    # Main title — large bold font
    main_font_size = 96
    main_font = find_font(FONT_PATHS_BOLD, main_font_size)

    # Wrap and potentially shrink if too many lines
    lines = wrap_text(main_text, main_font, max_text_width, draw)
    while len(lines) > 3 and main_font_size > 60:
        main_font_size -= 8
        main_font = find_font(FONT_PATHS_BOLD, main_font_size)
        lines = wrap_text(main_text, main_font, max_text_width, draw)

    # Calculate total text block height
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=main_font)
        line_heights.append(bbox[3] - bbox[1])

    line_spacing = 12
    total_text_height = sum(line_heights) + line_spacing * (len(lines) - 1)

    # Position: upper half of image (centered vertically in top 60%)
    start_y = int(height * 0.15)
    if sub_text:
        # Leave room at bottom for subtext — compress main text into top 55%
        available_height = int(height * 0.55) - start_y
        start_y = start_y + max(0, (available_height - total_text_height) // 2)

    # Draw each line centered horizontally
    y = start_y
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=main_font)
        line_w = bbox[2] - bbox[0]
        x = (width - line_w) // 2
        draw_text_with_stroke(
            draw, (x, y), line,
            font=main_font,
            fill=(255, 255, 255),
            stroke_fill=(0, 0, 0),
            stroke_width=4,
        )
        y += line_heights[i] + line_spacing

    # Subtext — smaller font in lower third
    if sub_text:
        sub_font_size = 44
        sub_font = find_font(FONT_PATHS_REGULAR, sub_font_size)
        sub_lines = wrap_text(sub_text, sub_font, max_text_width, draw)[:2]  # Max 2 lines

        sub_line_heights = []
        for line in sub_lines:
            bbox = draw.textbbox((0, 0), line, font=sub_font)
            sub_line_heights.append(bbox[3] - bbox[1])

        sub_total_h = sum(sub_line_heights) + 8 * (len(sub_lines) - 1)
        sub_y = height - padding - sub_total_h

        for i, line in enumerate(sub_lines):
            bbox = draw.textbbox((0, 0), line, font=sub_font)
            line_w = bbox[2] - bbox[0]
            x = (width - line_w) // 2
            draw_text_with_stroke(
                draw, (x, sub_y), line,
                font=sub_font,
                fill=(255, 235, 100),   # Warm yellow for contrast
                stroke_fill=(0, 0, 0),
                stroke_width=3,
            )
            sub_y += sub_line_heights[i] + 8

    return img


def main():
    parser = argparse.ArgumentParser(description="Generate a clickbaity YouTube thumbnail")
    parser.add_argument("--script-file", default="", help="Path to the video script JSON")
    parser.add_argument("--output-file", required=True, help="Output JPEG path")
    # Optional overrides — allow standalone use without a script file
    parser.add_argument("--thumbnail-text", default="", help="Override script thumbnail_text")
    parser.add_argument("--search-query", default="", help="Override Pexels search query")
    parser.add_argument("--sub-text", default="", help="Override sub-text overlay")
    parser.add_argument("--strategy-file", default="", help="Path to channel_strategy.json (auto-detected if not set)")
    args = parser.parse_args()

    strategy_path = args.strategy_file or DEFAULT_STRATEGY_PATH
    strategy = load_strategy(strategy_path)
    use_navy = False  # News/current events niche: use strong dark gradient, not navy

    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        print("ERROR: PEXELS_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    # Load script if provided, otherwise build a minimal dict from CLI args
    if args.script_file:
        if not os.path.exists(args.script_file):
            print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.script_file) as f:
            script = json.load(f)
    elif args.thumbnail_text:
        # Standalone mode: build synthetic script from CLI args
        script = {
            "thumbnail_text": args.thumbnail_text,
            "title": args.thumbnail_text,
            "segments": [{"overlay_text": args.sub_text, "pexels_search_query": args.search_query}],
        }
    else:
        print("ERROR: Provide either --script-file or --thumbnail-text", file=sys.stderr)
        sys.exit(1)

    # Extract text fields — CLI overrides take priority
    main_text = args.thumbnail_text or script.get("thumbnail_text") or script.get("title", "")
    if not main_text:
        print("ERROR: No thumbnail_text or title found in script", file=sys.stderr)
        sys.exit(1)

    segments = script.get("segments", [])
    sub_text = args.sub_text or (segments[0].get("overlay_text", "") if segments else "")
    # Prefer thumbnail_person_query (face/figure) for higher CTR, fall back to hook footage query
    search_query = (args.search_query
                    or script.get("thumbnail_person_query", "")
                    or (segments[0].get("pexels_search_query", "") if segments else "")
                    or script.get("pexels_search_query", main_text))

    print(f"Searching Pexels for: '{search_query}'", file=sys.stderr)
    photo_url = fetch_pexels_photo(search_query, api_key)

    if not photo_url:
        print("ERROR: Could not fetch photo from Pexels", file=sys.stderr)
        sys.exit(1)

    print(f"Downloading photo...", file=sys.stderr)
    img = download_image(photo_url)

    # Resize to thumbnail dimensions
    img = resize_and_crop(img, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)

    # Apply dark gradient overlay
    img = apply_dark_gradient(img, use_navy=use_navy)

    # Add text overlays
    img = add_text_overlays(img, main_text, sub_text if sub_text else None)

    # Add EXPLAINED badge in top-left corner
    img = add_badge(img, label="EXPLAINED")

    # Save as JPEG
    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
    img.save(args.output_file, "JPEG", quality=92, optimize=True)

    size_kb = os.path.getsize(args.output_file) // 1024
    print(f"Thumbnail saved: {args.output_file} ({size_kb}KB)", file=sys.stderr)
    print(args.output_file)


if __name__ == "__main__":
    main()
