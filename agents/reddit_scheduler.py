"""
reddit_scheduler.py — Cron-triggered agent that launches reddit_agent.py for today's videos

Checks state.json for videos whose scheduled_publish_at date matches today, and
launches reddit_agent.py as a non-blocking subprocess for each one that hasn't
already had Reddit posting triggered.

Intended to run ~30 minutes after videos go public (8:00 AM IST = 02:30 UTC).

Cron entry (Mon/Wed/Fri 8:00 AM IST = 02:30 UTC) — add when ready:
    30 2 * * 1,3,5   cd /path/to/TrendingTopics && venv/bin/python agents/reddit_scheduler.py >> .tmp/cron.log 2>&1

Manual usage:
    python3 agents/reddit_scheduler.py
    python3 agents/reddit_scheduler.py --dry-run
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


def run_tool(tool_name, args_list):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def get_state():
    raw = run_tool("manage_state.py", ["--read"])
    return json.loads(raw)


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def main():
    parser = argparse.ArgumentParser(description="Schedule Reddit posting for today's videos")
    parser.add_argument("--dry-run", action="store_true", help="Print without launching agents")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    print(f"[reddit_scheduler] Running for date: {today}", file=sys.stderr)

    try:
        state = get_state()
    except Exception as e:
        print(f"ERROR: Could not read state: {e}", file=sys.stderr)
        sys.exit(1)

    videos = state.get("videos", {})
    launched = 0

    for video_key, video_data in videos.items():
        scheduled_publish_at = video_data.get("scheduled_publish_at")
        if not scheduled_publish_at:
            continue

        try:
            pub_dt = datetime.fromisoformat(scheduled_publish_at.replace("Z", "+00:00"))
            pub_date = pub_dt.date()
        except Exception as e:
            print(f"  WARNING: Could not parse scheduled_publish_at for {video_key}: {e}", file=sys.stderr)
            continue

        if pub_date != today:
            continue

        if video_data.get("reddit_scheduling_triggered"):
            print(f"  {video_key}: Reddit already triggered, skipping.", file=sys.stderr)
            continue

        if not video_data.get("youtube_url") and not video_data.get("public_url"):
            print(f"  {video_key}: no YouTube URL yet, skipping.", file=sys.stderr)
            continue

        title = video_data.get("title", video_key)
        print(f"  Launching reddit_agent for {video_key}: {title}", file=sys.stderr)

        if not args.dry_run:
            cmd = [PYTHON, os.path.join(PROJECT_ROOT, "agents", "reddit_agent.py"),
                   "--video-key", video_key]
            subprocess.Popen(cmd)

            try:
                update_state({"videos": {video_key: {"reddit_scheduling_triggered": True}}})
            except Exception as e:
                print(f"  WARNING: Could not update state for {video_key}: {e}", file=sys.stderr)
        else:
            print(f"  [DRY RUN] Would launch: reddit_agent.py --video-key {video_key}", file=sys.stderr)

        launched += 1

    if args.dry_run:
        print(f"[reddit_scheduler] DRY RUN — would launch {launched} reddit_agent process(es).")
    else:
        print(f"[reddit_scheduler] Launched {launched} reddit_agent process(es).")


if __name__ == "__main__":
    main()
