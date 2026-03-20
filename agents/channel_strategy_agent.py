"""
channel_strategy_agent.py — One-time channel strategy generator

Run manually (once at channel launch, then quarterly) to generate a comprehensive
90-day channel growth strategy using Claude Sonnet.

Does NOT interact with state.json — runs as an independent process like analytics_agent.

Orchestrates:
  1. Generate channel strategy with Claude Sonnet → .tmp/channel_strategy.json
  2. Write strategy to Google Sheet "Strategy" tab → sheet URL
  3. Send formatted strategy email to APPROVAL_EMAIL

Usage:
    python3 agents/channel_strategy_agent.py
    python3 agents/channel_strategy_agent.py --niche "Finance" --target-audience "25-35 year old professionals"
    python3 agents/channel_strategy_agent.py --competitors "Ali Abdaal,Thomas Frank" --goals "1000 subs in 90 days"
    python3 agents/channel_strategy_agent.py --dry-run
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
STRATEGY_PATH = os.path.join(PROJECT_ROOT, "channel_strategy.json")
PYTHON = sys.executable


def run_tool(tool_name, args_list, capture_output=True):
    """Run a tool script and return stdout. Raises on non-zero exit."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def build_strategy_email(strategy, sheet_url, include_inline=False):
    """Build a human-readable plain-text email body from the strategy JSON."""
    niche = strategy.get("niche", "Your Niche")
    channel_name = os.getenv("CHANNEL_NAME", "Your Channel")
    generated_at = strategy.get("generated_at", "")
    date_str = generated_at[:10] if generated_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    positioning = strategy.get("channel_positioning", {})
    pillars = strategy.get("content_pillars", [])
    schedule = strategy.get("upload_schedule", {})
    milestones = strategy.get("milestone_strategy", {})
    formats = strategy.get("content_formats", [])
    seo_tactics = strategy.get("seo_tactics", [])
    thumbnail_strategy = strategy.get("thumbnail_strategy", "")
    roadmap = strategy.get("roadmap", [])

    sep = "─" * 52

    lines = [
        "CHANNEL STRATEGY REPORT",
        f"{niche.upper()} — {channel_name}",
        f"Generated: {date_str}",
        sep,
        "",
        "CHANNEL POSITIONING",
        f"  Unique Angle:    {positioning.get('unique_angle', '')}",
        f"  Differentiation: {positioning.get('differentiation', '')}",
        f"  Brand Voice:     {positioning.get('brand_voice', '')}",
        "",
        sep,
        "CONTENT PILLARS",
        "",
    ]

    for i, pillar in enumerate(pillars[:4]):
        titles = pillar.get("example_titles", [])
        lines.append(f"  {i + 1}. {pillar.get('name', '')}")
        lines.append(f"     {pillar.get('description', '')}")
        if titles:
            examples = " | ".join(f'"{t}"' for t in titles[:3])
            lines.append(f"     Examples: {examples}")
        lines.append("")

    lines += [
        sep,
        "UPLOAD SCHEDULE",
        f"  Best days:      {', '.join(schedule.get('best_days', []))}",
        f"  Best times EST: {', '.join(schedule.get('best_times_est', []))}",
        f"  Cadence:        {schedule.get('videos_per_week', 3)} videos/week",
        f"  Why:            {schedule.get('rationale', '')}",
        "",
        sep,
        "MILESTONE STRATEGY",
        f"  Days to 1000 subscribers:   {milestones.get('days_to_1000_subs', '')}",
        f"  Days to 4000 watch hours:   {milestones.get('days_to_4000_watch_hours', '')}",
        "",
        "  Fastest path to 1000 subscribers:",
    ]
    for j, step in enumerate(milestones.get("fastest_path_1000_subs", [])[:3]):
        lines.append(f"    {j + 1}. {step}")

    lines += ["", "  Fastest path to 4000 watch hours:"]
    for j, step in enumerate(milestones.get("fastest_path_4000_hours", [])[:3]):
        lines.append(f"    {j + 1}. {step}")

    lines += [
        "",
        sep,
        "CONTENT FORMATS",
        "",
    ]
    for fmt_item in formats[:4]:
        lines.append(f"  {fmt_item.get('format', '')}")
        lines.append(f"    Why it works: {fmt_item.get('why_it_works', '')}")
        lines.append(f"    Example: \"{fmt_item.get('example', '')}\"")
        lines.append("")

    lines += [sep, "SEO TACTICS", ""]
    for tactic in seo_tactics[:6]:
        lines.append(f"  • {tactic}")

    lines += [
        "",
        sep,
        "THUMBNAIL STRATEGY",
        f"  {thumbnail_strategy}",
        "",
        sep,
    ]

    # Roadmap: weeks 1-3 inline when sheet is available; full 10 weeks otherwise
    roadmap_weeks = roadmap if include_inline else roadmap[:3]
    roadmap_note = "" if include_inline else "  (weeks 4-10 in Google Sheet — see link below)"

    lines += ["90-DAY ROADMAP", ""]
    for week in roadmap_weeks:
        videos = week.get("videos", [])
        lines.append(f"  Week {week.get('week', '?')} — {week.get('theme', '')}")
        lines.append(f"    Goal: {week.get('goal', '')}")
        video_str = " | ".join(f'"{v}"' for v in videos[:3])
        lines.append(f"    Videos: {video_str}")
        lines.append("")
    if roadmap_note:
        lines.append(roadmap_note)
        lines.append("")

    lines += [
        sep,
        f"Full strategy in Google Sheet: {sheet_url}",
        "",
        "—YouTube Automation",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Channel strategy agent")
    parser.add_argument("--channel-name", default=None, help="Override CHANNEL_NAME from .env")
    parser.add_argument("--target-audience", default=None, help="Description of target audience")
    parser.add_argument("--competitors", default=None, help="Comma-separated competitor channel names")
    parser.add_argument("--goals", default=None, help="Growth goals (e.g., '1000 subs in 90 days')")
    parser.add_argument("--dry-run", action="store_true", help="Print email without sending")
    args = parser.parse_args()

    channel_name = args.channel_name or os.getenv("CHANNEL_NAME", "TrendingTopics")
    approval_email = os.getenv("APPROVAL_EMAIL")

    if not approval_email:
        print("ERROR: APPROVAL_EMAIL not set in .env", file=sys.stderr)
        sys.exit(1)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"[channel_strategy_agent] Starting for '{channel_name}' ({date_str})")

    os.makedirs(TMP_DIR, exist_ok=True)

    # ── Step 1: Generate Strategy (hard failure) ─────────────────────────────
    print("[1/3] Generating channel strategy with Claude Sonnet...")

    generate_args = ["--niche", channel_name, "--output", STRATEGY_PATH]
    if args.target_audience:
        generate_args += ["--target-audience", args.target_audience]
    if args.competitors:
        generate_args += ["--competitors", args.competitors]
    if args.goals:
        generate_args += ["--goals", args.goals]

    try:
        run_tool("generate_channel_strategy.py", generate_args)
        with open(STRATEGY_PATH) as f:
            strategy = json.load(f)
        print(f"  → Strategy generated: {STRATEGY_PATH}")
    except Exception as e:
        print(f"ERROR: Strategy generation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Step 2: Write to Google Sheet (soft failure) ─────────────────────────
    print("[2/3] Writing strategy to Google Sheet...")
    sheet_url = "[Sheet write failed — full strategy inline below]"
    sheet_failed = False

    try:
        sheet_url = run_tool("write_strategy_to_sheet.py", [
            "--strategy-file", STRATEGY_PATH,
        ])
        print(f"  → Sheet updated: {sheet_url}")
    except Exception as e:
        print(f"  WARNING: Could not write to sheet: {e}", file=sys.stderr)
        sheet_failed = True

    # ── Step 3: Send Email ────────────────────────────────────────────────────
    print("[3/3] Sending strategy email...")
    email_body = build_strategy_email(strategy, sheet_url, include_inline=sheet_failed)
    subject = f"[YT Automation] Channel Strategy Ready — {channel_name} ({date_str})"

    if args.dry_run:
        print("[DRY RUN] Email that would be sent:")
        print(f"  To: {approval_email}")
        print(f"  Subject: {subject}")
        print(f"  Body:\n{email_body}")
        print(f"[channel_strategy_agent] Dry run complete.")
        return

    try:
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", subject,
            "--body", email_body,
        ])
        print(f"  → Strategy email sent to {approval_email}")
    except Exception as e:
        print(f"  WARNING: Could not send email: {e}", file=sys.stderr)

    print(f"[channel_strategy_agent] Done. Strategy saved → {STRATEGY_PATH}")


if __name__ == "__main__":
    main()
