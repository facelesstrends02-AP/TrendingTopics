"""
generate_seo_metadata.py — Generate SEO-optimized metadata for a YouTube video

Acts as a YouTube SEO specialist who understands both YouTube and Google search algorithms.
Produces:
  - SEO-optimized title (max 100 chars)
  - High-ranking description (150-250 words with timestamps, keywords, CTA)
  - Semantic keywords and search phrases
  - Chapter markers with SEO titles
  - Related video topics for internal linking
  - Optionally updates the live YouTube video via API

Usage:
    python3 tools/generate_seo_metadata.py \
        --script-file .tmp/scripts/video_1_script.json \
        --niche "Self Development" \
        --output .tmp/seo/video_1_seo.json

    # With YouTube update:
    python3 tools/generate_seo_metadata.py \
        --script-file .tmp/scripts/video_1_script.json \
        --video-id abc123xyz \
        --update-youtube

Output: .tmp/seo/video_N_seo.json
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "token.json")
DEFAULT_STRATEGY_PATH = os.path.join(PROJECT_ROOT, "channel_strategy.json")


def load_strategy(path):
    """Load channel strategy JSON if it exists, return {} otherwise."""
    try:
        if path and os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/spreadsheets",
]


def build_seo_tactics_section(strategy):
    """Build channel-specific SEO tactics block from strategy."""
    if not strategy:
        return ""
    tactics = strategy.get("seo_tactics", [])
    positioning = strategy.get("channel_positioning", {})
    unique_angle = positioning.get("unique_angle", "")

    if not tactics and not unique_angle:
        return ""

    parts = []
    if tactics:
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(tactics))
        parts.append(f"CHANNEL-SPECIFIC SEO TACTICS — apply all of these:\n{numbered}")
    if unique_angle:
        parts.append(f"Channel Positioning for Keyword Strategy: {unique_angle}")

    return "\n\n".join(parts) + "\n\n"


def compute_segment_timestamps(segments):
    """Return list of 'M:SS' timestamp strings, one per segment."""
    timestamps = []
    cumulative_seconds = 0
    for seg in segments:
        mins = cumulative_seconds // 60
        secs = cumulative_seconds % 60
        timestamps.append(f"{mins}:{secs:02d}")
        cumulative_seconds += seg.get("duration_estimate", 20)
    return timestamps


def build_prompt(script, channel_name, strategy=None):
    title = script.get("title", "")
    description = script.get("description", "")
    tags = script.get("tags", [])
    segments = script.get("segments", [])
    # Derive category from script idea metadata if present
    category = script.get("category", "world")

    # Build segment summary — timestamps are deterministic, shown for context only
    segment_text = ""
    cumulative_seconds = 0
    for seg in segments:
        seg_type = seg.get("type", "")
        dur = seg.get("duration_estimate", 20)
        mins = cumulative_seconds // 60
        secs = cumulative_seconds % 60
        segment_text += f"  {mins}:{secs:02d} — {seg_type}: {seg.get('text', '')[:80]}\n"
        cumulative_seconds += dur

    existing_tags_str = ", ".join(tags[:10]) if tags else "none"
    seo_tactics_section = build_seo_tactics_section(strategy)

    return f"""Act as a YouTube SEO specialist who understands both YouTube and Google search algorithms.

Optimize this video for maximum discoverability and ranking potential.

CURRENT VIDEO DATA:
Title: {title}
Channel: {channel_name}
Category: {category}
Existing description: {description[:300] if description else 'none'}
Existing tags: {existing_tags_str}
Segments (for chapter markers):
{segment_text}
{seo_tactics_section}YOUR TASK — produce all of the following:

1. SEO TITLE: Improved version using the most searchable keywords while keeping the emotional hook. Max 100 chars. Place primary keyword near the start if it doesn't hurt readability.

2. DESCRIPTION (150-250 words):
   - Open with the primary keyword naturally in the first sentence
   - 2-3 short paragraphs covering the video content
   - Do NOT include timestamps — they will be added automatically
   - Add 3-5 semantic keyword phrases woven in naturally
   - End with a CTA to subscribe and a note about the channel covering trending world topics
   - Include relevant hashtags at the end (3-5)

3. SEMANTIC KEYWORDS: 8-12 specific search phrases people actually type into YouTube/Google when looking for this content. Mix of short-tail (2-3 words) and long-tail (4-6 words).

4. TAGS: 12-15 optimized tags. Mix of: exact match keyword, broad match, related topics, channel niche terms.

5. CHAPTER TITLES: One SEO-optimized title per segment (in order). 4-6 words each, highly descriptive, keyword-rich. Label them as the IMPLICATION or KEY FACT, not the segment type. BAD: "Context", "Point 2", "Background". GOOD: "How Iran Sanctions Affect Gas Prices", "What Tesla Recall Means for Owners", "Why Dollar Is Losing Reserve Status". Each title should be a micro-keyword someone might actually search.

6. RELATED VIDEO TOPICS: 5 specific video topic ideas that would appear in YouTube's "suggested videos" sidebar for this content — topics this channel could create to build internal linking momentum.

7. SEARCH PHRASES: 5 exact phrases (how people search) that this video should rank for. Target LONG-TAIL IMPLICATION phrases, not broad news keywords CNN already owns. BAD: "Iran nuclear deal 2026". GOOD: "how Iran sanctions affect my gas prices 2026", "what happens to oil prices if Iran deal fails". These should be 4-7 word phrases with personal consequence framing that match the "implications for you" channel positioning.

Return ONLY a valid JSON object:
{{
  "original_title": "{title}",
  "seo_title": "optimized title here",
  "description": "full optimized description here",
  "semantic_keywords": ["keyword phrase 1", "keyword phrase 2"],
  "tags": ["tag1", "tag2"],
  "chapter_titles": ["SEO title for segment 1", "SEO title for segment 2"],
  "related_video_topics": ["topic 1", "topic 2", "topic 3", "topic 4", "topic 5"],
  "search_phrases": ["exact search phrase 1", "exact search phrase 2"],
  "updated_youtube": false
}}

Respond ONLY with the JSON object, no other text.
"""


def get_youtube_service():
    """Get authenticated YouTube service using existing token.json."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: google-api-python-client not installed. Run: pip install google-api-python-client google-auth-oauthlib", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(TOKEN_PATH):
        print("ERROR: token.json not found. Run setup.sh first.", file=sys.stderr)
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def inject_chapters(description, chapter_markers):
    """Inject a deterministic chapter block into the description.

    Inserts before any trailing hashtags (lines starting with #), or appends
    at the end. Strips any existing timestamp lines the LLM may have written
    so we never get duplicates.
    YouTube requires: first chapter at 0:00, at least 3 chapters.
    """
    if not chapter_markers:
        return description

    # Build the chapter block
    lines = []
    for ch in chapter_markers:
        ts = ch.get("timestamp", "").strip()
        title = ch.get("title", "").strip()
        if ts and title:
            lines.append(f"{ts} {title}")

    if len(lines) < 3:
        return description  # YouTube ignores chapters if fewer than 3

    chapter_block = "\n".join(lines)

    # Remove any existing timestamp lines from the LLM description
    import re
    cleaned = re.sub(r"^\d+:\d{2}[^\n]*\n?", "", description, flags=re.MULTILINE).strip()

    # Find hashtag section at the end
    hashtag_match = re.search(r"\n(#\S)", cleaned)
    if hashtag_match:
        split = hashtag_match.start()
        return f"{cleaned[:split]}\n\n{chapter_block}\n\n{cleaned[split:].lstrip()}"
    else:
        return f"{cleaned}\n\n{chapter_block}"


def update_youtube_video(video_id, seo_data, script):
    """Update the YouTube video's title, description, and tags via API."""
    service = get_youtube_service()

    # Get current video snippet to preserve categoryId and other fields
    try:
        response = service.videos().list(part="snippet", id=video_id).execute()
        items = response.get("items", [])
        if not items:
            print(f"ERROR: Video ID {video_id} not found on YouTube.", file=sys.stderr)
            return False
        current_snippet = items[0]["snippet"]
    except Exception as e:
        print(f"ERROR: Could not fetch video snippet: {e}", file=sys.stderr)
        return False

    # Update snippet fields
    current_snippet["title"] = seo_data["seo_title"][:100]
    current_snippet["description"] = seo_data["description"][:5000]
    current_snippet["tags"] = seo_data["tags"][:500]
    # Preserve categoryId from script or existing
    current_snippet["categoryId"] = script.get("category_id", current_snippet.get("categoryId", "26"))

    try:
        service.videos().update(
            part="snippet",
            body={"id": video_id, "snippet": current_snippet},
        ).execute()
        print(f"  YouTube video {video_id} metadata updated.", file=sys.stderr)
        return True
    except Exception as e:
        print(f"ERROR: Could not update YouTube video: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Generate SEO metadata for a YouTube video")
    parser.add_argument("--script-file", required=True, help="Path to the video script JSON")
    parser.add_argument("--channel-name", default="", help="Channel name (overrides CHANNEL_NAME in .env)")
    parser.add_argument("--output", default="", help="Output JSON path")
    parser.add_argument("--video-id", default="", help="YouTube video ID (required for --update-youtube)")
    parser.add_argument("--update-youtube", action="store_true", help="Update the live YouTube video metadata")
    parser.add_argument("--strategy-file", default="", help="Path to channel_strategy.json (auto-detected if not set)")
    args = parser.parse_args()

    if not os.path.exists(args.script_file):
        print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.script_file) as f:
        script = json.load(f)

    channel_name = args.channel_name or os.getenv("CHANNEL_NAME", "TrendingTopics")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    strategy_path = args.strategy_file or DEFAULT_STRATEGY_PATH
    strategy = load_strategy(strategy_path)
    if strategy:
        print(f"  Strategy loaded: {strategy_path}", file=sys.stderr)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        video_key = os.path.basename(args.script_file).replace("_script.json", "")
        output_path = os.path.join(PROJECT_ROOT, ".tmp", "seo", f"{video_key}_seo.json")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(script, channel_name, strategy)

    title = script.get("title", "video")
    print(f"Generating SEO metadata for: '{title}'...", file=sys.stderr)

    seo_data = None
    raw = ""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            seo_data = json.loads(raw)

            if "seo_title" not in seo_data or "description" not in seo_data:
                raise ValueError("Missing required SEO fields")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse SEO data: {e}", file=sys.stderr)
                print(f"Raw: {raw[:500]}", file=sys.stderr)
                sys.exit(1)

    # Merge deterministic timestamps with LLM-generated titles
    computed_timestamps = compute_segment_timestamps(script.get("segments", []))
    chapter_titles = seo_data.pop("chapter_titles", [])
    chapter_markers = [
        {"timestamp": ts, "title": title}
        for ts, title in zip(computed_timestamps, chapter_titles)
    ]
    seo_data["chapter_markers"] = chapter_markers

    # Inject chapter block into description
    seo_data["description"] = inject_chapters(seo_data["description"], chapter_markers)

    seo_data["generated_at"] = datetime.now(timezone.utc).isoformat()
    seo_data["updated_youtube"] = False

    # Update YouTube if requested
    if args.update_youtube and args.video_id:
        print(f"  Updating YouTube video {args.video_id}...", file=sys.stderr)
        updated = update_youtube_video(args.video_id, seo_data, script)
        seo_data["updated_youtube"] = updated
    elif args.update_youtube and not args.video_id:
        print("WARNING: --update-youtube requires --video-id. Skipping YouTube update.", file=sys.stderr)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(seo_data, f, indent=2)

    print(f"SEO metadata saved → {output_path}", file=sys.stderr)
    print(output_path)


if __name__ == "__main__":
    main()
