"""
assemble_video.py — Assemble final video from stock footage, voiceover, and script

Usage:
    python3 tools/assemble_video.py \
        --script-file .tmp/scripts/video_1_script.json \
        --audio-file .tmp/audio/video_1_voiceover.mp3 \
        --footage-dir .tmp/footage/video_1/ \
        --output .tmp/output/video_1_final.mp4

Output (stdout): Path to final MP4
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile

import numpy as np

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
FPS = 30
OVERLAY_FONTSIZE = 72
WATERMARK_FONTSIZE = 36
FADE_DURATION = 0.3
SUB_CLIP_MIN = 3   # seconds: minimum cut duration
SUB_CLIP_MAX = 6   # seconds: maximum cut duration
CAPTION_FONTSIZE = 68           # 3/4 of original 90
CAPTION_CHUNK_SIZE = 3
OVERLAY_DISPLAY_DURATION = 4.5   # max seconds to show overlay text
OVERLAY_REVEAL_DURATION  = 0.5   # seconds for left-to-right wipe-in animation
OVERLAY_MARGIN           = 60    # px from top-left edge
OVERLAY_TEXT_COLOR       = (255, 215, 0, 255)  # #FFD700 yellow (matches Shorts palette)
OVERLAY_MAX_TEXT_WIDTH   = TARGET_WIDTH - OVERLAY_MARGIN - 80  # wrap before right edge

SFX_VOLUMES = {"riser": 0.40, "woosh": 0.50, "beep_0.5sec": 0.60, "beep_1sec": 0.60, "bell": 0.55}
CHAPTER_CARD_DURATION = 2.5   # seconds for each chapter transition card

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

CAPTION_COLOR_POOL = [
    (255, 215, 0, 255),   # gold
    (255, 107, 107, 255), # coral
    (0, 229, 204, 255),   # teal
    (96, 165, 250, 255),  # sky blue
    (167, 139, 250, 255), # soft purple
    (52, 211, 153, 255),  # mint green
    (251, 191, 36, 255),  # amber
    (244, 114, 182, 255), # pink
    (255, 255, 255, 255), # white
]
CAPTION_FONT_POOL = [
    os.path.join(PROJECT_ROOT, "fonts", "Poppins-SemiBold.ttf"),
    os.path.join(PROJECT_ROOT, "fonts", "Montserrat.ttf"),
    os.path.join(PROJECT_ROOT, "fonts", "Rubik.ttf"),
    os.path.join(PROJECT_ROOT, "fonts", "Raleway.ttf"),
]

# Fixed fonts: Poppins-SemiBold (thickest) for overlays/CTA/chapter cards,
# Montserrat for captions/watermark (readable medium weight).
OVERLAY_FONT_PATH = os.path.join(PROJECT_ROOT, "fonts", "Poppins-SemiBold.ttf")
CAPTION_FONT_PATH = os.path.join(PROJECT_ROOT, "fonts", "Montserrat.ttf")

# Set once per run by pick_video_style(); used by all text functions
_VIDEO_FONT_PATH = None
_VIDEO_FONT_PATH_OVERLAY = None   # hook, CTA overlays, chapter cards
_VIDEO_FONT_PATH_CAPTION = None   # dialogue captions, watermark
_VIDEO_COLOR_CAPTION = (255, 255, 255, 255)
_VIDEO_COLOR_OVERLAY = (255, 215, 0, 255)
_VIDEO_COLOR_CHAPTER = (255, 255, 255, 255)


def pick_video_style():
    global _VIDEO_FONT_PATH, _VIDEO_FONT_PATH_OVERLAY, _VIDEO_FONT_PATH_CAPTION
    global _VIDEO_COLOR_CAPTION, _VIDEO_COLOR_OVERLAY, _VIDEO_COLOR_CHAPTER
    _VIDEO_FONT_PATH_OVERLAY = OVERLAY_FONT_PATH if os.path.exists(OVERLAY_FONT_PATH) else None
    _VIDEO_FONT_PATH_CAPTION = CAPTION_FONT_PATH if os.path.exists(CAPTION_FONT_PATH) else None
    _VIDEO_FONT_PATH = _VIDEO_FONT_PATH_OVERLAY  # backward-compat fallback
    colors = random.sample(CAPTION_COLOR_POOL, min(3, len(CAPTION_COLOR_POOL)))
    _VIDEO_COLOR_CAPTION, _VIDEO_COLOR_OVERLAY, _VIDEO_COLOR_CHAPTER = colors[0], colors[1], colors[2]
    print(f"  Video style → overlay font: {os.path.basename(_VIDEO_FONT_PATH_OVERLAY or 'default')} | "
          f"caption font: {os.path.basename(_VIDEO_FONT_PATH_CAPTION or 'default')} | "
          f"caption: {_VIDEO_COLOR_CAPTION} | overlay: {_VIDEO_COLOR_OVERLAY} | "
          f"chapter: {_VIDEO_COLOR_CHAPTER}", file=sys.stderr)


def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def get_audio_duration(audio_path):
    """Get duration of audio file in seconds."""
    try:
        from mutagen.mp3 import MP3
        return MP3(audio_path).info.length
    except Exception:
        import subprocess, json
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(result.stdout)["format"]["duration"])


def compute_content_durations(segments, total_audio_duration):
    """Pure proportional durations — maps each segment to its share of the voiceover."""
    total_estimate = sum(max(s.get("duration_estimate", 15), 1) for s in segments)
    return [
        max((max(s.get("duration_estimate", 15), 1) / total_estimate) * total_audio_duration, 2.0)
        for s in segments
    ]


def calculate_segment_durations(segments, total_audio_duration):
    """
    Distribute total audio duration across segments proportionally.
    Chapter card segments get CHAPTER_CARD_DURATION added so the footage
    window covers the silent pause at the start of that segment.
    """
    content_durs = compute_content_durations(segments, total_audio_duration)
    return [
        dur + (CHAPTER_CARD_DURATION
               if seg.get("type", "").startswith("point_") and seg.get("chapter_title")
               else 0.0)
        for seg, dur in zip(segments, content_durs)
    ]


def make_text_clip(text, duration, position, fontsize, color="white", bg_opacity=0.6):
    """Create a text clip with semi-transparent background."""
    from moviepy import ImageClip, CompositeVideoClip
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    # Render text using PIL for better control
    font = None
    if _VIDEO_FONT_PATH and os.path.exists(_VIDEO_FONT_PATH):
        try:
            font = ImageFont.truetype(_VIDEO_FONT_PATH, fontsize)
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    # Calculate text size
    dummy_img = Image.new("RGBA", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    bbox = dummy_draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    padding_x, padding_y = 30, 15
    img_w = text_w + padding_x * 2
    img_h = text_h + padding_y * 2

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Semi-transparent indigo-purple background (#5856D6)
    bg_alpha = int(bg_opacity * 255)
    draw.rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=12,
                            fill=(88, 86, 214, bg_alpha))

    # White text
    draw.text((padding_x, padding_y), text, font=font, fill=(255, 255, 255, 255))

    # Convert to numpy for moviepy
    img_array = np.array(img)

    import moviepy.video.fx as vfx
    clip = ImageClip(img_array, is_mask=False).with_duration(duration)
    clip = clip.with_position(position)

    # Fade in/out
    clip = clip.with_effects([vfx.CrossFadeIn(FADE_DURATION), vfx.CrossFadeOut(FADE_DURATION)])

    return clip


def make_reveal_text_clip(text, seg_duration, fontsize=OVERLAY_FONTSIZE, bg_opacity=0.6):
    """
    Render overlay_text at top-left with a left-to-right wipe-in animation.
    Shows for min(OVERLAY_DISPLAY_DURATION, seg_duration) seconds, then fades out.
    Prepends a bullet point and wraps long text to avoid going off-screen right edge.
    """
    from moviepy import VideoClip
    from PIL import Image, ImageDraw, ImageFont

    display_dur = min(OVERLAY_DISPLAY_DURATION, seg_duration)

    # Heavy/bold font for thick heading-style overlay
    font = None
    _overlay_font = _VIDEO_FONT_PATH_OVERLAY or _VIDEO_FONT_PATH
    if _overlay_font and os.path.exists(_overlay_font):
        try:
            font = ImageFont.truetype(_overlay_font, fontsize)
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    # Greedy word-wrap: build lines that stay within OVERLAY_MAX_TEXT_WIDTH
    dummy = Image.new("RGBA", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy)

    def _tw(t):
        return dummy_draw.textbbox((0, 0), t, font=font)[2]

    words = text.split()
    lines = []
    current = "\u2022 "
    for word in words:
        test = current + word
        if _tw(test) <= OVERLAY_MAX_TEXT_WIDTH or current == "\u2022 ":
            current = test + " "
        else:
            lines.append(current.rstrip())
            current = "   " + word + " "   # indent continuation lines
    if current.strip():
        lines.append(current.rstrip())
    wrapped = "\n".join(lines)

    pad_x, pad_y = 30, 15
    bbox = dummy_draw.textbbox((0, 0), wrapped, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    img_w = text_w + pad_x * 2
    img_h = text_h + pad_y + pad_y * 3   # extra bottom padding prevents descender clipping

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=12,
                            fill=(0, 0, 0, int(bg_opacity * 255)))
    # Anchor text so bbox top-left lands at (pad_x, pad_y), fixing font-specific offsets
    tx = pad_x - bbox[0]
    ty = pad_y - bbox[1]
    draw.text((tx, ty), wrapped, font=font, fill=_VIDEO_COLOR_OVERLAY)

    full_rgba  = np.array(img)
    full_rgb   = full_rgba[:, :, :3]
    full_alpha = full_rgba[:, :, 3].astype(np.float32) / 255.0
    width      = full_rgb.shape[1]
    fade_start = display_dur - FADE_DURATION

    def make_frame(t):
        reveal_x = int(width * min(t / OVERLAY_REVEAL_DURATION, 1.0))
        frame = np.zeros_like(full_rgb)
        if reveal_x > 0:
            frame[:, :reveal_x] = full_rgb[:, :reveal_x]
        return frame

    def make_mask_frame(t):
        reveal_x = int(width * min(t / OVERLAY_REVEAL_DURATION, 1.0))
        fade_factor = max((display_dur - t) / FADE_DURATION, 0.0) if t >= fade_start else 1.0
        mask = np.zeros_like(full_alpha)
        if reveal_x > 0:
            mask[:, :reveal_x] = full_alpha[:, :reveal_x] * fade_factor
        return mask

    clip = VideoClip(make_frame, duration=display_dur)
    mask_clip = VideoClip(make_mask_frame, is_mask=True, duration=display_dur)
    clip = clip.with_mask(mask_clip).with_position((OVERLAY_MARGIN, OVERLAY_MARGIN))
    return clip


def make_chapter_transition_card(text, duration=CHAPTER_CARD_DURATION):
    """
    Full-frame black screen with centered bold white chapter title.
    Fades in over 0.3s, holds, then fades out over 0.3s.
    Inserted before each point_N segment to signal a new section.
    Uses ImageClip (not VideoClip) so .size is properly declared and the
    card reliably covers the full frame in CompositeVideoClip.
    """
    from PIL import Image, ImageDraw, ImageFont

    W, H = TARGET_WIDTH, TARGET_HEIGHT
    FONTSIZE = 90
    FADE = 0.3

    font = None
    _overlay_font = _VIDEO_FONT_PATH_OVERLAY or _VIDEO_FONT_PATH
    if _overlay_font and os.path.exists(_overlay_font):
        try:
            font = ImageFont.truetype(_overlay_font, FONTSIZE)
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # Anchor text so bbox top-left lands at center, avoiding descender offset
    tx = (W - tw) // 2 - bbox[0]
    ty = (H - th) // 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=_VIDEO_COLOR_CHAPTER[:3])
    frame_arr = np.array(img)  # shape (H, W, 3)

    def make_frame(t):
        if t < FADE:
            alpha = t / FADE
        elif t > duration - FADE:
            alpha = max((duration - t) / FADE, 0.0)
        else:
            alpha = 1.0
        return (frame_arr * alpha).astype(np.uint8)

    from moviepy import VideoClip as VC
    return VC(make_frame, duration=duration).with_position((0, 0))


def make_watermark(channel_name, total_duration):
    """
    Create channel watermark using PIL so descenders (y, g, p) are never clipped.
    Positioned top-right with 30px horizontal and 20px vertical margin.
    """
    from moviepy import ImageClip
    from PIL import Image, ImageDraw, ImageFont

    font = None
    _caption_font = _VIDEO_FONT_PATH_CAPTION or _VIDEO_FONT_PATH
    if _caption_font and os.path.exists(_caption_font):
        try:
            font = ImageFont.truetype(_caption_font, WATERMARK_FONTSIZE)
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    dummy = Image.new("RGBA", (1, 1))
    bbox = ImageDraw.Draw(dummy).textbbox((0, 0), channel_name, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x, pad_y = 6, 4
    img_w = text_w + pad_x * 2
    img_h = text_h + pad_y + pad_y * 3   # generous bottom padding for descenders

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    tx = pad_x - bbox[0]
    ty = pad_y - bbox[1]
    draw.text((tx, ty), channel_name, font=font, fill=(255, 255, 255, 255))

    img_arr = np.array(img)
    x_pos = TARGET_WIDTH - img_w - 30
    y_pos = 20

    try:
        wm = (ImageClip(img_arr, is_mask=False)
              .with_duration(total_duration)
              .with_opacity(0.3)
              .with_position((x_pos, y_pos)))
        return wm
    except Exception as e:
        print(f"  WARNING: Could not create watermark: {e}", file=sys.stderr)
        return None


def apply_color_grade(clip):
    """Apply a subtle teal-orange cinematic grade to a footage clip."""
    def grade_frame(frame):
        img = frame.astype(np.float32) / 255.0
        # Slight S-curve contrast boost
        img = np.clip(0.5 + 1.15 * (img - 0.5), 0, 1)
        # Teal-orange grade: shadows → teal, highlights → orange
        luma = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
        shadow_mask = np.clip(1.0 - luma * 2, 0, 1)[:, :, None]
        highlight_mask = np.clip(luma * 2 - 1, 0, 1)[:, :, None]
        # Teal shadows: reduce red, boost blue
        img[:, :, 0] = np.clip(img[:, :, 0] - 0.03 * shadow_mask[:, :, 0], 0, 1)
        img[:, :, 2] = np.clip(img[:, :, 2] + 0.05 * shadow_mask[:, :, 0], 0, 1)
        # Orange highlights: boost red, reduce blue
        img[:, :, 0] = np.clip(img[:, :, 0] + 0.05 * highlight_mask[:, :, 0], 0, 1)
        img[:, :, 2] = np.clip(img[:, :, 2] - 0.03 * highlight_mask[:, :, 0], 0, 1)
        return (img * 255).astype(np.uint8)
    return clip.image_transform(grade_frame)


def make_zoom_fn(duration, zoom_in=True, kb_scale=0.05):
    """Ken Burns slow zoom only (1.0 → 1.05 or 1.05 → 1.0 over full duration)."""
    def zoom_fn(t):
        if zoom_in:
            return 1.0 + kb_scale * (t / duration)
        else:
            return 1.0 + kb_scale - kb_scale * (t / duration)
    return zoom_fn


def group_words_into_chunks(words, chunk_size=CAPTION_CHUNK_SIZE):
    """Group word timestamps into display chunks, breaking early at sentence punctuation."""
    chunks = []
    current = []
    for w in words:
        current.append(w)
        word_text = w["word"].rstrip()
        if len(current) >= chunk_size or word_text.endswith(('.', '!', '?', ',', ';', ':')):
            chunks.append({
                "text": " ".join(x["word"].strip() for x in current),
                "start": current[0]["start"],
                "end": current[-1]["end"],
            })
            current = []
    if current:
        chunks.append({
            "text": " ".join(x["word"].strip() for x in current),
            "start": current[0]["start"],
            "end": current[-1]["end"],
        })
    return chunks


def make_caption_chunk_clip(text, start, end):
    """
    Render a caption chunk as a transparent full-frame RGBA ImageClip.
    White bold text with thick black outline, positioned center-bottom.
    """
    from moviepy import ImageClip
    from PIL import Image, ImageDraw, ImageFont

    duration = max(end - start, 0.05)
    fontsize = CAPTION_FONTSIZE

    font = None
    _caption_font = _VIDEO_FONT_PATH_CAPTION or _VIDEO_FONT_PATH
    if _caption_font and os.path.exists(_caption_font):
        try:
            font = ImageFont.truetype(_caption_font, fontsize)
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    # Measure text dimensions
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Small canvas sized to the text + stroke padding (avoids ~1.6 GB full-frame alloc)
    stroke = 4
    pad = stroke  # enough room for all outline offsets
    img_w = text_w + pad * 2
    img_h = text_h + pad * 2

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Anchor so the bounding box top-left lands exactly at (pad, pad),
    # regardless of font-specific bbox offsets (fixes descender clipping).
    tx = pad - bbox[0]
    ty = pad - bbox[1]

    # Thick black outline (8-directional offset) at local origin
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx != 0 or dy != 0:
                draw.text((tx + dx, ty + dy), text, font=font, fill=(0, 0, 0, 255))

    # Caption text color (picked once per video)
    draw.text((tx, ty), text, font=font, fill=_VIDEO_COLOR_CAPTION)

    # Position on the final composite: centred horizontally, ~82% down
    x_pos = (TARGET_WIDTH - img_w) // 2
    y_pos = int(TARGET_HEIGHT * 0.82) - text_h - pad

    img_array = np.array(img)
    clip = (ImageClip(img_array, is_mask=False)
            .with_duration(duration)
            .with_start(start)
            .with_position((x_pos, y_pos)))
    return clip


def process_image_clip(image_path: str, needed_duration: float):
    """Load a still image (JPEG/PNG) as a VideoClip with slow Ken Burns zoom."""
    from moviepy import ImageClip
    import moviepy.video.fx as vfx
    from PIL import Image
    import numpy as _np

    img = Image.open(image_path).convert("RGB")
    # Crop to fill 16:9 without stretching
    target_ratio = TARGET_WIDTH / TARGET_HEIGHT
    img_ratio = img.width / img.height
    if img_ratio > target_ratio:
        new_w = int(img.height * target_ratio)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, img.height))
    else:
        new_h = int(img.width / target_ratio)
        top = (img.height - new_h) // 2
        img = img.crop((0, top, img.width, top + new_h))
    img = img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)

    arr = _np.array(img)
    img.close()  # release file descriptor immediately after numpy conversion
    clip = ImageClip(arr, is_mask=False).with_duration(needed_duration)
    clip = apply_color_grade(clip)
    clip = clip.with_effects([vfx.Resize(make_zoom_fn(needed_duration, zoom_in=True))])
    return clip


def process_segment_clip(clip_path, needed_duration):
    """Load, resize, and trim/loop a stock footage clip. Routes images to process_image_clip."""
    if is_image_file(clip_path):
        return process_image_clip(clip_path, needed_duration)

    from moviepy import VideoFileClip, concatenate_videoclips
    import moviepy.video.fx as vfx

    # Track raw VideoFileClip so caller can close its ffmpeg decode subprocess explicitly.
    # moviepy's ConcatenateClip.close() does NOT recursively close child VideoFileClips,
    # so without explicit tracking the ffmpeg subprocesses accumulate across batches.
    _raw_clip = VideoFileClip(clip_path, audio=False)
    clip = _raw_clip.with_effects([vfx.Resize((TARGET_WIDTH, TARGET_HEIGHT))])

    if clip.duration < needed_duration:
        # Loop the clip
        repeats = math.ceil(needed_duration / clip.duration)
        clip = concatenate_videoclips([clip] * repeats)

    clip = clip.subclipped(0, needed_duration)

    # Color grade
    clip = apply_color_grade(clip)

    # Ken Burns + zoom punch at start
    clip = clip.with_effects([vfx.Resize(make_zoom_fn(needed_duration, zoom_in=True))])

    # Attach raw clip for explicit cleanup by caller after rendering
    clip._src_clips_to_close = [_raw_clip]
    return clip


def resolve_clip_list(manifest_entry, footage_dir):
    """
    Normalize manifest entry to a list of absolute clip paths.
    Supports both old string format and new list format.
    """
    if isinstance(manifest_entry, list):
        paths = [os.path.join(footage_dir, f) for f in manifest_entry]
    else:
        paths = [os.path.join(footage_dir, manifest_entry)]
    return [p for p in paths if os.path.exists(p)]


def assemble_segment_with_cuts(clip_paths, total_duration, segment_id):
    """
    Build a segment clip with fast cuts every 3-6 seconds.

    Args:
        clip_paths: List of absolute paths to source clips (1 or more)
        total_duration: Exact duration this segment must fill (seconds)
        segment_id: Used as random seed base for reproducibility

    Returns:
        A single VideoClip of exactly total_duration seconds
    """
    from moviepy import VideoFileClip, concatenate_videoclips, ColorClip
    import moviepy.video.fx as vfx

    # Single clip: delegate to original behavior (it also sets _src_clips_to_close)
    if len(clip_paths) == 1:
        return process_segment_clip(clip_paths[0], total_duration)

    # ── Step 1: Generate sub-clip durations summing to total_duration ────────
    rng = random.Random(segment_id * 1000)

    sub_durations = []
    remaining = total_duration
    while remaining > SUB_CLIP_MAX:
        d = round(rng.uniform(SUB_CLIP_MIN, SUB_CLIP_MAX), 2)
        sub_durations.append(d)
        remaining -= d
    if remaining > 0:
        sub_durations.append(max(remaining, 0.5))

    # ── Step 2: Track per-clip offset to avoid reusing the same start time ──
    clip_offsets = {}

    # ── Step 3: Build sub-clips ───────────────────────────────────────────────
    sub_clips = []
    zoom_in = True  # alternate Ken Burns direction per cut
    # Track all raw VideoFileClip objects so caller can close their ffmpeg decode
    # subprocesses after rendering. moviepy's ConcatenateClip.close() does NOT
    # recurse into child clips, so without this, ffmpeg processes accumulate.
    src_clips_to_close = []

    for sub_idx, sub_duration in enumerate(sub_durations):
        clip_path = clip_paths[sub_idx % len(clip_paths)]

        try:
            # Route still images (news photos, Pexels photos) to image handler
            if is_image_file(clip_path):
                sub_clip = process_image_clip(clip_path, sub_duration)
                zoom_in = not zoom_in
                sub_clips.append(sub_clip)
                continue

            _raw_src = VideoFileClip(clip_path, audio=False)
            src_clips_to_close.append(_raw_src)
            src = _raw_src.with_effects([vfx.Resize((TARGET_WIDTH, TARGET_HEIGHT))])

            # Determine start time — walk forward through clip, never replay same section
            if clip_path not in clip_offsets:
                max_initial = min(src.duration * 0.25, 5.0)
                clip_offsets[clip_path] = round(rng.uniform(0, max_initial), 2)

            start = clip_offsets[clip_path]

            # If this clip is exhausted, try cycling to another clip that still has runway
            if start + sub_duration > src.duration:
                # Find a different clip with enough remaining footage
                fallback = None
                for alt_path in clip_paths:
                    if alt_path == clip_path or is_image_file(alt_path):
                        continue
                    alt_offset = clip_offsets.get(alt_path, 0.0)
                    try:
                        alt_src = VideoFileClip(alt_path, audio=False)
                        alt_dur = alt_src.duration
                        alt_src.close()
                    except Exception:
                        continue
                    if alt_offset + sub_duration <= alt_dur:
                        fallback = alt_path
                        break

                if fallback:
                    # Switch to the fallback clip for this sub-clip
                    src.close()
                    clip_path = fallback
                    _raw_src = VideoFileClip(clip_path, audio=False)
                    src_clips_to_close.append(_raw_src)
                    src = _raw_src.with_effects([vfx.Resize((TARGET_WIDTH, TARGET_HEIGHT))])
                    start = clip_offsets.get(clip_path, 0.0)
                else:
                    # All clips exhausted — restart the current one from the beginning
                    start = 0.0
                    clip_offsets[clip_path] = 0.0

            # Advance offset for next use of this clip
            clip_offsets[clip_path] = start + sub_duration + 1.0

            actual_end = min(start + sub_duration, src.duration)
            sub_clip = src.subclipped(start, actual_end)

            # Pad to exact sub_duration if we got less (near end of clip)
            if sub_clip.duration < sub_duration - 0.1:
                from moviepy import ColorClip as CC
                pad = CC((TARGET_WIDTH, TARGET_HEIGHT), color=(0, 0, 0),
                         duration=sub_duration - sub_clip.duration)
                sub_clip = concatenate_videoclips([sub_clip, pad])

            # Color grade
            sub_clip = apply_color_grade(sub_clip)

            # Ken Burns + zoom punch (alternate direction per cut)
            sub_clip = sub_clip.with_effects([vfx.Resize(make_zoom_fn(sub_duration, zoom_in=zoom_in))])
            zoom_in = not zoom_in

            sub_clips.append(sub_clip)

        except Exception as e:
            print(f"  WARNING: Sub-clip {sub_idx} failed for {clip_path}: {e}", file=sys.stderr)
            from moviepy import ColorClip as CC
            sub_clips.append(CC((TARGET_WIDTH, TARGET_HEIGHT), color=(0, 0, 0),
                                duration=sub_duration))

    if not sub_clips:
        from moviepy import ColorClip as CC
        return CC((TARGET_WIDTH, TARGET_HEIGHT), color=(0, 0, 0), duration=total_duration)

    # ── Step 4: Concatenate with hard cuts ───────────────────────────────────
    segment_clip = concatenate_videoclips(sub_clips, method="compose")

    # Trim to exact duration (float drift)
    if segment_clip.duration > total_duration + 0.1:
        segment_clip = segment_clip.subclipped(0, total_duration)

    # Attach raw VideoFileClip list for explicit cleanup after rendering
    segment_clip._src_clips_to_close = src_clips_to_close
    return segment_clip


def find_chapter_content_start(captions_words, seg_text, estimated_start, search_window=20.0):
    """Find the timestamp just before the first spoken words of seg_text in captions.

    Matches the first 2 words of the segment text (case-insensitive, punctuation-stripped)
    against captions words within search_window seconds of estimated_start.

    Returns the end-time of the last caption word BEFORE the chapter begins, or None.
    """
    import re

    def clean(w):
        return re.sub(r"[^\w]", "", w.lower())

    # Clean segment text and extract first words, skipping "Point N:" prefix
    raw = re.sub(r"[^\w\s]", " ", seg_text.lower()).split()
    if len(raw) >= 2 and raw[0] == "point" and raw[1] in (
        "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "1", "2", "3", "4", "5"
    ):
        raw = raw[2:]
    search_seq = raw[:3]

    if len(search_seq) < 2:
        return None

    t_min = max(0.0, estimated_start - search_window)
    t_max = estimated_start + search_window
    candidates = [i for i, w in enumerate(captions_words) if t_min <= w["start"] <= t_max]

    for ci in candidates:
        if ci + 1 >= len(captions_words):
            continue
        if (clean(captions_words[ci]["word"]) == search_seq[0] and
                clean(captions_words[ci + 1]["word"]) == search_seq[1]):
            return captions_words[ci - 1]["end"] if ci > 0 else captions_words[ci]["start"]

    return None


def find_pause_near(captions_words, target_time, search_window=10.0, min_gap=0.15):
    """Find the end-time of the word just before the largest inter-word pause near target_time.

    A gap ≥ min_gap seconds between consecutive words usually marks a sentence boundary.
    Returns None if no suitable pause found.
    """
    t_min = max(0.0, target_time - search_window)
    t_max = target_time + search_window
    best_gap = 0.0
    best_end = None
    for i in range(len(captions_words) - 1):
        w1 = captions_words[i]
        w2 = captions_words[i + 1]
        if w1["end"] < t_min or w1["start"] > t_max:
            continue
        gap = w2["start"] - w1["end"]
        if gap > best_gap:
            best_gap = gap
            best_end = w1["end"]
    return best_end if best_gap >= min_gap else None


def build_chapter_pause_audio(voiceover, insertion_points):
    """Insert silence at pre-computed insertion_points into the voiceover.

    Instead of muting (which drops words), we PAUSE: split the voiceover at a sentence
    boundary, insert CHAPTER_CARD_DURATION of silence, then resume.  No words are lost.
    """
    from moviepy import concatenate_audioclips
    from moviepy.audio.AudioClip import AudioClip

    if not insertion_points:
        return voiceover

    nchannels = voiceover.nchannels
    fps = voiceover.fps

    def make_silence(dur):
        def frame(t):
            t = np.atleast_1d(t)
            return np.zeros((len(t), nchannels))
        return AudioClip(frame_function=frame, duration=dur, fps=fps)

    pieces = []
    cursor = 0.0
    for ins_time, silence_dur in sorted(insertion_points):
        ins_time = max(ins_time, cursor)
        end = min(ins_time, voiceover.duration)
        if end > cursor:
            pieces.append(voiceover.subclipped(cursor, end))
        pieces.append(make_silence(silence_dur))
        cursor = ins_time

    if cursor < voiceover.duration:
        pieces.append(voiceover.subclipped(cursor))

    return concatenate_audioclips(pieces)


def shift_captions(chunks, insertion_points):
    """Shift caption chunk timestamps forward by cumulative silence durations.

    Captions that fall inside a silence window are suppressed (no audio → no text).
    """
    if not insertion_points:
        return chunks

    sorted_ins = sorted(insertion_points)

    # Compute video-time silence windows for suppression
    silence_windows = []
    cumulative = 0.0
    for ins_t, dur in sorted_ins:
        v_start = ins_t + cumulative
        silence_windows.append((v_start, v_start + dur))
        cumulative += dur

    shifted = []
    for chunk in chunks:
        t = chunk["start"]
        shift = sum(dur for ins_t, dur in insertion_points if ins_t <= t)
        v_start = t + shift
        v_end = chunk["end"] + shift
        # Suppress captions that fall inside a silence/chapter card window
        if any(ws <= v_start < we for ws, we in silence_windows):
            continue
        shifted.append({"text": chunk["text"], "start": v_start, "end": v_end})
    return shifted


def build_sfx_events(valid_segments, segment_durations):
    """Return list of (start_time_seconds, sfx_name) based on segment type."""
    events = []
    cumulative = 0.0
    for seg, dur in zip(valid_segments, segment_durations):
        seg_type = seg.get("type", "")
        if seg_type == "hook":
            events.append((cumulative, "riser"))
        elif seg_type == "cta":
            events.append((cumulative, "woosh"))
        elif seg_type.startswith("pattern_interrupt") and seg.get("sfx"):
            events.append((cumulative, seg["sfx"]))
        elif seg_type == "engagement" and seg.get("sfx"):
            events.append((cumulative, seg["sfx"]))
        cumulative += dur
    return events


def load_sfx_clips(sfx_dir):
    """Load available SFX AudioFileClips. Missing files are skipped gracefully."""
    from moviepy import AudioFileClip
    import moviepy.audio.fx as afx
    loaded = {}
    for name, volume in SFX_VOLUMES.items():
        for ext in ("mp3", "wav"):
            path = os.path.join(sfx_dir, f"{name}.{ext}")
            if os.path.exists(path):
                try:
                    clip = AudioFileClip(path).with_effects([afx.MultiplyVolume(volume)])
                    loaded[name] = clip
                    print(f"  SFX: loaded '{name}' at {int(volume*100)}%", file=sys.stderr)
                except Exception as e:
                    print(f"  SFX WARNING: could not load '{name}': {e}", file=sys.stderr)
                break
        else:
            print(f"  SFX: '{name}' not found in {sfx_dir}, skipping.", file=sys.stderr)
    return loaded


def main():
    parser = argparse.ArgumentParser(description="Assemble final video")
    parser.add_argument("--script-file", required=True)
    parser.add_argument("--audio-file", required=True)
    parser.add_argument("--footage-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--captions-file", default=None, help="Optional path to word-level captions JSON")
    parser.add_argument("--batch-size", type=int, default=3,
                        help="Segments per batch when 5+ segments present (prevents OOM)")
    args = parser.parse_args()

    for path, name in [(args.script_file, "Script"), (args.audio_file, "Audio"),
                       (args.footage_dir, "Footage dir")]:
        if not os.path.exists(path):
            print(f"ERROR: {name} not found: {path}", file=sys.stderr)
            sys.exit(1)

    with open(args.script_file) as f:
        script = json.load(f)

    manifest_path = os.path.join(args.footage_dir, "footage_manifest.json")
    if not os.path.exists(manifest_path):
        print(f"ERROR: Footage manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    segments = script.get("segments", [])
    channel_name = os.getenv("CHANNEL_NAME", "")

    # Filter to only segments that have footage
    valid_segments = [s for s in segments if str(s.get("segment_id", "")) in manifest]
    if not valid_segments:
        print("ERROR: No segments have associated footage.", file=sys.stderr)
        sys.exit(1)

    print(f"Assembling {len(valid_segments)} segments...", file=sys.stderr)

    # Load captions words early — needed for sentence boundary detection during audio build
    captions_words_raw = []
    if args.captions_file and os.path.exists(args.captions_file):
        with open(args.captions_file) as f:
            captions_words_raw = json.load(f)

    # Get total audio duration and distribute across segments
    total_audio = get_audio_duration(args.audio_file)
    print(f"Total audio duration: {total_audio:.1f}s", file=sys.stderr)

    content_durations = compute_content_durations(valid_segments, total_audio)
    segment_durations = calculate_segment_durations(valid_segments, total_audio)

    # Pre-compute chapter pause insertion points (needed for captions AND audio)
    _audio_cursor = 0.0
    insertion_points = []
    for _seg, _cdur in zip(valid_segments, content_durations):
        if _seg.get("type", "").startswith("point_") and _seg.get("chapter_title"):
            _natural = _audio_cursor
            _boundary = find_chapter_content_start(
                captions_words_raw, _seg.get("text", ""), _natural
            ) if captions_words_raw else None
            if _boundary is None and captions_words_raw:
                _boundary = find_pause_near(captions_words_raw, _natural)
            _ins = _boundary if _boundary is not None else _natural
            _method = "word match" if _boundary and _boundary != _natural else (
                "pause detect" if _boundary else "fallback")
            insertion_points.append((_ins, CHAPTER_CARD_DURATION))
            print(f"  Chapter pause: \"{_seg.get('chapter_title')}\" silence at {_ins:.1f}s ({_method})", file=sys.stderr)
        _audio_cursor += _cdur

    from moviepy import (
        AudioFileClip,
        CompositeVideoClip,
        concatenate_videoclips,
    )

    output_dir = os.path.dirname(args.output) or "."
    os.makedirs(output_dir, exist_ok=True)

    pick_video_style()

    # ── Disk space guard ─────────────────────────────────────────────────────
    _free_gb = shutil.disk_usage(output_dir).free / (1024 ** 3)
    print(f"Disk free: {_free_gb:.1f} GB", file=sys.stderr)
    if _free_gb < 20:
        print(
            f"ERROR: Only {_free_gb:.1f} GB disk free — need ≥20 GB for video assembly. "
            "Free disk space first, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Clean stale partials_tmp from any prior crashed run ──────────────────
    _stale = os.path.join(output_dir, "partials_tmp")
    if os.path.isdir(_stale):
        shutil.rmtree(_stale, ignore_errors=True)
        print("Cleaned stale partials_tmp from prior run.", file=sys.stderr)

    text_clips_by_segment = []
    # Always use batch mode regardless of segment count.
    # The non-batch path holds ALL segment clips in memory simultaneously;
    # batch mode writes each batch to disk and frees memory between batches.
    use_batch_mode = True
    batch_tmp_dir = None

    def _build_one_segment(seg, duration, global_i):
        """Build a single segment clip. Returns vid_clip."""
        seg_id = seg.get("segment_id", 0)
        manifest_entry = manifest[str(seg_id)]
        clip_paths = resolve_clip_list(manifest_entry, args.footage_dir)
        print(f"  Processing segment {seg_id} ({duration:.1f}s, {len(clip_paths)} clip(s))...",
              file=sys.stderr)
        if not clip_paths:
            print(f"  WARNING: No valid clip files found for segment {seg_id}.", file=sys.stderr)
            from moviepy import ColorClip
            return ColorClip((TARGET_WIDTH, TARGET_HEIGHT), color=(0, 0, 0), duration=duration)
        try:
            return assemble_segment_with_cuts(clip_paths, duration, seg_id)
        except Exception as e:
            print(f"  WARNING: Failed to assemble segment {seg_id}: {e}", file=sys.stderr)
            from moviepy import ColorClip
            return ColorClip((TARGET_WIDTH, TARGET_HEIGHT), color=(0, 0, 0), duration=duration)

    if use_batch_mode:
        # ── BATCH MODE: render segments in batches, concat with ffmpeg ──────
        print(f"Batch mode: {len(valid_segments)} segments in batches of {args.batch_size}.",
              file=sys.stderr)
        batch_tmp_dir = os.path.join(output_dir, "partials_tmp")
        os.makedirs(batch_tmp_dir, exist_ok=True)
        partial_files = []

        # try/finally ensures partials_tmp is cleaned up even if rendering crashes mid-run.
        # (Stale partial files from a crash consume disk space and limit swap growth.)
        _batch_failed = False
        try:
            for batch_start in range(0, len(valid_segments), args.batch_size):
                batch_end = min(batch_start + args.batch_size, len(valid_segments))
                batch_segs = valid_segments[batch_start:batch_end]
                batch_durs = segment_durations[batch_start:batch_end]
                batch_clips = []

                for local_i, (seg, duration) in enumerate(zip(batch_segs, batch_durs)):
                    global_i = batch_start + local_i
                    vid_clip = _build_one_segment(seg, duration, global_i)
                    batch_clips.append(vid_clip)
                    overlay_text = seg.get("overlay_text")
                    if overlay_text:
                        text_clips_by_segment.append((global_i, overlay_text, duration))

                batch_num = batch_start // args.batch_size
                partial_path = os.path.join(batch_tmp_dir, f"partial_{batch_num:03d}.mp4")
                print(f"  Rendering batch {batch_num} to {os.path.basename(partial_path)}...",
                      file=sys.stderr)
                bv = concatenate_videoclips(batch_clips, method="compose")
                bv.write_videofile(partial_path, codec="libx264", audio=False,
                                   fps=FPS, preset="fast", threads=2, logger=None)
                # Explicitly close raw VideoFileClip objects (ffmpeg decode subprocesses).
                # moviepy's ConcatenateClip.close() does NOT recurse into child clips,
                # so without this, ffmpeg subprocesses accumulate across all batches.
                for c in batch_clips:
                    for raw in getattr(c, "_src_clips_to_close", []):
                        try: raw.close()
                        except Exception: pass
                    try: c.close()
                    except Exception: pass
                bv.close()
                del batch_clips, bv
                import gc as _gc; _gc.collect()
                partial_files.append(partial_path)

        except Exception:
            _batch_failed = True
            raise

        finally:
            # If batch rendering failed, clean up immediately so disk space is freed.
            # On success, we still need batch_tmp_dir for silent_combined — cleanup
            # happens at the end of main() after the final render is done.
            if _batch_failed and os.path.isdir(batch_tmp_dir):
                shutil.rmtree(batch_tmp_dir, ignore_errors=True)

        if not partial_files:
            print("ERROR: No partial files rendered.", file=sys.stderr)
            sys.exit(1)

        # ffmpeg concat partials (stream copy — no re-encode)
        concat_list_path = os.path.join(batch_tmp_dir, "concat_list.txt")
        silent_combined = os.path.join(batch_tmp_dir, "silent_combined.mp4")
        with open(concat_list_path, "w") as cf:
            for pf in partial_files:
                # ffmpeg concat demuxer resolves paths relative to the list file's directory,
                # so use absolute paths to avoid path confusion.
                cf.write(f"file '{os.path.abspath(pf)}'\n")
        print("Concatenating partial files with ffmpeg...", file=sys.stderr)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", concat_list_path, "-c", "copy", silent_combined],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        for pf in partial_files:
            try: os.remove(pf)
            except Exception: pass

        from moviepy import VideoFileClip
        final_video = VideoFileClip(silent_combined, audio=False)

    else:
        # ── ORIGINAL PATH: build all clips in memory, concatenate ───────────
        segment_clips = []
        for i, (seg, duration) in enumerate(zip(valid_segments, segment_durations)):
            vid_clip = _build_one_segment(seg, duration, i)
            segment_clips.append(vid_clip)
            overlay_text = seg.get("overlay_text")
            if overlay_text:
                text_clips_by_segment.append((i, overlay_text, duration))

        if not segment_clips:
            print("ERROR: No segment clips could be processed.", file=sys.stderr)
            sys.exit(1)

        print("Concatenating segments...", file=sys.stderr)
        final_video = concatenate_videoclips(segment_clips, method="compose")

    # Chapter transition cards — full-frame overlays positioned at the actual audio silence time.
    # video_time of j-th card = ins_time_j + sum(prior silence durations)
    overlay_clips = []
    # Maps seg_idx -> chapter card video start time, for text overlay alignment.
    chapter_card_start_times = {}
    chapter_card_idx = 0
    prior_silence_total = 0.0
    for i, (seg, duration) in enumerate(zip(valid_segments, segment_durations)):
        chapter_title = seg.get("chapter_title", "")
        if seg.get("type", "").startswith("point_") and chapter_title:
            if chapter_card_idx < len(insertion_points):
                ins_time, silence_dur = insertion_points[chapter_card_idx]
                start_time = ins_time + prior_silence_total
                prior_silence_total += silence_dur
            else:
                start_time = sum(segment_durations[:i])  # fallback if no insertion point
            chapter_card_idx += 1
            chapter_card_start_times[i] = start_time
            try:
                card = make_chapter_transition_card(chapter_title)
                overlay_clips.append(card.with_start(start_time))
                print(f"  Chapter card: \"{chapter_title}\" at t={start_time:.1f}s", file=sys.stderr)
            except Exception as e:
                print(f"  WARNING: Chapter card failed for '{chapter_title}': {e}", file=sys.stderr)

    # Inject woosh SFX at each chapter card's visual start time
    chapter_card_sfx = [(t, "woosh") for t in chapter_card_start_times.values()]

    # Build text overlay clips — top-left, bullet point, left-to-right reveal
    # Delayed by CHAPTER_CARD_DURATION when the segment starts with a chapter card.
    for i, (seg_idx, text, duration) in enumerate(text_clips_by_segment):
        if seg_idx in chapter_card_start_times:
            start_time = chapter_card_start_times[seg_idx] + CHAPTER_CARD_DURATION
            chapter_offset = CHAPTER_CARD_DURATION
        else:
            start_time = sum(segment_durations[:seg_idx])
            chapter_offset = 0.0
        adjusted_duration = max(duration - chapter_offset, 1.0)
        try:
            txt_clip = make_reveal_text_clip(text, seg_duration=adjusted_duration)
            txt_clip = txt_clip.with_start(start_time)
            overlay_clips.append(txt_clip)
        except Exception as e:
            print(f"  WARNING: Text overlay failed for segment {seg_idx}: {e}", file=sys.stderr)

    # Word-by-word captions
    if captions_words_raw:
        print("Adding word-by-word captions...", file=sys.stderr)
        chunks = group_words_into_chunks(captions_words_raw)
        chunks = shift_captions(chunks, insertion_points)

        # Suppress captions during CTA segment if it has overlay_text (avoid clutter)
        cta_no_caption_start = None
        cta_no_caption_end = None
        t = 0.0
        for seg, dur in zip(valid_segments, segment_durations):
            if seg.get("type") == "cta" and seg.get("overlay_text"):
                cta_no_caption_start = t
                cta_no_caption_end = t + dur
                break
            t += dur

        video_duration = final_video.duration
        cap_ok = 0
        cap_skipped = 0
        for chunk in chunks:
            # Skip captions that fall inside the CTA overlay window
            if (cta_no_caption_start is not None
                    and cta_no_caption_start <= chunk["start"] < cta_no_caption_end):
                cap_skipped += 1
                continue
            # Skip captions that start at or after video end
            if chunk["start"] >= video_duration:
                cap_skipped += 1
                continue
            caption_end = min(chunk["end"], video_duration)
            try:
                cap_clip = make_caption_chunk_clip(chunk["text"], chunk["start"], caption_end)
                overlay_clips.append(cap_clip)
                cap_ok += 1
            except Exception as e:
                print(f"  WARNING: Caption chunk failed '{chunk['text']}': {e}", file=sys.stderr)
        print(f"  {cap_ok}/{len(chunks)} caption chunks added"
              f"{f' ({cap_skipped} suppressed during CTA)' if cap_skipped else ''}.",
              file=sys.stderr)

    # Watermark
    if channel_name:
        wm = make_watermark(channel_name, final_video.duration)
        if wm:
            overlay_clips.append(wm)

    # Compose final video with overlays
    if overlay_clips:
        final_video = CompositeVideoClip(
            [final_video] + overlay_clips,
            size=(TARGET_WIDTH, TARGET_HEIGHT),
        )

    # Add audio — insert silence at sentence boundaries before chapter cards (no words lost)
    voiceover = AudioFileClip(args.audio_file)
    voiceover = build_chapter_pause_audio(voiceover, insertion_points)

    # SFX
    sfx_events = build_sfx_events(valid_segments, segment_durations) + chapter_card_sfx
    sfx_dir = os.getenv("SFX_DIR", os.path.join(PROJECT_ROOT, "sfx")).strip()
    sfx_loaded = {}
    if sfx_dir and os.path.isdir(sfx_dir):
        sfx_loaded = load_sfx_clips(sfx_dir)
    elif sfx_dir:
        print(f"  SFX: directory not found ({sfx_dir}), skipping.", file=sys.stderr)

    sfx_audio_clips = []
    for ts, sfx_name in sfx_events:
        if sfx_name in sfx_loaded:
            sfx_audio_clips.append(sfx_loaded[sfx_name].with_start(ts))
            print(f"  SFX: '{sfx_name}' at t={ts:.1f}s", file=sys.stderr)

    # Optional background music
    audio_layers = [voiceover]
    bg_music_path = os.getenv("BACKGROUND_MUSIC_PATH", "").strip()
    if bg_music_path and os.path.exists(bg_music_path):
        from moviepy import concatenate_audioclips
        import moviepy.audio.fx as afx
        bg = AudioFileClip(bg_music_path).with_effects([afx.MultiplyVolume(0.10)])
        if bg.duration < final_video.duration:
            loops = math.ceil(final_video.duration / bg.duration)
            bg = concatenate_audioclips([bg] * loops)
        bg = bg.subclipped(0, final_video.duration)
        audio_layers.append(bg)

    audio_layers.extend(sfx_audio_clips)

    from moviepy import CompositeAudioClip
    audio = CompositeAudioClip(audio_layers) if len(audio_layers) > 1 else voiceover

    # Trim audio to video duration (or vice versa — use the shorter one)
    min_duration = min(final_video.duration, audio.duration)
    final_video = final_video.subclipped(0, min_duration)
    audio = audio.subclipped(0, min_duration)

    final_video = final_video.with_audio(audio)

    # Render
    print(f"Rendering to {args.output}...", file=sys.stderr)
    print("(This may take 8-15 minutes for a 10-min video)", file=sys.stderr)

    final_video.write_videofile(
        args.output,
        codec="libx264",
        audio_codec="aac",
        fps=FPS,
        preset="medium",
        threads=2,
        logger="bar",
        ffmpeg_params=["-bufsize", "4M", "-maxrate", "8M"],
    )

    # Cleanup moviepy resources
    if not use_batch_mode:
        for clip in segment_clips:
            for raw in getattr(clip, "_src_clips_to_close", []):
                try: raw.close()
                except Exception: pass
            try:
                clip.close()
            except Exception:
                pass
    for clip in overlay_clips:
        try:
            clip.close()
        except Exception:
            pass
    try:
        voiceover.close()
        final_video.close()
    except Exception:
        pass
    try:
        import gc
        gc.collect()
    except Exception:
        pass

    # Cleanup batch temp directory
    if batch_tmp_dir and os.path.isdir(batch_tmp_dir):
        shutil.rmtree(batch_tmp_dir, ignore_errors=True)

    print(f"\nVideo assembled → {args.output}", file=sys.stderr)
    print(args.output)


if __name__ == "__main__":
    main()
