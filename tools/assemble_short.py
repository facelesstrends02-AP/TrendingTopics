"""
assemble_short.py — Assemble a 9:16 portrait YouTube Short (1080×1920) using pure ffmpeg

Fetches portrait Pexels clips, converts landscape to pillarbox portrait, splits the
spoken script into sentences, assigns each sentence a proportional clip segment,
then burns per-sentence captions + hook/CTA overlays into a single ffmpeg pass.

Usage:
    python3 tools/assemble_short.py \
        --script-path .tmp/scripts/video_1_script.json \
        --audio-path .tmp/audio/video_1_short_0.mp3 \
        --output-path .tmp/shorts/video_1_short_0.mp4 \
        --hook-overlay "BREAKING: THIS CHANGES EVERYTHING" \
        --cta-overlay "FOLLOW FOR UPDATES" \
        --spoken-script "Breaking news: here is what happened..." \
        --pexels-queries '["breaking news protest", "government officials meeting", "world map globe"]'

Output (stdout): Path to final MP4
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

# ffmpeg-full has drawtext (libfreetype) support; regular ffmpeg bottle does not.
_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFMPEG_BIN = _FFMPEG_FULL if os.path.exists(_FFMPEG_FULL) else "ffmpeg"

TARGET_W = 1080
TARGET_H = 1920
MAX_DURATION = 60.0
HOOK_END = 3.5        # seconds hook overlay is shown
CTA_BEFORE_END = 8.0  # seconds before audio end to show CTA

CAPTION_COLORS = ["#FFD700", "#FF6B6B", "#00E5CC", "#7FFF00", "#FF8C42"]

FONT_URL = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Bold.ttf"
FONT_LOCAL = os.path.join(PROJECT_ROOT, ".tmp", "fonts", "Montserrat-Bold.ttf")
FONT_FALLBACKS = [
    "/System/Library/Fonts/Futura.ttc",
    "/Library/Fonts/Futura.ttc",
    "/System/Library/Fonts/AvenirNext.ttc",
    "/System/Library/Fonts/Avenir Next.ttc",
    "/System/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

PEXELS_VIDEOS_API = "https://api.pexels.com/videos/search"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def ensure_font() -> str:
    """Return path to a usable bold font. Downloads Montserrat-Bold if needed."""
    if os.path.exists(FONT_LOCAL) and os.path.getsize(FONT_LOCAL) > 1000:
        return FONT_LOCAL

    os.makedirs(os.path.dirname(FONT_LOCAL), exist_ok=True)
    try:
        print(f"Downloading Montserrat-Bold font...", file=sys.stderr)
        resp = requests.get(FONT_URL, timeout=30)
        resp.raise_for_status()
        with open(FONT_LOCAL, "wb") as f:
            f.write(resp.content)
        print(f"Font saved → {FONT_LOCAL}", file=sys.stderr)
        return FONT_LOCAL
    except Exception as e:
        print(f"WARNING: Could not download Montserrat: {e}", file=sys.stderr)

    for fp in FONT_FALLBACKS:
        if os.path.exists(fp):
            print(f"Using fallback font: {fp}", file=sys.stderr)
            return fp

    print("WARNING: No bold font found — ffmpeg will use its built-in default.", file=sys.stderr)
    return ""


def ffmpeg_escape(text: str) -> str:
    """Escape text for ffmpeg drawtext filter."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\u2019")   # replace straight apostrophe with curly to avoid quoting issues
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    return text


def run_ffmpeg(cmd: list, description: str = ""):
    """Run an ffmpeg command, logging it to stderr. Raises on non-zero exit."""
    print(f"  ffmpeg: {' '.join(cmd[:8])}{'...' if len(cmd) > 8 else ''}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: ffmpeg failed ({description}):", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed: {description}")


def get_duration(path: str) -> float:
    """Get media duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def get_video_dims(path: str) -> tuple:
    """Return (width, height) of a video file."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-select_streams", "v:0", path],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(result.stdout).get("streams", [{}])[0]
    return int(stream.get("width", 0)), int(stream.get("height", 0))


# ---------------------------------------------------------------------------
# Sentence splitting + duration allocation
# ---------------------------------------------------------------------------

def split_sentences(text: str) -> list:
    """Split spoken script into individual sentences."""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
    return sentences if sentences else [text.strip()]


def proportional_durations(sentences: list, total: float) -> list:
    """Allocate total duration to sentences proportionally by word count."""
    weights = [max(len(s.split()), 1) for s in sentences]
    total_w = sum(weights)
    return [(w / total_w) * total for w in weights]


# ---------------------------------------------------------------------------
# Pexels clip fetching
# ---------------------------------------------------------------------------

def _pick_hd_url(video: dict) -> str:
    """Pick the best (HD) download URL from a Pexels video object."""
    files = video.get("video_files", [])
    hd = [f for f in files if (f.get("width") or 0) >= 1280]
    target = sorted(hd or files, key=lambda f: f.get("width", 0) or 0, reverse=True)
    return target[0].get("link", "") if target else ""


def _download_clip(url: str, path: str) -> bool:
    """Download a video clip to path. Returns True on success."""
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"    Download failed: {e}", file=sys.stderr)
        return False


def fetch_portrait_clips(queries: list, pexels_key: str, tmp_dir: str) -> list:
    """
    Fetch and download Pexels video clips for the given queries.
    Prefers portrait orientation; falls back to landscape.
    Returns list of local paths to downloaded clips.
    """
    headers = {"Authorization": pexels_key}
    portrait_urls = []
    landscape_urls = []

    for query in queries:
        time.sleep(0.3)
        # Try portrait first
        try:
            resp = requests.get(
                PEXELS_VIDEOS_API,
                headers=headers,
                params={"query": query, "per_page": 5, "orientation": "portrait"},
                timeout=30,
            )
            if resp.ok:
                for v in resp.json().get("videos", [])[:2]:
                    url = _pick_hd_url(v)
                    if url and url not in portrait_urls:
                        portrait_urls.append(url)
        except Exception as e:
            print(f"    Pexels portrait search error for '{query}': {e}", file=sys.stderr)

        time.sleep(0.3)
        # Also gather landscape as fallback pool
        try:
            resp = requests.get(
                PEXELS_VIDEOS_API,
                headers=headers,
                params={"query": query, "per_page": 5, "orientation": "landscape",
                        "size": "large"},
                timeout=30,
            )
            if resp.ok:
                for v in resp.json().get("videos", [])[:2]:
                    url = _pick_hd_url(v)
                    if url and url not in portrait_urls and url not in landscape_urls:
                        landscape_urls.append(url)
        except Exception as e:
            print(f"    Pexels landscape search error for '{query}': {e}", file=sys.stderr)

    all_urls = portrait_urls + landscape_urls
    if not all_urls:
        print("  WARNING: No Pexels clips found for any query.", file=sys.stderr)
        return []

    paths = []
    for i, url in enumerate(all_urls[:6]):
        path = os.path.join(tmp_dir, f"raw_clip_{i:02d}.mp4")
        if os.path.exists(path) and os.path.getsize(path) > 10000:
            paths.append(path)
            continue
        print(f"    Downloading clip {i+1}/{min(len(all_urls), 6)}...", file=sys.stderr)
        if _download_clip(url, path):
            paths.append(path)

    return paths


# ---------------------------------------------------------------------------
# Portrait conversion
# ---------------------------------------------------------------------------

def to_portrait(input_path: str, output_path: str):
    """Convert a video clip to 1080×1920 portrait format."""
    w, h = get_video_dims(input_path)

    if w > h:
        # Landscape → blurred pillarbox
        vf = (
            f"split=2[bg][fg];"
            f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},boxblur=20:5[blurred];"
            f"[fg]scale=-2:{TARGET_H}[scaled];"
            f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        # Portrait or square → scale + crop
        vf = (
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H}"
        )

    run_ffmpeg([
        FFMPEG_BIN, "-y", "-i", input_path,
        "-vf", vf,
        "-r", "30",
        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        output_path,
    ], description=f"portrait conversion of {os.path.basename(input_path)}")


# ---------------------------------------------------------------------------
# Per-sentence clip segments
# ---------------------------------------------------------------------------

def build_sentence_segments(sentences: list, durations: list,
                             portrait_paths: list, tmp_dir: str) -> list:
    """
    For each sentence, select a clip from the pool (cycling), trim or loop
    to the sentence duration, and write it to a temp segment file.
    Returns list of segment file paths.
    """
    if not portrait_paths:
        return []

    clip_durations = {}
    for p in portrait_paths:
        try:
            clip_durations[p] = get_duration(p)
        except Exception:
            clip_durations[p] = 0.0

    # Track per-clip playhead to avoid replaying the same section
    clip_offsets = {p: 0.0 for p in portrait_paths}
    segment_files = []
    clip_idx = 0

    for i, (sentence, dur) in enumerate(zip(sentences, durations)):
        # Cycle clips, skip back to alternating to avoid consecutive repeats
        src = portrait_paths[clip_idx % len(portrait_paths)]
        next_clip_idx = (clip_idx + 1) % len(portrait_paths)
        if len(portrait_paths) > 1 and next_clip_idx == clip_idx % len(portrait_paths):
            next_clip_idx = (clip_idx + 2) % len(portrait_paths)
        clip_idx += 1

        out_seg = os.path.join(tmp_dir, f"seg_{i:03d}.mp4")
        src_dur = clip_durations.get(src, 0.0)

        if src_dur <= 0:
            # Fallback: black segment
            run_ffmpeg([
                FFMPEG_BIN, "-y",
                "-f", "lavfi", "-i", f"color=c=black:size={TARGET_W}x{TARGET_H}:rate=30:d={dur:.3f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23", out_seg,
            ], f"black segment {i}")
        else:
            offset = clip_offsets[src]
            if offset + dur > src_dur:
                offset = 0.0
                clip_offsets[src] = 0.0

            if src_dur >= offset + dur:
                # Simple trim
                run_ffmpeg([
                    FFMPEG_BIN, "-y", "-ss", f"{offset:.3f}", "-i", src,
                    "-t", f"{dur:.3f}",
                    "-r", "30",
                    "-c:v", "libx264", "-an", "-preset", "fast", "-crf", "23", out_seg,
                ], f"trim seg {i}")
                clip_offsets[src] = offset + dur
            else:
                # Need to loop
                loops = math.ceil(dur / src_dur) + 1
                loop_list = os.path.join(tmp_dir, f"loop_{i}.txt")
                with open(loop_list, "w") as lf:
                    for _ in range(loops):
                        lf.write(f"file '{src}'\n")
                looped = os.path.join(tmp_dir, f"looped_{i:03d}.mp4")
                run_ffmpeg([
                    FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", loop_list,
                    "-t", f"{dur:.3f}",
                    "-r", "30",
                    "-c:v", "libx264", "-an", "-preset", "fast", "-crf", "23", looped,
                ], f"loop seg {i}")
                os.rename(looped, out_seg)
                clip_offsets[src] = dur % src_dur if src_dur > 0 else 0.0

        segment_files.append(out_seg)

    return segment_files


# ---------------------------------------------------------------------------
# Filtergraph builder
# ---------------------------------------------------------------------------

def _wrap_text(text: str, max_chars: int) -> list:
    """Wrap text at max_chars and return a list of lines."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _make_text_file(text: str, max_chars: int, tmp_dir: str) -> str:
    """Wrap text at max_chars and write to a temp file (single line per file).
    Returns the file path."""
    lines = _wrap_text(text, max_chars)
    path = os.path.join(tmp_dir, f"txt_{uuid.uuid4().hex[:8]}.txt")
    # Write only the first line — multi-line textfile= renders \n as a visible glyph
    # on many font/ffmpeg combos. Per-line drawtext filters are used instead.
    with open(path, "w", encoding="utf-8") as f:
        f.write(lines[0] if lines else text)
    return path


def build_filtergraph(sentences: list, durations: list, font_path: str,
                      total_audio_dur: float,
                      hook_overlay: str, cta_overlay: str,
                      tmp_dir: str) -> str:
    """
    Build an ffmpeg -vf filtergraph string that:
    - Burns per-sentence captions (each in a rotating color)
    - Shows hook_overlay for the first HOOK_END seconds
    - Shows a dark box + CTA overlay for the last CTA_BEFORE_END seconds

    Uses textfile= for all text overlays so real newlines work correctly.
    """
    filters = []
    font_arg = f":fontfile='{font_path}'" if font_path else ""
    cta_start = max(total_audio_dur - CTA_BEFORE_END, total_audio_dur * 0.75)

    # ── Per-sentence captions ──────────────────────────────────────────────
    FONT_SIZE = 56
    LINE_SPACING = 8
    LINE_HEIGHT = FONT_SIZE + LINE_SPACING  # approximate px per line

    t = 0.0
    for i, (sentence, dur) in enumerate(zip(sentences, durations)):
        end_t = t + dur
        color = CAPTION_COLORS[i % len(CAPTION_COLORS)]
        lines = _wrap_text(sentence, max_chars=26)
        total_h = len(lines) * LINE_HEIGHT
        for j, line in enumerate(lines):
            txt_file = os.path.join(tmp_dir, f"txt_{uuid.uuid4().hex[:8]}.txt")
            with open(txt_file, "w", encoding="utf-8") as f:
                f.write(line)
            # Center the block vertically around h*0.77, one line per drawtext
            y_offset = int(j * LINE_HEIGHT - total_h / 2)
            y_expr = f"h*0.77+{y_offset}" if y_offset >= 0 else f"h*0.77{y_offset}"
            filters.append(
                f"drawtext=textfile='{txt_file}'"
                f":fontsize={FONT_SIZE}"
                f":fontcolor={color}"
                f":x=(w-text_w)/2"
                f":y={y_expr}"
                f":borderw=3"
                f":bordercolor=black"
                f"{font_arg}"
                f":enable='between(t,{t:.3f},{end_t:.3f})'"
            )
        t = end_t

    # ── Hook overlay (first HOOK_END seconds) ─────────────────────────────
    hook_lines = _wrap_text(hook_overlay, max_chars=22)
    hook_total_h = len(hook_lines) * (68 + LINE_SPACING)
    filters.append(
        f"drawbox=x=0:y=ih*0.10"
        f":w=iw:h=ih*0.13"
        f":color=black@0.55:t=fill"
        f":enable='between(t,0,{HOOK_END:.1f})'"
    )
    for j, line in enumerate(hook_lines):
        hook_file = os.path.join(tmp_dir, f"txt_{uuid.uuid4().hex[:8]}.txt")
        with open(hook_file, "w", encoding="utf-8") as f:
            f.write(line)
        y_offset = int(j * (68 + LINE_SPACING) - hook_total_h / 2)
        y_expr = f"h*0.165+{y_offset}" if y_offset >= 0 else f"h*0.165{y_offset}"
        filters.append(
            f"drawtext=textfile='{hook_file}'"
            f":fontsize=68"
            f":fontcolor=#FFD700"
            f":x=(w-text_w)/2"
            f":y={y_expr}"
            f":borderw=4"
            f":bordercolor=black"
            f"{font_arg}"
            f":enable='between(t,0,{HOOK_END:.1f})'"
        )

    # ── CTA overlay (last CTA_BEFORE_END seconds) ─────────────────────────
    cta_lines = _wrap_text(cta_overlay, max_chars=28)
    cta_total_h = len(cta_lines) * (64 + LINE_SPACING)
    end_t_str = f"{total_audio_dur:.3f}"
    filters.append(
        f"drawbox=x=0:y=ih*0.83"
        f":w=iw:h=ih*0.10"
        f":color=black@0.65:t=fill"
        f":enable='between(t,{cta_start:.3f},{end_t_str})'"
    )
    for j, line in enumerate(cta_lines):
        cta_file = os.path.join(tmp_dir, f"txt_{uuid.uuid4().hex[:8]}.txt")
        with open(cta_file, "w", encoding="utf-8") as f:
            f.write(line)
        y_offset = int(j * (64 + LINE_SPACING) - cta_total_h / 2)
        y_expr = f"h*0.86+{y_offset}" if y_offset >= 0 else f"h*0.86{y_offset}"
        filters.append(
            f"drawtext=textfile='{cta_file}'"
            f":fontsize=64"
            f":fontcolor=white"
            f":x=(w-text_w)/2"
            f":y={y_expr}"
            f":borderw=2"
            f":bordercolor=black"
            f"{font_arg}"
            f":enable='between(t,{cta_start:.3f},{end_t_str})'"
        )

    return ",".join(filters)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Assemble a YouTube Short (9:16 portrait) with ffmpeg")
    parser.add_argument("--script-path", required=True, help="Full video script JSON (for channel name)")
    parser.add_argument("--audio-path", required=True, help="Voiceover MP3")
    parser.add_argument("--output-path", required=True, help="Output MP4 path")
    parser.add_argument("--hook-overlay", required=True, help="ALL CAPS hook text (shown first 3.5s)")
    parser.add_argument("--cta-overlay", required=True, help="CTA text (shown last 8s)")
    parser.add_argument("--spoken-script", required=True, help="Full spoken narration text")
    parser.add_argument("--pexels-queries", required=True,
                        help="JSON array of 3 Pexels search queries")
    args = parser.parse_args()

    for path, name in [(args.audio_path, "Audio"), (args.script_path, "Script")]:
        if not os.path.exists(path):
            print(f"ERROR: {name} not found: {path}", file=sys.stderr)
            sys.exit(1)

    pexels_key = os.getenv("PEXELS_API_KEY")
    if not pexels_key:
        print("ERROR: PEXELS_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    try:
        pexels_queries = json.loads(args.pexels_queries)
        if not isinstance(pexels_queries, list):
            raise ValueError("pexels_queries must be a JSON array")
    except Exception as e:
        print(f"ERROR: Invalid --pexels-queries JSON: {e}", file=sys.stderr)
        sys.exit(1)

    output_dir = os.path.dirname(os.path.abspath(args.output_path))
    os.makedirs(output_dir, exist_ok=True)

    # Unique temp directory for this assembly run
    tmp_dir = os.path.join(output_dir, f"short_tmp_{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        _assemble(args, pexels_key, pexels_queries, tmp_dir, output_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(args.output_path)


def _assemble(args, pexels_key, pexels_queries, tmp_dir, output_dir):
    font_path = ensure_font()

    # Audio duration
    total_audio_dur = get_duration(args.audio_path)
    effective_dur = min(total_audio_dur, MAX_DURATION)
    print(f"Audio duration: {total_audio_dur:.1f}s (effective: {effective_dur:.1f}s)",
          file=sys.stderr)

    # Sentence split and proportional durations
    sentences = split_sentences(args.spoken_script)
    durations = proportional_durations(sentences, effective_dur)
    print(f"Split into {len(sentences)} sentences.", file=sys.stderr)

    # Fetch and convert Pexels clips to portrait
    print("Fetching Pexels portrait clips...", file=sys.stderr)
    raw_clips = fetch_portrait_clips(pexels_queries, pexels_key, tmp_dir)

    portrait_clips = []
    if raw_clips:
        portrait_dir = os.path.join(tmp_dir, "portrait")
        os.makedirs(portrait_dir, exist_ok=True)
        for i, src in enumerate(raw_clips):
            dst = os.path.join(portrait_dir, f"portrait_{i:02d}.mp4")
            try:
                to_portrait(src, dst)
                portrait_clips.append(dst)
            except Exception as e:
                print(f"  WARNING: Portrait conversion failed for clip {i}: {e}", file=sys.stderr)

    if not portrait_clips:
        print("WARNING: No portrait clips available — using black background.", file=sys.stderr)
        # Create a looping black background clip long enough for the full short
        black_clip = os.path.join(tmp_dir, "black_bg.mp4")
        run_ffmpeg([
            FFMPEG_BIN, "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:size={TARGET_W}x{TARGET_H}:rate=30:d={effective_dur:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", black_clip,
        ], "black background")
        portrait_clips = [black_clip]

    # Build per-sentence segments
    print("Building sentence-aligned clip segments...", file=sys.stderr)
    segment_files = build_sentence_segments(sentences, durations, portrait_clips, tmp_dir)

    if not segment_files:
        # Fallback: use first portrait clip directly
        print("WARNING: No sentence segments built — using first clip as-is.", file=sys.stderr)
        silent_video = portrait_clips[0]
    else:
        # Concatenate segments into silent video (stream copy — fast)
        concat_list = os.path.join(tmp_dir, "concat_list.txt")
        with open(concat_list, "w") as f:
            for seg in segment_files:
                f.write(f"file '{seg}'\n")
        silent_video = os.path.join(tmp_dir, "silent_video.mp4")
        run_ffmpeg([
            FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-c", "copy", silent_video,
        ], "concat segments")

    # Build filtergraph with captions + overlays
    print("Building filtergraph...", file=sys.stderr)
    filtergraph = build_filtergraph(
        sentences, durations, font_path,
        effective_dur, args.hook_overlay, args.cta_overlay,
        tmp_dir=tmp_dir,
    )

    # Final render: silent video + audio + filtergraph → output
    print(f"Rendering final Short → {args.output_path}...", file=sys.stderr)
    run_ffmpeg([
        FFMPEG_BIN, "-y",
        "-i", silent_video,
        "-i", args.audio_path,
        "-vf", filtergraph,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-t", f"{effective_dur:.3f}",
        "-shortest",
        args.output_path,
    ], "final render")

    print(f"Short assembled → {args.output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
