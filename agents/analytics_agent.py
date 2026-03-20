"""
analytics_agent.py — Weekly video performance analytics and feedback loop

Runs every Sunday at 9pm (1 hour before idea_agent.py at 10pm).

Orchestrates:
  1. Load all published video IDs from persistent registry
  2. Fetch performance stats from YouTube Data API
  3. Analyze with Claude Haiku to generate insights
  4. Write analytics + insights to Google Sheet "Analytics" tab
  5. Save insights to .tmp/analytics_insights.json (used by idea_agent next hour)
  6. Email weekly analytics summary to user

This agent does NOT interact with state.json — it runs as an independent side process.

Usage:
    python3 agents/analytics_agent.py
    python3 agents/analytics_agent.py --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
TMP_DIR = os.path.join(PROJECT_ROOT, ".tmp")
REGISTRY_PATH = os.path.join(TMP_DIR, "published_videos_registry.json")
PYTHON = sys.executable


def run_tool(tool_name, args_list, capture_output=True):
    """Run a tool script and return stdout. Raises on non-zero exit."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def load_registry():
    """Load the persistent published videos registry."""
    if not os.path.exists(REGISTRY_PATH):
        return []
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def build_analytics_email(analytics, insights, sheet_url, niche):
    """Build the weekly analytics summary email body."""
    week_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    lines = [
        f"Weekly YouTube Analytics Report — {week_str}",
        f"Channel niche: {niche}",
        "",
    ]

    if not analytics:
        lines += [
            "No video data available yet.",
            "Analytics will appear here after your first videos are published.",
            "",
            "—YouTube Automation",
        ]
        return "\n".join(lines)

    # Top-level stats
    total_views = sum(v["view_count"] for v in analytics)
    avg_engagement = sum(v["engagement_rate"] for v in analytics) / len(analytics)

    lines += [
        f"Videos tracked: {len(analytics)}",
        f"Total views: {total_views:,}",
        f"Avg engagement: {avg_engagement:.2%}",
        "",
        "─" * 50,
        "VIDEO PERFORMANCE:",
        "",
    ]

    # Sort by views
    for v in sorted(analytics, key=lambda x: x["view_count"], reverse=True):
        eng_pct = f"{v['engagement_rate']:.2%}"
        lines.append(f"  \"{v['title'][:60]}\"")
        lines.append(f"   {v['view_count']:,} views | {v['like_count']:,} likes | {eng_pct} engagement")
        lines.append("")

    lines += ["─" * 50, ""]

    if insights and insights.get("insights_summary"):
        lines += [
            "KEY INSIGHTS (Claude Haiku analysis):",
            "",
            insights["insights_summary"],
            "",
        ]

    if insights and insights.get("double_down_topics"):
        lines.append("Double down on:")
        for t in insights["double_down_topics"]:
            lines.append(f"  ✓ {t}")
        lines.append("")

    if insights and insights.get("avoid_topics"):
        lines.append("Avoid or reframe:")
        for t in insights["avoid_topics"]:
            lines.append(f"  ✗ {t}")
        lines.append("")

    lines += [
        "─" * 50,
        f"Full analytics: {sheet_url}",
        "",
        "These insights will automatically influence the ideas generated tonight at 10pm.",
        "",
        "—YouTube Automation",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Weekly analytics agent")
    parser.add_argument("--dry-run", action="store_true", help="Run without sending email")
    args = parser.parse_args()

    niche = os.getenv("NICHE", "Self Development")
    approval_email = os.getenv("APPROVAL_EMAIL")
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    week_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not approval_email:
        print("ERROR: APPROVAL_EMAIL not set in .env", file=sys.stderr)
        sys.exit(1)

    print(f"[analytics_agent] Starting weekly analytics for '{niche}' (week {week_str})")

    # ── Step 1: Load Published Videos Registry ──────────────────────────────
    print("[1/5] Loading published video registry...")
    registry = load_registry()

    if not registry:
        print("  → No published videos yet. Skipping analytics.")
        if not args.dry_run:
            try:
                run_tool("send_email.py", [
                    "--to", approval_email,
                    "--subject", f"[YT Automation] Analytics - Week of {week_str} (No data yet)",
                    "--body", f"No published videos found yet. Analytics will begin appearing once your first videos are published.\n\n—YouTube Automation",
                ])
            except Exception:
                pass
        sys.exit(0)

    video_ids = [v["youtube_video_id"] for v in registry if v.get("youtube_video_id")]
    print(f"  → {len(video_ids)} published video(s) in registry")

    # ── Step 2: Fetch Video Analytics ───────────────────────────────────────
    print("[2/5] Fetching video analytics from YouTube...")
    analytics_path = os.path.join(TMP_DIR, f"analytics_{week_str}.json")
    os.makedirs(TMP_DIR, exist_ok=True)

    try:
        run_tool("fetch_video_analytics.py", [
            "--video-ids", ",".join(video_ids),
            "--output-file", analytics_path,
        ])
        with open(analytics_path) as f:
            analytics = json.load(f)
        print(f"  → Fetched stats for {len(analytics)} video(s)")
    except Exception as e:
        print(f"  ERROR: Could not fetch analytics: {e}", file=sys.stderr)
        analytics = []
        analytics_path = None

    # ── Step 3: Analyze with Claude ─────────────────────────────────────────
    print("[3/5] Analyzing performance with Claude Haiku...")
    insights_path = os.path.join(TMP_DIR, "analytics_insights.json")

    if analytics_path and analytics:
        try:
            run_tool("analyze_performance.py", [
                "--analytics-file", analytics_path,
                "--niche", niche,
                "--output-file", insights_path,
            ])
            with open(insights_path) as f:
                insights = json.load(f)
            print(f"  → Insights generated")
        except Exception as e:
            print(f"  WARNING: Analysis failed: {e}", file=sys.stderr)
            insights = {}
    else:
        insights = {}
        print("  → Skipped (no analytics data)")

    # ── Step 4: Write to Google Sheet ───────────────────────────────────────
    print("[4/5] Writing to Google Sheet...")
    sheet_url = "[Sheet not configured]"

    if analytics and analytics_path:
        # Write empty insights file if it doesn't exist
        if not os.path.exists(insights_path):
            with open(insights_path, "w") as f:
                json.dump({}, f)

        try:
            sheet_url = run_tool("write_analytics_to_sheet.py", [
                "--analytics-file", analytics_path,
                "--insights-file", insights_path,
            ])
            print(f"  → Sheet updated: {sheet_url}")
        except Exception as e:
            print(f"  WARNING: Could not write to sheet: {e}", file=sys.stderr)
    else:
        print("  → Skipped (no data or no sheet ID)")

    # ── Step 5: Send Email Summary ───────────────────────────────────────────
    print("[5/5] Sending analytics email...")
    email_body = build_analytics_email(analytics, insights, sheet_url, niche)
    subject = f"[YT Automation] Weekly Analytics - Week of {week_str}"

    if args.dry_run:
        print("[DRY RUN] Email that would be sent:")
        print(f"  To: {approval_email}")
        print(f"  Subject: {subject}")
        print(f"  Body:\n{email_body}")
        print("[analytics_agent] Dry run complete.")
        return

    try:
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", subject,
            "--body", email_body,
        ])
        print(f"  → Analytics email sent")
    except Exception as e:
        print(f"  WARNING: Could not send email: {e}", file=sys.stderr)

    print(f"[analytics_agent] Done. Insights saved → {insights_path}")
    print("  (idea_agent.py will load these in ~1 hour)")


if __name__ == "__main__":
    main()
