"""
analyze_performance.py — Analyze video performance data with Claude Haiku

Reads analytics JSON, sends to Claude Haiku, and generates structured insights:
  - Top performers and why they worked
  - Underperformers and why they flopped
  - Patterns to double down on
  - Topics/styles to avoid

Usage:
    python3 tools/analyze_performance.py \
        --analytics-file .tmp/analytics_2026-03-09.json \
        --niche "Self Development" \
        --output-file .tmp/analytics_insights.json

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


def build_analysis_prompt(analytics, niche):
    # Format video data for the prompt
    video_lines = []
    for v in analytics:
        video_lines.append(
            f"- \"{v['title']}\"\n"
            f"  Views: {v['view_count']:,} | Likes: {v['like_count']:,} | "
            f"Comments: {v['comment_count']:,} | Engagement: {v['engagement_rate']:.2%}\n"
            f"  Published: {v.get('published_week', 'unknown')}"
        )

    videos_text = "\n".join(video_lines)
    count = len(analytics)

    return f"""You are a YouTube channel analytics expert specializing in the "{niche}" niche.

Below is performance data for {count} published video(s) on this channel:

{videos_text}

Analyze this data and provide actionable insights for future content strategy.

Return ONLY a valid JSON object with exactly these fields:
{{
  "top_performers": [
    {{
      "title": "video title",
      "views": 45000,
      "engagement_rate": 0.042,
      "why_it_worked": "concise explanation of success factors (max 150 chars)"
    }}
  ],
  "underperformers": [
    {{
      "title": "video title",
      "views": 8000,
      "engagement_rate": 0.009,
      "why_it_flopped": "concise explanation of failure factors (max 150 chars)"
    }}
  ],
  "patterns": [
    "Pattern observation 1 (e.g., 'Numbered list titles average 2.3x more views')",
    "Pattern observation 2",
    "Pattern observation 3"
  ],
  "double_down_topics": [
    "Topic or angle that performed well and should be repeated"
  ],
  "avoid_topics": [
    "Topic or angle that underperformed and should be avoided or reframed"
  ],
  "content_recommendations": [
    "Specific actionable recommendation for future videos (max 200 chars)"
  ],
  "insights_summary": "2-3 sentence summary of the key takeaways and strategic direction (max 400 chars)"
}}

Rules:
- Include top 2-3 performers in top_performers (or all if fewer than 3)
- Include bottom 1-2 in underperformers (skip if all videos performed well)
- Patterns should be data-driven observations, not generic advice
- Be specific and concrete, not vague
- If there are fewer than 3 videos, still provide useful insights based on available data
- Respond ONLY with valid JSON, no other text
"""


def main():
    parser = argparse.ArgumentParser(description="Analyze video performance with Claude Haiku")
    parser.add_argument("--analytics-file", required=True, help="Path to analytics JSON")
    parser.add_argument("--niche", required=True, help="Channel niche for context")
    parser.add_argument("--output-file", required=True, help="Output JSON file for insights")
    args = parser.parse_args()

    if not os.path.exists(args.analytics_file):
        print(f"ERROR: Analytics file not found: {args.analytics_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.analytics_file) as f:
        analytics = json.load(f)

    if not analytics:
        print("WARNING: No analytics data to analyze. Writing empty insights.", file=sys.stderr)
        empty_insights = {
            "top_performers": [],
            "underperformers": [],
            "patterns": [],
            "double_down_topics": [],
            "avoid_topics": [],
            "content_recommendations": [],
            "insights_summary": "No video data available yet. Insights will appear after first videos are published.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "videos_analyzed": 0,
        }
        os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
        with open(args.output_file, "w") as f:
            json.dump(empty_insights, f, indent=2)
        print(args.output_file)
        sys.exit(0)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_analysis_prompt(analytics, args.niche)

    print(f"Analyzing {len(analytics)} video(s) with Claude Haiku...", file=sys.stderr)

    insights = None
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code block if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            insights = json.loads(raw)
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse insights from Claude response: {e}", file=sys.stderr)
                sys.exit(1)

    # Add metadata
    insights["generated_at"] = datetime.now(timezone.utc).isoformat()
    insights["videos_analyzed"] = len(analytics)
    insights["niche"] = args.niche

    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
    with open(args.output_file, "w") as f:
        json.dump(insights, f, indent=2)

    print(f"Insights generated → {args.output_file}", file=sys.stderr)
    print(f"  Summary: {insights.get('insights_summary', '')[:100]}", file=sys.stderr)
    print(args.output_file)


if __name__ == "__main__":
    main()
