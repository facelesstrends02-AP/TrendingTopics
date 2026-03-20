"""
generate_channel_strategy.py — Generate a comprehensive channel strategy using Claude Sonnet

Uses claude-sonnet-4-6 with max_tokens=4000 for verbose strategic output.

Usage:
    python3 tools/generate_channel_strategy.py \
        --niche "Self Development" \
        --target-audience "25-35 year old professionals" \
        --competitors "Ali Abdaal,Thomas Frank" \
        --goals "1000 subscribers in 90 days" \
        --output .tmp/channel_strategy.json

Output (stdout): Path to output JSON file
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
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "channel_strategy.json")

SYSTEM_PROMPT = (
    "You are a senior YouTube growth strategist who has scaled multiple channels from 0 to 1M subscribers. "
    "You specialize in faceless, AI-powered YouTube channels covering trending news and current events across all topics — politics, technology, finance, science, sports, and global events. "
    "You give specific, actionable, data-driven advice — never vague platitudes. "
    "Your output is always structured JSON exactly matching the schema requested."
)


def build_strategy_prompt(niche, target_audience, competitors_list, goals):
    competitors_formatted = (
        ", ".join(competitors_list)
        if competitors_list
        else "none specified — position uniquely within the niche"
    )
    competitors_json_array = json.dumps(competitors_list)

    return f"""I am launching a faceless YouTube channel in the "{niche}" niche.

CHANNEL DETAILS:
- Niche: {niche}
- Target Audience: {target_audience}
- Main Competitors to Differentiate From: {competitors_formatted}
- Goals: {goals}
- Format: Faceless (stock footage + AI voiceover, no talking head)
- Upload cadence target: 3 videos per week

Generate a comprehensive channel growth strategy. I need ALL of the following:

1. CHANNEL POSITIONING
   - A unique angle that differentiates us from: {competitors_formatted}
   - Specific brand voice tone (e.g., "calm and authoritative like a wise mentor" — not just "professional")
   - The differentiation statement: what will make viewers choose us over competitors

2. CONTENT PILLARS
   - Exactly 4 core content pillars for the "{niche}" niche
   - Each pillar: name, 1-sentence description, 3 specific example video titles
   - Titles must be algorithm-optimized: curiosity gap, power words, specific numbers

3. UPLOAD SCHEDULE
   - Best 3 days of the week for the "{niche}" audience specifically (not generic advice)
   - Best upload times in EST for maximum first-24-hour traction
   - Rationale based on audience behavior in the "{niche}" niche

4. 30-VIDEO ROADMAP (first 90 days, weeks 1-10)
   - 10 weeks x 3 videos per week = 30 videos total
   - Each week: theme, 3 specific video titles, weekly goal
   - Weeks 1-2: foundation building (evergreen, SEO-heavy)
   - Weeks 3-5: audience building (shareable, emotional)
   - Weeks 6-8: momentum (capitalize on what worked, first viral attempts)
   - Weeks 9-10: optimization (refine based on data)

5. MILESTONE STRATEGY
   - Realistic timeline estimate: days to 1000 subscribers from launch
   - Realistic timeline estimate: days to 4000 watch hours from launch
   - Top 3 fastest actions to reach 1000 subscribers (ranked by impact, specific and actionable)
   - Top 3 fastest actions to reach 4000 watch hours (ranked by impact, specific and actionable)

6. CONTENT FORMATS
   - 4 content formats proven to perform in the "{niche}" niche
   - For each: format name, why it works for this specific niche, a concrete example title

7. SEO TACTICS
   - 6 specific SEO tactics for the "{niche}" niche (not generic YouTube SEO)

8. THUMBNAIL STRATEGY
   - 2-3 sentence description of the thumbnail style: colors, typography, face/no-face,
     emotional tone, background style — optimized for the "{niche}" niche

CRITICAL JSON RULES (your output must pass json.loads() without errors):
1. NEVER use double quotes (") inside any string value. Use single quotes or rephrase instead.
   BAD:  "title": "The "Burned Out" Professional's Guide"
   GOOD: "title": "The Burned-Out Professional's Guide"
2. All string values must be on a single line — no literal newline characters inside strings.
3. Use the em dash (—) or hyphen (-) instead of quotation marks for emphasis.

Return ONLY valid JSON matching EXACTLY this schema. No markdown, no explanation, no preamble:
{{
  "niche": "{niche}",
  "target_audience": "{target_audience}",
  "competitors": {competitors_json_array},
  "goals": "{goals}",
  "channel_positioning": {{
    "unique_angle": "...",
    "differentiation": "...",
    "brand_voice": "..."
  }},
  "content_pillars": [
    {{"name": "...", "description": "...", "example_titles": ["...", "...", "..."]}}
  ],
  "upload_schedule": {{
    "videos_per_week": 3,
    "best_days": ["Tuesday", "Thursday", "Saturday"],
    "best_times_est": ["2pm", "6pm"],
    "rationale": "..."
  }},
  "roadmap": [
    {{"week": 1, "theme": "...", "videos": ["title1", "title2", "title3"], "goal": "..."}}
  ],
  "milestone_strategy": {{
    "days_to_1000_subs": "...",
    "days_to_4000_watch_hours": "...",
    "fastest_path_1000_subs": ["step1", "step2", "step3"],
    "fastest_path_4000_hours": ["step1", "step2", "step3"]
  }},
  "content_formats": [
    {{"format": "...", "why_it_works": "...", "example": "..."}}
  ],
  "seo_tactics": ["...", "...", "...", "...", "...", "..."],
  "thumbnail_strategy": "..."
}}"""


def main():
    parser = argparse.ArgumentParser(description="Generate channel strategy with Claude Sonnet")
    parser.add_argument("--niche", required=True, help="Channel niche (e.g., 'Self Development')")
    parser.add_argument(
        "--target-audience",
        default="25-40 year old professionals seeking personal growth",
        help="Description of target audience",
    )
    parser.add_argument(
        "--competitors",
        default="",
        help="Comma-separated competitor channel names",
    )
    parser.add_argument(
        "--goals",
        default="reach 1000 subscribers and 4000 watch hours as fast as possible",
        help="Channel growth goals",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON file path")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    competitors_list = (
        [c.strip() for c in args.competitors.split(",") if c.strip()]
        if args.competitors
        else []
    )

    print(f"Generating strategy for '{args.niche}' with Claude Sonnet...", file=sys.stderr)
    print(f"  Target audience: {args.target_audience}", file=sys.stderr)
    print(f"  Competitors: {competitors_list or 'none specified'}", file=sys.stderr)
    print(f"  Goals: {args.goals}", file=sys.stderr)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_strategy_prompt(args.niche, args.target_audience, competitors_list, args.goals)

    strategy = None
    raw = ""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            if response.stop_reason == "max_tokens":
                print("WARNING: Response was cut off by max_tokens limit. Increase max_tokens if this fails.", file=sys.stderr)

            raw = response.content[0].text.strip()

            # Strip markdown code block if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            strategy = json.loads(raw)
            if not isinstance(strategy, dict):
                raise ValueError("Expected a JSON object")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse strategy from Claude response: {e}", file=sys.stderr)
                print(f"Raw response: {raw[:500]}", file=sys.stderr)
                sys.exit(1)

    # Inject metadata after parse — never rely on Claude for timestamps
    strategy["generated_at"] = datetime.now(timezone.utc).isoformat()
    strategy.setdefault("niche", args.niche)
    strategy.setdefault("target_audience", args.target_audience)
    strategy.setdefault("competitors", competitors_list)
    strategy.setdefault("goals", args.goals)

    # Warn if roadmap is not exactly 10 weeks (but don't fail)
    roadmap = strategy.get("roadmap", [])
    if len(roadmap) != 10:
        print(
            f"  WARNING: roadmap has {len(roadmap)} weeks (expected 10). Strategy is still usable.",
            file=sys.stderr,
        )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(strategy, f, indent=2)

    print(f"Strategy generated → {args.output}", file=sys.stderr)
    print(args.output)


if __name__ == "__main__":
    main()
