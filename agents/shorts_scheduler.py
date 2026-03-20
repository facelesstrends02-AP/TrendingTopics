"""
shorts_scheduler.py — Cron-triggered agent that launches shorts_agent.py for today's videos

Checks state.json for videos whose scheduled_publish_at date matches today, and
launches shorts_agent.py as a non-blocking subprocess for each one that hasn't
already had shorts triggered.

Cron entry (Mon/Wed/Fri 23:00 IST = 17:30 UTC, 2h after full video publish):
    30 17 * * 1,3,5   cd /path/to/TrendingTopics && python agents/shorts_scheduler.py >> .tmp/cron.log 2>&1

Usage:
    python3 agents/shorts_scheduler.py
"""

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
    """Run a tool script. Raises on non-zero exit."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{tool_name} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def get_state():
    raw = run_tool("manage_state.py", ["--read"])
    return json.loads(raw)


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def main():
    today = datetime.now(timezone.utc).date()
    print(f"[shorts_scheduler] Running for date: {today}", file=sys.stderr)

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
            print(f"  WARNING: Could not parse scheduled_publish_at for {video_key}: {e}",
                  file=sys.stderr)
            continue

        if pub_date != today:
            continue

        if video_data.get("shorts_scheduling_triggered"):
            print(f"  {video_key}: shorts already triggered, skipping.", file=sys.stderr)
            continue

        if not video_data.get("youtube_video_id"):
            print(f"  {video_key}: no youtube_video_id yet, skipping.", file=sys.stderr)
            continue

        # Launch shorts_agent.py as a non-blocking subprocess
        cmd = [PYTHON, os.path.join(PROJECT_ROOT, "agents", "shorts_agent.py"),
               "--video-key", video_key]
        subprocess.Popen(cmd)
        print(f"  Launched shorts_agent for {video_key} (publish date: {pub_date})",
              file=sys.stderr)
        launched += 1

        # Mark as triggered immediately so re-runs don't double-launch
        try:
            update_state({"videos": {video_key: {"shorts_scheduling_triggered": True}}})
        except Exception as e:
            print(f"  WARNING: Could not update state for {video_key}: {e}", file=sys.stderr)

    print(f"[shorts_scheduler] Launched {launched} shorts_agent process(es).")


if __name__ == "__main__":
    main()
