"""
viral_idea_agent.py — On-demand viral video idea generator

Run manually anytime to brainstorm viral ideas for a specific topic or niche.
Uses Claude Sonnet with a viral trend researcher prompt for richer, higher-quality ideas
than the weekly cron idea_agent.py.

Does NOT interact with state.json by default.
Use --integrate-pipeline to write ideas into state and trigger approval flow.

Orchestrates:
  1. Optionally scrape YouTube for trending context
  2. Generate viral ideas with Claude Sonnet
  3. Write ideas to Google Sheet
  4. Send email with ideas

Usage:
    python3 agents/viral_idea_agent.py --topic "morning routines"
    python3 agents/viral_idea_agent.py --niche "Finance" --count 10
    python3 agents/viral_idea_agent.py --scraped-file .tmp/scraped_videos.json
    python3 agents/viral_idea_agent.py --dry-run
    python3 agents/viral_idea_agent.py --topic "discipline" --integrate-pipeline
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
PYTHON = sys.executable


def run_tool(tool_name, args_list, capture_output=True):
    """Run a tool script and return stdout. Raises on non-zero exit."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def build_email(ideas_json_path, sheet_url, channel_name, topic=""):
    """Build a human-readable email with viral idea summaries."""
    try:
        with open(ideas_json_path) as f:
            ideas = json.load(f)
    except Exception:
        ideas = []

    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    topic_line = f" — Topic: {topic}" if topic else ""

    lines = [
        f"Viral video ideas for '{channel_name}'{topic_line} ({date_str})",
        "",
        f"Full details in sheet: {sheet_url}",
        "",
        "─" * 52,
        "",
    ]

    for idea in ideas:
        potential = idea.get("potential", "")
        fmt = idea.get("content_format", "")
        trigger = idea.get("target_emotion", "")
        potential_tag = f"[{potential.upper()}]" if potential else ""
        fmt_tag = f"[{fmt}]" if fmt else ""

        lines.append(f"  {idea['id']}. \"{idea['title']}\" {potential_tag} {fmt_tag}")
        lines.append(f"     Hook:        {idea.get('hook', '')[:120]}")
        lines.append(f"     Why viral:   {idea.get('viral_reason', '')[:120]}")
        lines.append(f"     Emotion:     {trigger}")
        lines.append("")

    lines += [
        "─" * 52,
        "These are viral-grade ideas generated with the trend researcher prompt.",
        "To produce any of these, reply with: APPROVE: 1, 3, 7",
        "",
        "—YouTube Automation",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="On-demand viral idea generator")
    parser.add_argument("--topic", default="", help="Focus topic for idea generation")
    parser.add_argument("--channel-name", default=None, help="Override CHANNEL_NAME from .env")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--trending-file", default="", help="Use existing trending_topics.json (skips aggregation)")
    parser.add_argument("--scraped-file", default="", help="Legacy: use existing scraped_videos.json")
    parser.add_argument("--integrate-pipeline", action="store_true",
                        help="Write ideas to state.json and set phase to awaiting_idea_approval")
    parser.add_argument("--dry-run", action="store_true", help="Print email without sending")
    args = parser.parse_args()

    channel_name = args.channel_name or os.getenv("CHANNEL_NAME", "TrendingTopics")
    approval_email = os.getenv("APPROVAL_EMAIL")
    if not approval_email:
        print("ERROR: APPROVAL_EMAIL not set in .env", file=sys.stderr)
        sys.exit(1)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topic_label = f" (topic: {args.topic})" if args.topic else ""
    print(f"[viral_idea_agent] Generating viral ideas for '{channel_name}'{topic_label}")

    os.makedirs(TMP_DIR, exist_ok=True)

    # ── Step 1: Aggregate Trending Topics (if no file provided) ─────────────
    trending_path = args.trending_file or args.scraped_file
    if not trending_path:
        print("[1/4] Aggregating trending topics (Google Trends, RSS, Reddit, YouTube)...")
        trending_path = os.path.join(TMP_DIR, "trending_topics.json")
        try:
            run_tool("scrape_trending_topics.py", [
                "--max-results", "50",
                "--output", trending_path,
            ])
            print(f"  → Trending topics: {trending_path}")
        except Exception as e:
            print(f"  WARNING: Trending aggregation failed ({e}), proceeding without context.", file=sys.stderr)
            trending_path = ""
    else:
        print(f"[1/4] Using existing trending file: {trending_path}")

    # ── Step 2: Generate Viral Ideas ─────────────────────────────────────────
    print(f"[2/4] Generating {args.count} viral ideas with Claude Sonnet...")
    ideas_output = os.path.join(TMP_DIR, "viral_ideas.json")
    try:
        generate_args = [
            "--channel-name", channel_name,
            "--count", str(args.count),
            "--output", ideas_output,
        ]
        if trending_path:
            generate_args += ["--trending-file", trending_path]
        if args.topic:
            generate_args += ["--topic", args.topic]
        ideas_path = run_tool("generate_viral_ideas.py", generate_args)
        print(f"  → Ideas: {ideas_path}")
    except Exception as e:
        print(f"ERROR: Idea generation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Step 3: Write to Google Sheet ────────────────────────────────────────
    print("[3/4] Writing ideas to Google Sheet...")
    sheet_url = f"[Sheet unavailable — check {ideas_path}]"
    try:
        sheet_url = run_tool("write_ideas_to_sheet.py", ["--ideas-file", ideas_path])
        print(f"  → Sheet: {sheet_url}")
    except Exception as e:
        print(f"  WARNING: Could not write to sheet: {e}", file=sys.stderr)

    # ── Step 4: Send Email ────────────────────────────────────────────────────
    print("[4/4] Composing email...")
    email_body = build_email(ideas_path, sheet_url, channel_name, args.topic)
    subject = f"[YT Automation] {args.count} Viral Ideas Ready — {channel_name}{topic_label} ({date_str})"

    if args.dry_run:
        print("[DRY RUN] Email that would be sent:")
        print(f"  To: {approval_email}")
        print(f"  Subject: {subject}")
        print(f"  Body:\n{email_body}")
        print("[viral_idea_agent] Dry run complete.")
        return

    try:
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", subject,
            "--body", email_body,
        ])
        print(f"  → Email sent to {approval_email}")
    except Exception as e:
        print(f"  WARNING: Could not send email: {e}", file=sys.stderr)
        print(f"  Ideas available at: {ideas_path}")

    # ── Optional: Integrate with pipeline state ───────────────────────────────
    if args.integrate_pipeline:
        try:
            week_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            run_tool("manage_state.py", ["--reset"])
            run_tool("manage_state.py", ["--write", json.dumps({"week": week_str, "sheet_url": sheet_url})])
            run_tool("manage_state.py", ["--set-phase", "awaiting_idea_approval"])
            print("  → State updated: awaiting_idea_approval (approval_poller.py will pick up replies)")
        except Exception as e:
            print(f"  WARNING: Could not update pipeline state: {e}", file=sys.stderr)

    print(f"[viral_idea_agent] Done. Ideas saved → {ideas_path}")


if __name__ == "__main__":
    main()
