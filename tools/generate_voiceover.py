"""
generate_voiceover.py — Generate AI voiceover MP3 from a script using OpenAI TTS

Usage:
    python3 tools/generate_voiceover.py \
        --script-file .tmp/scripts/video_1_script.json \
        --output .tmp/audio/video_1_voiceover.mp3

    # Or pass spoken text directly (for Shorts):
    python3 tools/generate_voiceover.py \
        --text "Breaking news: this just happened..." \
        --output .tmp/audio/short_1.mp3

Output (stdout): Actual audio duration in seconds
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import re
import sys
import tempfile

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
MAX_TTS_CHARS = 4096


def split_text_into_chunks(text, max_chars=MAX_TTS_CHARS):
    """Split text into chunks at sentence boundaries, respecting max_chars limit."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            # If a single sentence is longer than limit, hard-split it
            if len(sentence) > max_chars:
                for i in range(0, len(sentence), max_chars):
                    chunks.append(sentence[i:i + max_chars])
            else:
                current = sentence
    if current:
        chunks.append(current)
    return chunks


def get_audio_duration(mp3_path):
    """Get audio duration in seconds using mutagen."""
    try:
        from mutagen.mp3 import MP3
        audio = MP3(mp3_path)
        return audio.info.length
    except Exception:
        # Fallback: rough estimate based on file size
        size_bytes = os.path.getsize(mp3_path)
        return size_bytes / 16000  # rough estimate for 128kbps MP3


def concatenate_mp3s(mp3_paths, output_path):
    """Concatenate multiple MP3 files into one using ffmpeg."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in mp3_paths:
            f.write(f"file '{path}'\n")
        list_file = f.name

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
             "-c", "copy", output_path],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(list_file)


def main():
    parser = argparse.ArgumentParser(description="Generate voiceover from script")
    parser.add_argument("--script-file", default=None, help="Script JSON file (mutually exclusive with --text)")
    parser.add_argument("--text", default=None, help="Spoken text string (alternative to --script-file)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--voice", default=None, help="TTS voice (overrides .env TTS_VOICE)")
    args = parser.parse_args()

    if args.text is None and args.script_file is None:
        print("ERROR: Must provide either --script-file or --text", file=sys.stderr)
        sys.exit(1)
    if args.text is not None and args.script_file is not None:
        print("ERROR: --script-file and --text are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    voice = args.voice or os.getenv("TTS_VOICE", "ash")

    if args.text is not None:
        full_text = args.text.strip()
        if not full_text:
            print("ERROR: --text is empty.", file=sys.stderr)
            sys.exit(1)
    else:
        if not os.path.exists(args.script_file):
            print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.script_file) as f:
            script = json.load(f)
        full_text = " ".join(
            seg.get("text", "") for seg in script.get("segments", []) if seg.get("text")
        ).strip()
        if not full_text:
            print("ERROR: Script has no text content.", file=sys.stderr)
            sys.exit(1)

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    chunks = split_text_into_chunks(full_text, MAX_TTS_CHARS)
    print(f"Generating voiceover: {len(chunks)} chunk(s), voice='{voice}'...", file=sys.stderr)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    if len(chunks) == 1:
        # Single request
        with client.audio.speech.with_streaming_response.create(
            model="tts-1-hd",
            voice=voice,
            input=chunks[0],
            response_format="mp3",
        ) as response:
            response.stream_to_file(args.output)
    else:
        # Multiple chunks — generate then concatenate
        tmp_files = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, chunk in enumerate(chunks):
                chunk_path = os.path.join(tmpdir, f"chunk_{i:03d}.mp3")
                print(f"  Chunk {i+1}/{len(chunks)} ({len(chunk)} chars)...", file=sys.stderr)
                with client.audio.speech.with_streaming_response.create(
                    model="tts-1-hd",
                    voice=voice,
                    input=chunk,
                    response_format="mp3",
                ) as response:
                    response.stream_to_file(chunk_path)
                tmp_files.append(chunk_path)

            print("Concatenating audio chunks...", file=sys.stderr)
            concatenate_mp3s(tmp_files, args.output)

    duration = get_audio_duration(args.output)
    print(f"Voiceover saved → {args.output} ({duration:.1f}s)", file=sys.stderr)
    print(f"{duration:.1f}")


if __name__ == "__main__":
    main()
