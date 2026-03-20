"""
load_analytics_context.py — Load performance analytics context for idea generation

Reads .tmp/analytics_insights.json and formats it as a plain-text context block
suitable for injection into the generate_ideas.py prompt.

Returns empty string (exit 0) if no insights exist — graceful first-run behavior.

Usage:
    python3 tools/load_analytics_context.py \
        --sheet-id 1BxiMV... \
        --weeks 4

Output (stdout): formatted context string (may be empty)
Exit code: 0 always (missing data is not an error)
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
INSIGHTS_PATH = os.path.join(PROJECT_ROOT, ".tmp", "analytics_insights.json")


def format_context(insights):
    """Format insights JSON into a plain-text block for prompt injection."""
    if not insights or insights.get("videos_analyzed", 0) == 0:
        return ""

    lines = []

    summary = insights.get("insights_summary", "")
    if summary:
        lines.append(f"CHANNEL PERFORMANCE SUMMARY:")
        lines.append(summary)
        lines.append("")

    top = insights.get("top_performers", [])
    if top:
        lines.append("TOP PERFORMING VIDEOS (replicate these patterns):")
        for v in top[:3]:
            title = v.get("title", "")
            views = v.get("views", 0)
            eng = v.get("engagement_rate", 0)
            why = v.get("why_it_worked", "")
            eng_pct = f"{eng:.1%}" if isinstance(eng, float) else str(eng)
            lines.append(f"  - \"{title}\" — {views:,} views, {eng_pct} engagement")
            if why:
                lines.append(f"    Why it worked: {why}")
        lines.append("")

    under = insights.get("underperformers", [])
    if under:
        lines.append("UNDERPERFORMING VIDEOS (avoid these patterns):")
        for v in under[:2]:
            title = v.get("title", "")
            views = v.get("views", 0)
            why = v.get("why_it_flopped", "")
            lines.append(f"  - \"{title}\" — {views:,} views")
            if why:
                lines.append(f"    Why it flopped: {why}")
        lines.append("")

    patterns = insights.get("patterns", [])
    if patterns:
        lines.append("OBSERVED PATTERNS:")
        for p in patterns[:5]:
            lines.append(f"  - {p}")
        lines.append("")

    double_down = insights.get("double_down_topics", [])
    if double_down:
        lines.append("DOUBLE DOWN ON:")
        for t in double_down[:4]:
            lines.append(f"  - {t}")
        lines.append("")

    avoid = insights.get("avoid_topics", [])
    if avoid:
        lines.append("AVOID OR REFRAME:")
        for t in avoid[:4]:
            lines.append(f"  - {t}")
        lines.append("")

    recs = insights.get("content_recommendations", [])
    if recs:
        lines.append("RECOMMENDATIONS:")
        for r in recs[:3]:
            lines.append(f"  - {r}")

    return "\n".join(lines).strip()


def main():
    parser = argparse.ArgumentParser(description="Load analytics context for idea generation")
    parser.add_argument("--sheet-id", default="", help="Google Sheet ID (unused, reserved for future)")
    parser.add_argument("--weeks", type=int, default=4, help="Number of weeks of history to include")
    args = parser.parse_args()

    if not os.path.exists(INSIGHTS_PATH):
        print("No analytics insights found (first run — skipping context)", file=sys.stderr)
        print("")  # Empty context — generate_ideas.py handles this gracefully
        sys.exit(0)

    try:
        with open(INSIGHTS_PATH) as f:
            insights = json.load(f)
    except Exception as e:
        print(f"WARNING: Could not load insights: {e}", file=sys.stderr)
        print("")
        sys.exit(0)

    context = format_context(insights)

    if context:
        generated_at = insights.get("generated_at", "unknown")
        videos_analyzed = insights.get("videos_analyzed", 0)
        print(
            f"Analytics context loaded ({videos_analyzed} videos, generated {generated_at[:10]})",
            file=sys.stderr,
        )
    else:
        print("Analytics insights exist but contain no data yet", file=sys.stderr)

    print(context)  # stdout — may be empty string


if __name__ == "__main__":
    main()
