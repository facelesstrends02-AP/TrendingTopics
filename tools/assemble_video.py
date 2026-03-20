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
CAPTION_FONTSIZE = 90
CAPTION_CHUNK_SIZE = 3
OVERLAY_DISPLAY_DURATION = 4.5   # max seconds to show overlay text
OVERLAY_REVEAL_DURATION  = 0.5   # seconds for left-to-right wipe-in animation
OVERLAY_MARGIN           = 60    # px from top-left edge

SFX_VOLUMES = {"riser": 0.40, "woosh": 0.50, "beep_0.5sec": 0.60, "beep_1sec": 0.60, "bell": 0.55}

WATERMARK_FONT_CHAIN = [
    "Arial-Bold", "ArialBold", "Futura-Bold", "FuturaBold",
    "AvenirNext-Bold", "AvenirNextCondensed-Bold",
    "DejaVuSans-Bold", "DejaVu Sans Bold",
]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


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


def calculate_segment_durations(segments, total_audio_duration):
    """
    Distribute total audio duration across segments proportionally
    based on each segment's duration_estimate weight.
    """
    total_estimate = sum(max(s.get("duration_estimate", 15), 1) for s in segments)
    durations = []
    for seg in segments:
        weight = max(seg.get("duration_estimate", 15), 1)
        duration = (weight / total_estimate) * total_audio_duration
        durations.append(max(duration, 2.0))  # minimum 2 seconds
    return durations


def make_text_clip(text, duration, position, fontsize, color="white", bg_opacity=0.6):
    """Create a text clip with semi-transparent background."""
    from moviepy import ImageClip, CompositeVideoClip
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    # Render text using PIL for better control
    font = None
    for font_path in [
        "/System/Library/Fonts/Futura.ttc",
        "/Library/Fonts/Futura.ttc",
        "/System/Library/Fonts/AvenirNext.ttc",
        "/System/Library/Fonts/Avenir Next.ttc",
        "/System/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        try:
            font = ImageFont.truetype(font_path, fontsize)
            break
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
    Prepends a bullet point to the text.
    """
    from moviepy import VideoClip
    from PIL import Image, ImageDraw, ImageFont

    display_dur = min(OVERLAY_DISPLAY_DURATION, seg_duration)
    bullet_text = f"\u2022 {text}"

    font = None
    for fp in [
        "/System/Library/Fonts/SFNSItalic.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf",
        "/System/Library/Fonts/Supplemental/Trebuchet MS Bold Italic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    ]:
        try:
            font = ImageFont.truetype(fp, fontsize)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    dummy = Image.new("RGBA", (1, 1))
    bbox = ImageDraw.Draw(dummy).textbbox((0, 0), bullet_text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 30, 15
    img_w, img_h = text_w + pad_x * 2, text_h + pad_y * 2

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=12,
                            fill=(0, 0, 0, int(bg_opacity * 255)))
    draw.text((pad_x, pad_y), bullet_text, font=font, fill=(255, 255, 255, 255))

    full_rgba  = np.array(img)                            # (H, W, 4)
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


def make_watermark(channel_name, total_duration):
    """Create channel watermark clip, trying multiple fonts in order."""
    from moviepy import TextClip
    import moviepy.video.fx as vfx

    def _apply_watermark_effects(wm):
        wm = wm.with_opacity(0.3)
        wm = wm.with_duration(total_duration)
        wm = wm.with_position(("right", "top"))
        wm = wm.with_effects([vfx.Margin(right=30, top=20, opacity=0)])
        return wm

    for font_name in WATERMARK_FONT_CHAIN:
        try:
            wm = TextClip(text=channel_name, font_size=WATERMARK_FONTSIZE,
                          color="white", font=font_name)
            return _apply_watermark_effects(wm)
        except Exception:
            continue

    # Final fallback: no font kwarg (moviepy default)
    try:
        wm = TextClip(text=channel_name, font_size=WATERMARK_FONTSIZE, color="white")
        return _apply_watermark_effects(wm)
    except Exception as e:
        print(f"  WARNING: Could not create watermark with any font: {e}", file=sys.stderr)
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
    for font_path in [
        "/System/Library/Fonts/Futura.ttc",
        "/Library/Fonts/Futura.ttc",
        "/System/Library/Fonts/AvenirNext.ttc",
        "/System/Library/Fonts/Avenir Next.ttc",
        "/System/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        try:
            font = ImageFont.truetype(font_path, fontsize)
            break
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

    # White text on top
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))

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

    clip = ImageClip(_np.array(img), is_mask=False).with_duration(needed_duration)
    clip = apply_color_grade(clip)
    clip = clip.with_effects([vfx.Resize(make_zoom_fn(needed_duration, zoom_in=True))])
    return clip


def process_segment_clip(clip_path, needed_duration):
    """Load, resize, and trim/loop a stock footage clip. Routes images to process_image_clip."""
    if is_image_file(clip_path):
        return process_image_clip(clip_path, needed_duration)

    from moviepy import VideoFileClip, concatenate_videoclips
    import moviepy.video.fx as vfx

    clip = VideoFileClip(clip_path, audio=False)
    clip = clip.with_effects([vfx.Resize((TARGET_WIDTH, TARGET_HEIGHT))])

    if clip.duration < needed_duration:
        # Loop the clip
        repeats = math.ceil(needed_duration / clip.duration)
        clip = concatenate_videoclips([clip] * repeats)

    clip = clip.subclipped(0, needed_duration)

    # Color grade
    clip = apply_color_grade(clip)

    # Ken Burns + zoom punch at start
    clip = clip.with_effects([vfx.Resize(make_zoom_fn(needed_duration, zoom_in=True))])

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

    # Single clip: delegate to original behavior
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

    for sub_idx, sub_duration in enumerate(sub_durations):
        clip_path = clip_paths[sub_idx % len(clip_paths)]

        try:
            # Route still images (news photos, Pexels photos) to image handler
            if is_image_file(clip_path):
                sub_clip = process_image_clip(clip_path, sub_duration)
                zoom_in = not zoom_in
                sub_clips.append(sub_clip)
                continue

            src = VideoFileClip(clip_path, audio=False)
            src = src.with_effects([vfx.Resize((TARGET_WIDTH, TARGET_HEIGHT))])

            # Determine start time for this sub-clip
            if clip_path not in clip_offsets:
                # First use: start in first 25% or first 5s, whichever is smaller
                max_initial = min(src.duration * 0.25, 5.0)
                clip_offsets[clip_path] = round(rng.uniform(0, max_initial), 2)

            start = clip_offsets[clip_path]

            # Clamp: if we'd run past the end, wrap to near the beginning
            if start + sub_duration > src.duration:
                start = round(rng.uniform(0, min(2.0, src.duration * 0.1)), 2)
                clip_offsets[clip_path] = start

            # Advance offset for next use of this clip
            clip_offsets[clip_path] = start + sub_duration + 2.0

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

    return segment_clip


def build_sfx_events(valid_segments, segment_durations):
    """Return list of (start_time_seconds, sfx_name) based on segment type."""
    events = []
    cumulative = 0.0
    for seg, dur in zip(valid_segments, segment_durations):
        seg_type = seg.get("type", "")
        if seg_type == "hook":
            events.append((cumulative, "riser"))
        elif seg_type == "point_1":
            events.append((cumulative, "woosh"))
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
    parser.add_argument("--batch-size", type=int, default=5,
                        help="Segments per batch when 15+ segments present (prevents OOM)")
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

    # Get total audio duration and distribute across segments
    total_audio = get_audio_duration(args.audio_file)
    print(f"Total audio duration: {total_audio:.1f}s", file=sys.stderr)

    segment_durations = calculate_segment_durations(valid_segments, total_audio)

    from moviepy import (
        AudioFileClip,
        CompositeVideoClip,
        concatenate_videoclips,
    )

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    text_clips_by_segment = []
    use_batch_mode = len(valid_segments) >= 15
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
                               fps=FPS, preset="medium", threads=4, logger=None)
            for c in batch_clips:
                try: c.close()
                except Exception: pass
            bv.close()
            partial_files.append(partial_path)

        if not partial_files:
            print("ERROR: No partial files rendered.", file=sys.stderr)
            sys.exit(1)

        # ffmpeg concat partials (stream copy — no re-encode)
        concat_list_path = os.path.join(batch_tmp_dir, "concat_list.txt")
        silent_combined = os.path.join(batch_tmp_dir, "silent_combined.mp4")
        with open(concat_list_path, "w") as cf:
            for pf in partial_files:
                cf.write(f"file '{pf}'\n")
        print("Concatenating partial files with ffmpeg...", file=sys.stderr)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", concat_list_path, "-c", "copy", silent_combined],
            check=True, capture_output=True,
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

    # Build text overlay clips — top-left, bullet point, left-to-right reveal
    # No conflict with bottom-center captions so no captions guard needed
    overlay_clips = []
    for i, (seg_idx, text, duration) in enumerate(text_clips_by_segment):
        start_time = sum(segment_durations[:seg_idx])
        try:
            txt_clip = make_reveal_text_clip(text, seg_duration=duration)
            txt_clip = txt_clip.with_start(start_time)
            overlay_clips.append(txt_clip)
        except Exception as e:
            print(f"  WARNING: Text overlay failed for segment {seg_idx}: {e}", file=sys.stderr)

    # Word-by-word captions
    if args.captions_file and os.path.exists(args.captions_file):
        print("Adding word-by-word captions...", file=sys.stderr)
        with open(args.captions_file) as f:
            words = json.load(f)
        chunks = group_words_into_chunks(words)

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
        final_video = CompositeVideoClip([final_video] + overlay_clips)

    # Add audio
    voiceover = AudioFileClip(args.audio_file)

    # SFX
    sfx_events = build_sfx_events(valid_segments, segment_durations)
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
        threads=4,
        logger="bar",
    )

    # Cleanup moviepy resources
    if not use_batch_mode:
        for clip in segment_clips:
            try:
                clip.close()
            except Exception:
                pass
    try:
        voiceover.close()
        final_video.close()
    except Exception:
        pass

    # Cleanup batch temp directory
    if batch_tmp_dir and os.path.isdir(batch_tmp_dir):
        shutil.rmtree(batch_tmp_dir, ignore_errors=True)

    print(f"\nVideo assembled → {args.output}", file=sys.stderr)
    print(args.output)


if __name__ == "__main__":
    main()
