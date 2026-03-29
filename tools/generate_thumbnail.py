"""
generate_thumbnail.py — Generate a clickbaity YouTube thumbnail from script metadata

Supports three layouts driven by the script's thumbnail_layout field:
  - split_face_text  : face/person on left ~55%, bold text on dark panel right ~45%
                       (highest CTR for news content with a key person)
  - face_dominant    : face fills full frame, bold text overlaid in lower third
  - text_dominant    : blurred/darkened background image, hero text fills frame
                       (current fallback — used when no strong person visual)

Output: 1280x720 JPEG (YouTube thumbnail spec)

Usage:
    python3 tools/generate_thumbnail.py \
        --script-file .tmp/scripts/video_1_script.json \
        --output-file .tmp/thumbnails/video_1_thumbnail.jpg
"""

import argparse
import io
import json
import os
import random
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

# Split layout proportions
SPLIT_FACE_RATIO = 0.55   # left face panel width as fraction of total
SPLIT_DIVIDER_W = 4       # px — accent divider between panels

# Brand colors
BRAND_ACCENT = (255, 215, 0)    # #FFD700 gold
DARK_PANEL_BG = (10, 10, 10)    # near-black right panel

FONT_PATHS_BOLD = [
    os.path.join(PROJECT_ROOT, "fonts", "Poppins-SemiBold.ttf"),
    os.path.join(PROJECT_ROOT, "fonts", "Montserrat.ttf"),
    os.path.join(PROJECT_ROOT, "fonts", "Rubik.ttf"),
    os.path.join(PROJECT_ROOT, "fonts", "Raleway.ttf"),
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_PATHS_REGULAR = [
    os.path.join(PROJECT_ROOT, "fonts", "Raleway.ttf"),
    os.path.join(PROJECT_ROOT, "fonts", "Montserrat.ttf"),
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


def fetch_pexels_photo(query, api_key, orientation="landscape"):
    """Fetch the best photo from Pexels for the given query with fallback chain."""
    headers = {"Authorization": api_key}
    queries_to_try = [query, query.split()[0] if query else "motivation", "success"]

    for q in queries_to_try:
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers=headers,
                params={"query": q, "per_page": 30, "orientation": orientation},
                timeout=15,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if photos:
                src = random.choice(photos).get("src", {})
                url = src.get("large2x") or src.get("large") or src.get("original")
                if url:
                    return url
        except Exception as e:
            print(f"  Pexels query '{q}' failed: {e}", file=sys.stderr)

    return None


def download_image(url):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def resize_and_crop(img, width, height):
    """Resize and center-crop image to exact dimensions."""
    img_ratio = img.width / img.height
    target_ratio = width / height
    if img_ratio > target_ratio:
        new_h = height
        new_w = int(img.width * height / img.height)
    else:
        new_w = width
        new_h = int(img.height * width / img.width)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def crop_to_subject(img, target_w, target_h, focus="upper"):
    """Crop image to target size, biased toward the upper portion (faces)."""
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h
    if img_ratio > target_ratio:
        new_h = target_h
        new_w = int(img.width * target_h / img.height)
    else:
        new_w = target_w
        new_h = int(img.height * target_w / img.width)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    # Bias toward upper portion for face crops
    if focus == "upper":
        top = max(0, int((new_h - target_h) * 0.25))
    else:
        top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def apply_dark_gradient(img):
    """Standard bottom-heavy dark gradient for text legibility."""
    gradient = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)
    for y in range(img.height):
        progress = max(0, (y / img.height - 0.15) / 0.85)
        alpha = int(min(200, progress * 200))
        draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))
    combined = Image.alpha_composite(img.convert("RGBA"), gradient)
    return combined.convert("RGB")


def apply_vignette(img, strength=0.7):
    """Radial dark vignette — darkens edges, keeps center vivid (face_dominant)."""
    vignette = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(vignette)
    w, h = img.size
    cx, cy = w // 2, h // 2
    max_r = (cx**2 + cy**2) ** 0.5
    # Draw concentric ellipses from outside in
    steps = 80
    for i in range(steps):
        frac = 1.0 - i / steps          # 1.0 at edge, 0.0 at center
        alpha = int(frac ** 1.8 * 255 * strength)
        ex = int(cx * (1 - frac * 0.85))
        ey = int(cy * (1 - frac * 0.85))
        draw.ellipse([cx - ex, cy - ey, cx + ex, cy + ey], fill=(0, 0, 0, alpha))
    combined = Image.alpha_composite(img.convert("RGBA"), vignette)
    return combined.convert("RGB")


def draw_text_with_stroke(draw, position, text, font, fill, stroke_fill, stroke_width):
    x, y = position
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)


def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines = []
    current_line = []
    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))
    return lines


def add_badge(img, label="EXPLAINED", x=40, y=40):
    """Bold badge pill in the top-left corner (or specified position)."""
    draw = ImageDraw.Draw(img)
    badge_font = find_font(FONT_PATHS_BOLD, 32)
    bbox = draw.textbbox((0, 0), label, font=badge_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x, pad_y = 16, 10
    rx2 = x + text_w + pad_x * 2
    ry2 = y + text_h + pad_y * 2
    draw.rounded_rectangle([x, y, rx2, ry2], radius=6, fill=(220, 38, 38))
    draw.text((x + pad_x, y + pad_y), label, font=badge_font, fill=(255, 255, 255))
    return img


def load_strategy(path):
    try:
        if path and os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ─── Layout renderers ────────────────────────────────────────────────────────

def render_split_face_text(face_img, main_text, sub_text=None):
    """Left panel: face/person. Right panel: dark bg + bold text.

    Proven highest-CTR layout for news channels — the face creates emotional
    connection; the text creates the tension/curiosity that completes the click.
    """
    face_w = int(THUMBNAIL_WIDTH * SPLIT_FACE_RATIO)
    text_panel_w = THUMBNAIL_WIDTH - face_w - SPLIT_DIVIDER_W

    # Face panel — crop biased upward to catch the face
    face_panel = crop_to_subject(face_img, face_w, THUMBNAIL_HEIGHT, focus="upper")

    # Subtle dark edge on the right side of face panel to blend into dark text bg
    face_panel = face_panel.copy()
    fp_draw = ImageDraw.Draw(face_panel.convert("RGBA"))
    overlay = Image.new("RGBA", face_panel.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    fade_w = 80
    for i in range(fade_w):
        alpha = int((i / fade_w) ** 0.5 * 160)
        x = face_w - fade_w + i
        od.line([(x, 0), (x, THUMBNAIL_HEIGHT)], fill=(0, 0, 0, alpha))
    face_panel = Image.alpha_composite(face_panel.convert("RGBA"), overlay).convert("RGB")

    # Text panel — dark background
    text_panel = Image.new("RGB", (text_panel_w, THUMBNAIL_HEIGHT), DARK_PANEL_BG)
    draw = ImageDraw.Draw(text_panel)

    # Subtle texture: very faint diagonal lines
    for y in range(0, THUMBNAIL_HEIGHT, 40):
        draw.line([(0, y), (text_panel_w, y + 20)], fill=(20, 20, 20), width=1)

    # Main text — 1-3 words max, so start big and only shrink if truly needed
    padding = 40
    max_text_w = text_panel_w - padding * 2
    font_size = 130
    main_font = find_font(FONT_PATHS_BOLD, font_size)
    lines = wrap_text(main_text, main_font, max_text_w, draw)
    while len(lines) > 3 and font_size > 72:
        font_size -= 8
        main_font = find_font(FONT_PATHS_BOLD, font_size)
        lines = wrap_text(main_text, main_font, max_text_w, draw)

    # Calculate total text block height and center vertically
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=main_font)
        line_heights.append(bbox[3] - bbox[1])
    line_spacing = 18
    total_h = sum(line_heights) + line_spacing * (len(lines) - 1)
    start_y = (THUMBNAIL_HEIGHT - total_h) // 2

    # Draw gold accent bar above text
    bar_y = start_y - 24
    draw.rectangle([padding, bar_y, padding + 70, bar_y + 6], fill=BRAND_ACCENT)

    y = start_y
    for i, line in enumerate(lines):
        draw_text_with_stroke(draw, (padding, y), line, font=main_font,
                              fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke_width=4)
        y += line_heights[i] + line_spacing

    # Sub-text suppressed — fewer elements = cleaner, better CTR at small sizes

    # Compose: face | divider | text panel
    canvas = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT))
    canvas.paste(face_panel, (0, 0))
    # Gold divider line
    cd = ImageDraw.Draw(canvas)
    cd.rectangle([face_w, 0, face_w + SPLIT_DIVIDER_W, THUMBNAIL_HEIGHT], fill=BRAND_ACCENT)
    canvas.paste(text_panel, (face_w + SPLIT_DIVIDER_W, 0))

    # Badge on face panel
    canvas = add_badge(canvas, label="EXPLAINED", x=28, y=28)
    return canvas


def render_face_dominant(face_img, main_text, sub_text=None):
    """Face fills full frame. Bold text overlaid in lower third.

    Best when the person IS the story — recognisable figure, strong expression.
    """
    img = resize_and_crop(face_img, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
    img = apply_vignette(img, strength=0.65)

    # Extra dark gradient at the bottom for text legibility
    gradient = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for y in range(img.height):
        progress = max(0, (y / img.height - 0.35) / 0.65)
        alpha = int(min(230, progress * 230))
        gd.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), gradient).convert("RGB")

    draw = ImageDraw.Draw(img)
    padding = 60
    max_text_w = THUMBNAIL_WIDTH - padding * 2

    # Main text — big, few words, lower third (safe from duration badge bottom-right)
    font_size = 140
    main_font = find_font(FONT_PATHS_BOLD, font_size)
    lines = wrap_text(main_text, main_font, max_text_w, draw)
    while len(lines) > 3 and font_size > 80:
        font_size -= 8
        main_font = find_font(FONT_PATHS_BOLD, font_size)
        lines = wrap_text(main_text, main_font, max_text_w, draw)

    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=main_font)
        line_heights.append(bbox[3] - bbox[1])
    line_spacing = 14
    total_h = sum(line_heights) + line_spacing * (len(lines) - 1)

    # Position in lower third, left-aligned — keeps bottom-right corner clear
    text_bottom = THUMBNAIL_HEIGHT - 55   # above YouTube duration badge area
    start_y = text_bottom - total_h

    # Gold accent bar
    draw.rectangle([padding, start_y - 18, padding + 70, start_y - 12], fill=BRAND_ACCENT)

    y = start_y
    for i, line in enumerate(lines):
        draw_text_with_stroke(draw, (padding, y), line, font=main_font,
                              fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke_width=4)
        y += line_heights[i] + line_spacing

    img = add_badge(img, label="EXPLAINED", x=40, y=40)
    return img


def render_text_dominant(bg_img, main_text, sub_text=None):
    """Blurred/darkened background + hero text fills frame.

    Standard fallback — best when no strong person visual is available.
    """
    img = resize_and_crop(bg_img, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
    img = img.filter(ImageFilter.GaussianBlur(radius=8))

    # Heavy dark overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 180))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    # Gradient still helps
    img = apply_dark_gradient(img)

    draw = ImageDraw.Draw(img)
    padding = 60
    max_text_w = THUMBNAIL_WIDTH - padding * 2

    # Big font — 1-3 words fills the frame intentionally
    font_size = 150
    main_font = find_font(FONT_PATHS_BOLD, font_size)
    lines = wrap_text(main_text, main_font, max_text_w, draw)
    while len(lines) > 3 and font_size > 80:
        font_size -= 10
        main_font = find_font(FONT_PATHS_BOLD, font_size)
        lines = wrap_text(main_text, main_font, max_text_w, draw)

    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=main_font)
        line_heights.append(bbox[3] - bbox[1])
    line_spacing = 16
    total_h = sum(line_heights) + line_spacing * (len(lines) - 1)

    # Center vertically in the frame
    start_y = (THUMBNAIL_HEIGHT - total_h) // 2

    y = start_y
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=main_font)
        line_w = bbox[2] - bbox[0]
        x = (THUMBNAIL_WIDTH - line_w) // 2
        draw_text_with_stroke(draw, (x, y), line, font=main_font,
                              fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke_width=5)
        y += line_heights[i] + line_spacing

    img = add_badge(img, label="EXPLAINED", x=40, y=40)
    return img


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate a clickbaity YouTube thumbnail")
    parser.add_argument("--script-file", default="", help="Path to the video script JSON")
    parser.add_argument("--output-file", required=True, help="Output JPEG path")
    parser.add_argument("--thumbnail-text", default="", help="Override script thumbnail_text")
    parser.add_argument("--search-query", default="", help="Override Pexels search query (bg/face)")
    parser.add_argument("--sub-text", default="", help="Override sub-text overlay")
    parser.add_argument("--layout", default="", help="Override thumbnail_layout from script")
    parser.add_argument("--strategy-file", default="", help="Path to channel_strategy.json")
    args = parser.parse_args()

    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        print("ERROR: PEXELS_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    # Load script
    if args.script_file:
        if not os.path.exists(args.script_file):
            print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.script_file) as f:
            script = json.load(f)
    elif args.thumbnail_text:
        script = {
            "thumbnail_text": args.thumbnail_text,
            "title": args.thumbnail_text,
            "segments": [{"overlay_text": args.sub_text, "pexels_search_query": args.search_query}],
        }
    else:
        print("ERROR: Provide either --script-file or --thumbnail-text", file=sys.stderr)
        sys.exit(1)

    # Resolve fields — CLI overrides take priority
    main_text = args.thumbnail_text or script.get("thumbnail_text") or script.get("title", "")
    if not main_text:
        print("ERROR: No thumbnail_text or title found in script", file=sys.stderr)
        sys.exit(1)

    segments = script.get("segments", [])
    sub_text = args.sub_text or (segments[0].get("overlay_text", "") if segments else "")

    layout = (args.layout
              or script.get("thumbnail_layout", "")
              or "split_face_text").lower().strip()
    # Normalise — tolerate minor variations
    if "split" in layout:
        layout = "split_face_text"
    elif "face" in layout:
        layout = "face_dominant"
    else:
        layout = "text_dominant"

    # For split/face_dominant: prefer thumbnail_face_query, then thumbnail_person_query
    face_query = (args.search_query
                  or script.get("thumbnail_face_query", "")
                  or script.get("thumbnail_person_query", "")
                  or (segments[0].get("pexels_search_query", "") if segments else "")
                  or main_text)

    # Background query (used as fallback in split, or primary in text_dominant)
    bg_query = (args.search_query
                or script.get("thumbnail_person_query", "")
                or (segments[0].get("pexels_search_query", "") if segments else "")
                or main_text)

    print(f"Layout: {layout}", file=sys.stderr)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_file)), exist_ok=True)

    if layout == "split_face_text":
        print(f"Fetching face image: '{face_query}'", file=sys.stderr)
        face_url = fetch_pexels_photo(face_query, api_key, orientation="portrait")
        if not face_url:
            face_url = fetch_pexels_photo(face_query, api_key, orientation="landscape")
        if not face_url:
            print("  Face fetch failed — falling back to text_dominant", file=sys.stderr)
            layout = "text_dominant"
        else:
            face_img = download_image(face_url)
            img = render_split_face_text(face_img, main_text, sub_text or None)

    if layout == "face_dominant":
        print(f"Fetching face image: '{face_query}'", file=sys.stderr)
        face_url = fetch_pexels_photo(face_query, api_key, orientation="portrait")
        if not face_url:
            face_url = fetch_pexels_photo(face_query, api_key, orientation="landscape")
        if not face_url:
            print("  Face fetch failed — falling back to text_dominant", file=sys.stderr)
            layout = "text_dominant"
        else:
            face_img = download_image(face_url)
            img = render_face_dominant(face_img, main_text, sub_text or None)

    if layout == "text_dominant":
        print(f"Fetching background image: '{bg_query}'", file=sys.stderr)
        bg_url = fetch_pexels_photo(bg_query, api_key, orientation="landscape")
        if not bg_url:
            print("ERROR: Could not fetch any photo from Pexels", file=sys.stderr)
            sys.exit(1)
        bg_img = download_image(bg_url)
        img = render_text_dominant(bg_img, main_text, sub_text or None)

    img.save(args.output_file, "JPEG", quality=92, optimize=True)
    size_kb = os.path.getsize(args.output_file) // 1024
    print(f"Thumbnail saved: {args.output_file} ({size_kb}KB)", file=sys.stderr)
    print(args.output_file)


if __name__ == "__main__":
    main()
