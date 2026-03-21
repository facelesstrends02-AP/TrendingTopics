"""
generate_short_scripts.py — Generate 2 YouTube Shorts scripts

Two input modes:
  1. --script-path  (original): derives shorts from point_N segments of a full video script
  2. --ideas-file   (new):      derives shorts from 2 independent Short ideas
                                (output of generate_short_ideas.py)

Usage:
    # From full video script (original):
    python3 tools/generate_short_scripts.py \
        --script-path .tmp/scripts/video_1_script.json \
        --output .tmp/shorts/video_1_shorts_plan.json

    # From independent Short ideas (new):
    python3 tools/generate_short_scripts.py \
        --ideas-file .tmp/shorts/video_1_short_ideas.json \
        --output .tmp/shorts/video_1_shorts_plan.json

Output (stdout): Path to the written shorts plan JSON
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

REQUIRED_KEYS = {"spoken_script", "hook_overlay", "cta_overlay",
                 "pexels_queries", "short_title", "short_description"}


def strip_markdown_fences(text: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` wrappers if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    return text


def build_prompt_from_ideas(idea_1: dict, idea_2: dict, channel_name: str) -> str:
    """Build the Claude prompt using 2 independent Short ideas instead of video segments."""
    return f"""You are a YouTube Shorts script writer for "{channel_name}", the Implications Channel — every Short answers "something just happened, here's what it means for YOU personally." The viewer's wallet, job, health, or rights must be the focus, not just the event itself.

I will give you 2 trending news ideas. For EACH idea, write a standalone 25-32 second YouTube Short script designed for maximum completion rate.

Return ONLY a valid JSON array of exactly 2 objects. No markdown, no explanation, just the JSON array.

Each object must have EXACTLY these keys:
- "spoken_script": string, 55-70 words MAXIMUM, urgent breaking-news tone, second-person ("you"), no stage directions. Structure: 1-2 sentences of pure fact/tension (the hook) → 2-3 sentences of the key implication → 1 sentence CTA. NEVER start with "In this video", "Today we're covering", or any warm-up. Start mid-drama with the most shocking or consequential sentence.
- "hook_overlay": string, 4-8 ALL CAPS words for the opening graphic (first 3.5s). Must start with "BREAKING:", "THIS JUST HAPPENED:", or "YOU NEED TO KNOW:"
- "cta_overlay": string, 3-6 words shown at the end. Use "FOLLOW FOR MORE", "STAY INFORMED", or "MORE ON THIS"
- "pexels_queries": array of exactly 3 strings — specific B-roll search terms (specific → complementary → broad fallback). Use concrete nouns. Include news-relevant terms like newsroom, journalist, world map, newspaper when relevant.
- "short_title": string, max 60 chars, urgent news headline style, NO question marks, ends with " #Shorts"
- "short_description": string, 2-3 sentences, 3-5 relevant hashtags at the end

Idea 1 — {idea_1.get("category", "world")}:
Title: {idea_1.get("title", "")}
Hook: {idea_1.get("hook", "")}
Angle: {idea_1.get("angle", "")}

Idea 2 — {idea_2.get("category", "world")}:
Title: {idea_2.get("title", "")}
Hook: {idea_2.get("hook", "")}
Angle: {idea_2.get("angle", "")}

Requirements for each Short:
- Cold open: the very first word must be the drama — no warm-up, no intro, no "hey guys"
- Resolution by 25 seconds: viewer must get a complete, satisfying take within 25s
- Language: urgent, factual, zero filler. Every word earns its place.
- Target: 90%+ completion rate, which requires resolution before 30 seconds

Return ONLY the JSON array, starting with [ and ending with ]."""


def build_prompt(seg_1: dict, seg_2: dict, channel_name: str) -> str:
    return f"""You are a YouTube Shorts script writer for "{channel_name}", the Implications Channel — every Short answers "something just happened, here's what it means for YOU personally." The viewer's wallet, job, health, or rights must be the focus, not just the event itself.

I will give you 2 video segments from a long-form news video. For EACH segment, write a standalone 25-32 second YouTube Short script designed for maximum completion rate.

Return ONLY a valid JSON array of exactly 2 objects. No markdown, no explanation, just the JSON array.

Each object must have EXACTLY these keys:
- "spoken_script": string, 55-70 words MAXIMUM, urgent breaking-news tone, second-person ("you"), no stage directions. Structure: 1-2 sentences of pure fact/tension (the hook) → 2-3 sentences of the key implication → 1 sentence CTA. NEVER start with "In this video", "Today we're covering", or any warm-up. Start mid-drama with the most shocking or consequential sentence.
- "hook_overlay": string, 4-8 ALL CAPS words for the opening graphic (first 3.5s). Must start with "BREAKING:", "THIS JUST HAPPENED:", or "YOU NEED TO KNOW:"
- "cta_overlay": string, 3-6 words shown at the end. Use "FOLLOW FOR MORE", "STAY INFORMED", or "MORE ON THIS"
- "pexels_queries": array of exactly 3 strings — specific B-roll search terms (specific → complementary → broad fallback). Use concrete nouns. Include news-relevant terms like newsroom, journalist, world map, newspaper when relevant.
- "short_title": string, max 60 chars, urgent news headline style, NO question marks, ends with " #Shorts"
- "short_description": string, 2-3 sentences, 3-5 relevant hashtags at the end

Segment 1 (type: {seg_1.get("type", "point")}, segment_id: {seg_1.get("segment_id", 1)}):
{seg_1.get("text", "")}

Segment 2 (type: {seg_2.get("type", "point")}, segment_id: {seg_2.get("segment_id", 2)}):
{seg_2.get("text", "")}

Requirements for each Short:
- Cold open: the very first word must be the drama — no warm-up, no intro, no "hey guys"
- Resolution by 25 seconds: viewer must get a complete, satisfying take within 25s
- Language: urgent, factual, zero filler. Every word earns its place.
- Target: 90%+ completion rate, which requires resolution before 30 seconds

Return ONLY the JSON array, starting with [ and ending with ]."""


def main():
    parser = argparse.ArgumentParser(description="Generate YouTube Shorts scripts")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--script-path", help="Path to full video script JSON (original mode)")
    group.add_argument("--ideas-file", help="Path to short_ideas.json from generate_short_ideas.py (independent mode)")
    parser.add_argument("--output", default=None,
                        help="Output path (default: .tmp/shorts/{video_key}_shorts_plan.json)")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    channel_name = os.getenv("CHANNEL_NAME", "TrendingTopics")
    client = anthropic.Anthropic(api_key=api_key)

    if args.ideas_file:
        # Independent mode: generate scripts from 2 fresh Short ideas
        if not os.path.exists(args.ideas_file):
            print(f"ERROR: Ideas file not found: {args.ideas_file}", file=sys.stderr)
            sys.exit(1)

        with open(args.ideas_file) as f:
            ideas = json.load(f)

        if not isinstance(ideas, list) or len(ideas) < 2:
            print(f"ERROR: Ideas file must contain at least 2 ideas, found "
                  f"{len(ideas) if isinstance(ideas, list) else 0}.", file=sys.stderr)
            sys.exit(1)

        if args.output:
            output_path = args.output
        else:
            stem = os.path.splitext(os.path.basename(args.ideas_file))[0]
            video_key = stem.replace("_short_ideas", "")
            output_path = os.path.join(PROJECT_ROOT, ".tmp", "shorts",
                                       f"{video_key}_shorts_plan.json")

        print(f"Generating shorts from 2 independent ideas: "
              f"\"{ideas[0].get('title', '')}\" / \"{ideas[1].get('title', '')}\"...",
              file=sys.stderr)
        prompt = build_prompt_from_ideas(ideas[0], ideas[1], channel_name)

    else:
        # Original mode: derive scripts from point_N segments of a full video script
        if not os.path.exists(args.script_path):
            print(f"ERROR: Script not found: {args.script_path}", file=sys.stderr)
            sys.exit(1)

        with open(args.script_path) as f:
            script = json.load(f)

        if args.output:
            output_path = args.output
        else:
            stem = os.path.splitext(os.path.basename(args.script_path))[0]
            video_key = stem.replace("_script", "")
            output_path = os.path.join(PROJECT_ROOT, ".tmp", "shorts",
                                       f"{video_key}_shorts_plan.json")

        segments = script.get("segments", [])
        point_segs = [s for s in segments if s.get("type", "").startswith("point_")]

        if len(point_segs) < 2:
            print(f"ERROR: Need at least 2 point segments, found {len(point_segs)}.", file=sys.stderr)
            sys.exit(1)

        seg_1 = point_segs[0]
        seg_2 = point_segs[len(point_segs) // 2]
        if seg_1.get("segment_id") == seg_2.get("segment_id") and len(point_segs) > 1:
            seg_2 = point_segs[-1]

        print(f"Generating shorts from segments {seg_1.get('segment_id')} and "
              f"{seg_2.get('segment_id')}...", file=sys.stderr)
        prompt = build_prompt(seg_1, seg_2, channel_name)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Single Claude call with retry on JSON parse failure
    raw_text = ""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text
            cleaned = strip_markdown_fences(raw_text)
            shorts_plan = json.loads(cleaned)
            break
        except json.JSONDecodeError as e:
            if attempt == 0:
                print(f"  JSON parse failed (attempt {attempt+1}): {e}. Retrying...",
                      file=sys.stderr)
                continue
            print(f"ERROR: Failed to parse Claude response as JSON after 2 attempts.",
                  file=sys.stderr)
            print(f"Raw response:\n{raw_text}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"ERROR: Claude API call failed: {e}", file=sys.stderr)
            sys.exit(1)

    # Validate
    if not isinstance(shorts_plan, list) or len(shorts_plan) != 2:
        print(f"ERROR: Expected a list of 2 shorts, got: {type(shorts_plan).__name__} "
              f"with {len(shorts_plan) if isinstance(shorts_plan, list) else '?'} items.",
              file=sys.stderr)
        sys.exit(1)

    for i, short in enumerate(shorts_plan):
        missing = REQUIRED_KEYS - set(short.keys())
        if missing:
            print(f"ERROR: Short {i} missing required keys: {missing}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(short.get("pexels_queries"), list) or len(short["pexels_queries"]) != 3:
            print(f"WARNING: Short {i} pexels_queries should be a list of 3. Got: "
                  f"{short.get('pexels_queries')}", file=sys.stderr)
            # Pad or truncate to 3
            queries = list(short.get("pexels_queries") or [])
            while len(queries) < 3:
                queries.append("breaking news world event")
            short["pexels_queries"] = queries[:3]

    with open(output_path, "w") as f:
        json.dump(shorts_plan, f, indent=2)

    print(f"Shorts plan saved → {output_path}", file=sys.stderr)
    print(f"  Short 0: {shorts_plan[0]['short_title']}", file=sys.stderr)
    print(f"  Short 1: {shorts_plan[1]['short_title']}", file=sys.stderr)
    print(output_path)


if __name__ == "__main__":
    main()
