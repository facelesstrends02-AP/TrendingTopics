"""
generate_captions.py — Generate word-level captions from voiceover using OpenAI Whisper

Usage:
    python3 tools/generate_captions.py \
        --audio-file .tmp/audio/video_1_voiceover.mp3 \
        --output .tmp/captions/video_1_captions.json

Output (stdout): JSON with status and word_count
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Generate word-level captions via Whisper")
    parser.add_argument("--audio-file", required=True, help="Path to voiceover MP3")
    parser.add_argument("--output", required=True, help="Output path for captions JSON")
    args = parser.parse_args()

    if not os.path.exists(args.audio_file):
        print(f"ERROR: Audio file not found: {args.audio_file}", file=sys.stderr)
        sys.exit(1)

    import openai
    client = openai.OpenAI()

    print(f"Transcribing audio with Whisper: {args.audio_file}", file=sys.stderr)

    with open(args.audio_file, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = [{"word": w.word, "start": w.start, "end": w.end} for w in result.words]

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    with open(args.output, "w") as f:
        json.dump(words, f, indent=2)

    print(f"Captions saved: {len(words)} words → {args.output}", file=sys.stderr)
    print(json.dumps({"status": "ok", "word_count": len(words)}))


if __name__ == "__main__":
    main()
