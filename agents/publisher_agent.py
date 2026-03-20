"""
publisher_agent.py — Publish approved YouTube videos (unlisted → public)

Triggered by approval_poller.py when video approval reply is received.

Usage:
    python3 agents/publisher_agent.py
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
TMP_DIR = os.path.join(PROJECT_ROOT, ".tmp")
PYTHON = sys.executable
REGISTRY_PATH = os.path.join(TMP_DIR, "published_videos_registry.json")


def run_tool(tool_name, args_list, capture_output=True):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def get_state():
    _, stdout, _ = _run_raw("manage_state.py", ["--read"])
    return json.loads(stdout)


def _run_raw(tool_name, args_list):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def set_phase(phase: str):
    run_tool("manage_state.py", ["--set-phase", phase])


def log_error(msg: str):
    run_tool("manage_state.py", ["--add-error", msg])
    print(f"[ERROR] {msg}", file=sys.stderr)


def get_publish_slots(n: int) -> list:
    """Return n UTC ISO timestamps for Mon/Wed/Fri at 7am IST (01:30 UTC) starting next week."""
    # IST is UTC+5:30, so 7am IST = 01:30 UTC
    PUBLISH_HOUR_UTC = 1
    PUBLISH_MINUTE_UTC = 30
    # Mon=0, Wed=2, Fri=4
    WEEKDAYS = [0, 2, 4]

    today = datetime.now(timezone.utc).date()
    # Find next Monday (day after Sunday approval)
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7  # if today is Monday, use next Monday
    next_monday = today + timedelta(days=days_until_monday)

    slots = []
    for i in range(n):
        day_offset = WEEKDAYS[i % 3]
        slot_date = next_monday + timedelta(days=day_offset)
        slot_dt = datetime(slot_date.year, slot_date.month, slot_date.day,
                           PUBLISH_HOUR_UTC, PUBLISH_MINUTE_UTC, 0, tzinfo=timezone.utc)
        slots.append(slot_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return slots


def main():
    state = get_state()
    approved_video_ids = state.get("approved_video_ids", [])
    videos = state.get("videos", {})
    niche = os.getenv("NICHE", "Self Development")
    approval_email = os.getenv("APPROVAL_EMAIL")

    if not approved_video_ids:
        print("[publisher_agent] No approved video IDs found.")
        sys.exit(0)

    if not approval_email:
        print("ERROR: APPROVAL_EMAIL not set in .env", file=sys.stderr)
        sys.exit(1)

    set_phase("publishing_in_progress")
    print(f"[publisher_agent] Scheduling {len(approved_video_ids)} video(s) for Mon/Wed/Fri at 7am IST...")

    # Sort approved videos by key (video_1, video_2, video_3) so slot assignment is deterministic
    approved_videos = sorted(
        [(k, v) for k, v in videos.items() if v.get("youtube_video_id") in approved_video_ids],
        key=lambda x: x[0]
    )
    publish_slots = get_publish_slots(len(approved_videos))

    # Day label map for readable email (Mon=0, Tue=1, ..., Sun=6)
    DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    published = []
    failed = []

    for i, (video_key, video_data) in enumerate(approved_videos):
        yt_id = video_data.get("youtube_video_id")
        title = video_data.get("title", "Untitled")
        publish_at = publish_slots[i]

        print(f"  Scheduling: '{title}' ({yt_id}) → {publish_at}...")
        try:
            public_url = run_tool("publish_youtube_video.py", [
                "--video-id", yt_id,
                "--publish-at", publish_at,
            ])

            # Update video status in state
            update_state({
                "videos": {
                    video_key: {
                        **video_data,
                        "scheduled": True,
                        "public_url": public_url,
                        "scheduled_publish_at": publish_at,
                    }
                }
            })

            # Parse day name for email
            slot_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            day_label = DAY_NAMES[slot_dt.weekday()]
            published.append({"title": title, "url": public_url, "day": day_label, "publish_at": publish_at})
            print(f"  ✓ Scheduled for {day_label}: {public_url}")

            # Append to persistent registry (survives weekly state resets — used by analytics)
            try:
                registry = json.load(open(REGISTRY_PATH)) if os.path.exists(REGISTRY_PATH) else []
                registry.append({
                    "youtube_video_id": yt_id,
                    "title": title,
                    "scheduled_publish_at": publish_at,
                    "week": state.get("week"),
                    "public_url": public_url,
                })
                os.makedirs(TMP_DIR, exist_ok=True)
                with open(REGISTRY_PATH, "w") as f:
                    json.dump(registry, f, indent=2)
            except Exception as reg_err:
                print(f"  WARNING: Could not update video registry: {reg_err}", file=sys.stderr)

        except Exception as e:
            error_msg = f"Failed to schedule {title} ({yt_id}): {e}"
            log_error(error_msg)
            print(f"  ✗ {error_msg}", file=sys.stderr)
            failed.append(title)

    # Send completion email
    week_str = state.get("week", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    if published:
        lines = [
            f"{len(published)} video(s) scheduled to publish this week at 7am IST!",
            "",
        ]
        for vid in published:
            # Convert UTC to IST for display (UTC+5:30)
            slot_dt = datetime.fromisoformat(vid["publish_at"].replace("Z", "+00:00"))
            ist_dt = slot_dt + timedelta(hours=5, minutes=30)
            lines.append(f"  ✓ \"{vid['title']}\"")
            lines.append(f"    {vid['day']} {ist_dt.strftime('%d %b')} at 7:00am IST")
            lines.append(f"    {vid['url']}")
            lines.append("")

        if failed:
            lines.append(f"Note: {len(failed)} video(s) failed to schedule: {', '.join(failed)}")
            lines.append("")

        lines += [
            "─" * 50,
            "Next steps:",
            "  • YouTube will publish each video automatically at the scheduled time",
            "  • Check analytics in 48 hours after each publish",
            f"  • New ideas will be generated next Sunday",
            "",
            "—YouTube Automation",
        ]

        subject = f"[YT Automation] {len(published)} Video(s) Scheduled for This Week!"
        if failed:
            subject += f" ({len(failed)} failed)"

    else:
        lines = [
            "Scheduling failed for all videos.",
            "",
            f"Failed: {', '.join(failed)}",
            "",
            "The videos remain unlisted on YouTube. You can schedule or publish them manually.",
        ]
        subject = "[YT Automation] ERROR: Video scheduling failed"

    try:
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", subject,
            "--body", "\n".join(lines),
        ])
        print(f"  → Completion email sent.")
    except Exception as e:
        print(f"WARNING: Could not send completion email: {e}", file=sys.stderr)

    set_phase("completed")
    print(f"[publisher_agent] Done. Scheduled: {len(published)}, Failed: {len(failed)}")


if __name__ == "__main__":
    main()
