"""
generate_titles_thumbnails.py — Generate multiple title/thumbnail variants for a video topic

Acts as a YouTube CTR expert. For a given topic, produces 3-5 variants, each with:
  - Highly clickable title using psychological triggers (curiosity gap, specificity, tension, novelty)
  - Thumbnail concept: main visual, text overlay (max 4 words), emotion/expression, color strategy

Usage:
    python3 tools/generate_titles_thumbnails.py \
        --topic "how to wake up at 5am" \
        --niche "Self Development" \
        --count 5

    # From an existing idea:
    python3 tools/generate_titles_thumbnails.py \
        --idea-id 3 \
        --ideas-file .tmp/ideas.json \
        --niche "Self Development"

Output: .tmp/title_variants/variants.json  (or --output path)
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
OUTPUT_PATH = os.path.join(PROJECT_ROOT, ".tmp", "title_variants", "variants.json")


def build_prompt(topic, niche, count):
    return f"""Act as a YouTube CTR expert who optimizes videos for maximum click-through rate and watch time.

Niche: {niche}
Topic: {topic}

Create {count} DISTINCT title + thumbnail variant combinations. Each variant must make the viewer feel they CANNOT ignore the video.

For titles, use one of these psychological triggers per variant (use different ones across variants):
- curiosity_gap: withhold key information ("The Sleep Trick Doctors Won't Tell You")
- specificity: use exact numbers and details ("I Tried 7am Wake-Ups for 90 Days — Here's What Happened")
- tension: create cognitive dissonance or challenge a belief ("Stop Waking Up Early (Do This Instead)")
- novelty: position as new, rare, or surprising ("The Japanese Secret to Never Feeling Tired")
- fomo: fear of missing out on a result ("This Morning Habit Is Why You're Always Tired")

For thumbnail concepts, think in terms of:
- What single image instantly communicates the video's promise?
- What 3-4 word text overlay creates instant tension or curiosity?
- What emotion on the subject's face (or scene energy) matches the title's trigger?
- What color contrast makes this pop in a crowded feed?

Return ONLY a valid JSON object:
{{
  "topic": "{topic}",
  "niche": "{niche}",
  "generated_at": "placeholder",
  "variants": [
    {{
      "variant_id": 1,
      "title": "the clickable title here",
      "psychological_trigger": "curiosity_gap",
      "why_it_works": "one sentence on why this trigger is effective for this topic",
      "thumbnail": {{
        "main_visual": "description of the primary image/scene (e.g. 'person jolting awake in dark bedroom, alarm showing 5:00am')",
        "text_overlay": "MAX 4 WORDS ALL CAPS",
        "emotion": "the emotion or energy in the scene (e.g. 'shock', 'determination', 'calm confidence')",
        "color_strategy": "e.g. 'dark background with bright orange text, high contrast'",
        "pexels_search_query": "concrete noun phrase for Pexels photo search (e.g. 'person alarm clock dark bedroom morning')"
      }}
    }}
  ],
  "recommended_variant_id": 1
}}

Make each of the {count} variants genuinely different — different trigger, different angle, different visual approach.
The recommended_variant_id should be whichever has the highest estimated CTR for this specific topic.

Respond ONLY with the JSON object, no other text.
"""


def main():
    parser = argparse.ArgumentParser(description="Generate title/thumbnail variants")
    parser.add_argument("--topic", default="", help="Video topic (used when no ideas file)")
    parser.add_argument("--idea-id", type=int, default=None, help="Idea ID from ideas file")
    parser.add_argument("--ideas-file", default="", help="Path to ideas.json")
    parser.add_argument("--niche", default="", help="Channel niche (overrides NICHE in .env)")
    parser.add_argument("--count", type=int, default=5, help="Number of variants to generate")
    parser.add_argument("--output", default="", help="Output JSON path")
    args = parser.parse_args()

    niche = args.niche or os.getenv("NICHE", "Self Development")

    # Resolve topic
    topic = args.topic
    if not topic and args.idea_id is not None:
        if not args.ideas_file or not os.path.exists(args.ideas_file):
            print(f"ERROR: Ideas file not found: {args.ideas_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.ideas_file) as f:
            ideas = json.load(f)
        idea = next((i for i in ideas if i.get("id") == args.idea_id), None)
        if not idea:
            print(f"ERROR: Idea ID {args.idea_id} not found in {args.ideas_file}", file=sys.stderr)
            sys.exit(1)
        topic = idea.get("title", "")

    if not topic:
        print("ERROR: Provide either --topic or --idea-id + --ideas-file", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or OUTPUT_PATH

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(topic, niche, args.count)

    print(f"Generating {args.count} title/thumbnail variants for: '{topic}'...", file=sys.stderr)

    result = None
    raw = ""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            result = json.loads(raw)

            if "variants" not in result or not result["variants"]:
                raise ValueError("No variants in response")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse variants: {e}", file=sys.stderr)
                print(f"Raw: {raw[:500]}", file=sys.stderr)
                sys.exit(1)

    result["generated_at"] = datetime.now(timezone.utc).isoformat()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Generated {len(result['variants'])} variants → {output_path}", file=sys.stderr)
    print(output_path)


if __name__ == "__main__":
    main()
