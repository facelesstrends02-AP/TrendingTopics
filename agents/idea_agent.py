"""
idea_agent.py — Weekly idea generation agent

Runs every Sunday night via cron. Orchestrates:
  1. Aggregate trending topics from Google Trends, RSS feeds, Reddit, YouTube, NewsAPI
  2. Generate 10 video ideas with Claude Sonnet
  3. Write ideas to Google Sheet
  4. Email user with ideas + sheet link + approval instructions

Usage:
    python3 agents/idea_agent.py
    python3 agents/idea_agent.py --channel-name "The Pulse"  # override channel name
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
PYTHON = sys.executable


def run_tool(tool_name, args_list, capture_output=True):
    """Run a tool script and return stdout. Raises on non-zero exit."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def set_phase(phase: str):
    run_tool("manage_state.py", ["--set-phase", phase])


def log_error(msg: str):
    run_tool("manage_state.py", ["--add-error", msg])
    print(f"[ERROR] {msg}", file=sys.stderr)


def build_ideas_email(ideas_json_path, sheet_url, channel_name):
    """Build the approval request email body."""
    try:
        with open(ideas_json_path) as f:
            ideas = json.load(f)
    except Exception:
        ideas = []

    week_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    lines = [
        f"Your weekly YouTube video ideas for '{channel_name}' are ready!",
        "",
        f"Review the full sheet: {sheet_url}",
        "",
        "Quick preview:",
        "",
    ]

    for idea in ideas:
        potential = idea.get("potential", "")
        category = idea.get("category", "")
        potential_tag = f"[{potential.upper()}]" if potential else ""
        lines.append(f"  {idea['id']}. {category} {potential_tag}")

    lines += [
        "",
        "-" * 50,
        "To approve ideas, reply to this email with:",
        "",
        "  APPROVE: 1, 3, 7        (approve specific ideas by number)",
        "  APPROVE ALL             (approve all 10)",
        "  APPROVE: 1-5            (approve a range)",
        "  APPROVE ALL EXCEPT: 4, 9",
        "",
        "Review full titles and hooks in the sheet before approving.",
        "",
        "-YouTube Automation",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Weekly idea generation agent")
    parser.add_argument("--channel-name", default=None, help="Override CHANNEL_NAME from .env")
    parser.add_argument("--dry-run", action="store_true", help="Run without sending email")
    args = parser.parse_args()

    channel_name = args.channel_name or os.getenv("CHANNEL_NAME", "TrendingTopics")
    approval_email = os.getenv("APPROVAL_EMAIL")
    if not approval_email:
        print("ERROR: APPROVAL_EMAIL not set in .env", file=sys.stderr)
        sys.exit(1)

    week_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"[idea_agent] Starting weekly idea generation for '{channel_name}' (week {week_str})")

    # Reset state for new week
    try:
        run_tool("manage_state.py", ["--reset"])
        update_state({"week": week_str})
    except Exception as e:
        print(f"WARNING: Could not reset state: {e}", file=sys.stderr)

    # ── Step 0a: Ensure channel strategy exists ─────────────────────────────
    strategy_path = os.path.join(PROJECT_ROOT, "channel_strategy.json")
    if not os.path.exists(strategy_path):
        print("[0/5] No channel_strategy.json found — generating now...")
        try:
            cmd = [PYTHON, os.path.join(PROJECT_ROOT, "agents", "channel_strategy_agent.py")]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print("  → Channel strategy generated.")
            else:
                print(f"  WARNING: Strategy generation failed (non-fatal): {result.stderr.strip()}", file=sys.stderr)
        except Exception as e:
            print(f"  WARNING: Could not generate strategy (non-fatal): {e}", file=sys.stderr)

    # ── Step 0: Load Analytics Context (if available) ──────────────────────
    print("[0/5] Loading analytics context...")
    analytics_context = ""
    try:
        sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
        analytics_context = run_tool("load_analytics_context.py", [
            "--sheet-id", sheet_id,
            "--weeks", "4",
        ])
        if analytics_context:
            print(f"  → Analytics context loaded ({len(analytics_context)} chars)")
        else:
            print("  → No analytics context yet (first run or no published videos)")
    except Exception as e:
        print(f"  → Analytics context unavailable (non-fatal): {e}", file=sys.stderr)

    # ── Step 1: Aggregate Trending Topics ──────────────────────────────────
    print("[1/5] Aggregating trending topics (Google Trends, RSS, Reddit, YouTube, NewsAPI)...")
    trending_path = os.path.join(PROJECT_ROOT, ".tmp", "trending_topics.json")
    try:
        result_json = run_tool("scrape_trending_topics.py", [
            "--max-results", "50",
            "--output", trending_path,
        ])
        print(f"  → Trending topics: {trending_path}")
    except Exception as e:
        log_error(f"Trending topics aggregation failed: {e}")
        if not args.dry_run:
            try:
                run_tool("send_email.py", [
                    "--to", approval_email,
                    "--subject", f"[YT Automation] ERROR: Idea generation failed ({week_str})",
                    "--body", f"The weekly trending topics aggregation encountered an error:\n\n{e}\n\nPlease check the logs at .tmp/cron.log",
                ])
            except Exception:
                pass
        sys.exit(1)

    # ── Step 2: Generate Ideas ──────────────────────────────────────────────
    print("[2/5] Generating 10 video ideas with Claude Sonnet (viral researcher mode)...")
    try:
        generate_args = [
            "--trending-file", trending_path,
            "--channel-name", channel_name,
            "--count", "10",
            "--output", os.path.join(PROJECT_ROOT, ".tmp", "ideas.json"),
        ]
        if analytics_context:
            generate_args += ["--analytics-context", analytics_context]
        ideas_path = run_tool("generate_viral_ideas.py", generate_args)
        print(f"  → Ideas: {ideas_path}")
    except Exception as e:
        log_error(f"Idea generation failed: {e}")
        if not args.dry_run:
            try:
                run_tool("send_email.py", [
                    "--to", approval_email,
                    "--subject", f"[YT Automation] ERROR: Idea generation failed ({week_str})",
                    "--body", f"Claude failed to generate ideas:\n\n{e}",
                ])
            except Exception:
                pass
        sys.exit(1)

    # ── Step 3: Write to Google Sheet ───────────────────────────────────────
    print("[3/5] Writing ideas to Google Sheet...")
    try:
        sheet_url = run_tool("write_ideas_to_sheet.py", [
            "--ideas-file", ideas_path,
        ])
        print(f"  → Sheet: {sheet_url}")
        update_state({"sheet_url": sheet_url})
        # Extract sheet ID from URL
        sheet_id = sheet_url.split("/d/")[1].split("/")[0] if "/d/" in sheet_url else ""
        if sheet_id:
            update_state({"sheet_id": sheet_id})
    except Exception as e:
        log_error(f"Sheet write failed: {e}")
        sheet_url = f"[Sheet unavailable - check {ideas_path}]"

    # ── Step 4: Build and send email ────────────────────────────────────────
    print("[4/5] Composing approval email...")
    ideas_json = os.path.join(PROJECT_ROOT, ".tmp", "ideas.json")
    email_body = build_ideas_email(ideas_json, sheet_url, channel_name)

    subject = f"[YT Automation] 10 Ideas Ready - Week of {week_str} - Reply to Approve"

    if args.dry_run:
        print("[DRY RUN] Email that would be sent:")
        print(f"  To: {approval_email}")
        print(f"  Subject: {subject}")
        print(f"  Body:\n{email_body}")
        set_phase("awaiting_idea_approval")
        print("[idea_agent] Dry run complete.")
        return

    print(f"[5/5] Sending email to {approval_email}...")
    try:
        message_id = run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", subject,
            "--body", email_body,
        ])
        print(f"  → Email sent, message ID: {message_id}")
        update_state({
            "ideas_email_message_id": message_id,
            "ideas_email_sent_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        log_error(f"Email send failed: {e}")
        print(f"WARNING: Could not send email: {e}", file=sys.stderr)
        print(f"Ideas are ready at: {sheet_url}", file=sys.stderr)

    # ── Step 5: Update phase ────────────────────────────────────────────────
    set_phase("awaiting_idea_approval")
    print("[idea_agent] Done. Waiting for approval reply.")


if __name__ == "__main__":
    main()
