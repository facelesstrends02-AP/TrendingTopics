"""
shorts_agent.py — Produce 2 independent YouTube Shorts for a given publishing day

Instead of deriving Shorts from the full video script, this agent fetches fresh
trending ideas at trigger time and generates 2 standalone Shorts on completely
independent topics — maximising viral potential.

Flow:
  1. Check/refresh .tmp/trending_topics.json (reuse if < 6 hours old)
  2. generate_short_ideas.py  → 2 fresh Short ideas from today's trending topics
  3. generate_short_scripts.py --ideas-file → 2 standalone Short scripts
  4. Per short: voiceover → assemble → upload (private) → schedule publish

Usage:
    python3 agents/shorts_agent.py                     # standalone, key = shorts_YYYY-MM-DD
    python3 agents/shorts_agent.py --video-key video_1 # tied to a specific video in state

State reads:  state.videos.{video_key}.scheduled_publish_at (optional)
              state.videos.{video_key}.script_path          (optional, for hook image only)
State writes: state.videos.{video_key}.shorts.short_{0,1}
              state.videos.{video_key}.shorts_scheduling_triggered (set by shorts_scheduler)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

TRENDING_CACHE_MAX_AGE_HOURS = 6

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
TMP_DIR = os.path.join(PROJECT_ROOT, ".tmp")
PYTHON = sys.executable


# ---------------------------------------------------------------------------
# WAT agent helpers (mirrored from production_agent.py)
# ---------------------------------------------------------------------------

def run_tool(tool_name, args_list, capture_output=True):
    """Run a tool script and return stdout. Raises on non-zero exit."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def _run_raw(tool_name, args_list):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def get_state():
    _, stdout, _ = _run_raw("manage_state.py", ["--read"])
    return json.loads(stdout)


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def log_error(msg: str):
    run_tool("manage_state.py", ["--add-error", msg])
    print(f"[ERROR] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Schedule calculation
# ---------------------------------------------------------------------------

def compute_short_publish_times(scheduled_publish_at: str) -> tuple:
    """
    Returns (slot_0_utc, slot_1_utc) as ISO UTC strings.

    Formula: full_video_day + 1 day
      slot_0: 7:00am IST = 01:30 UTC
      slot_1: 7:00pm IST = 13:30 UTC

    If scheduled_publish_at is None, falls back to today + 1 day.
    """
    if scheduled_publish_at:
        try:
            pub_dt = datetime.fromisoformat(scheduled_publish_at.replace("Z", "+00:00"))
            base_date = pub_dt.date() + timedelta(days=1)
        except Exception:
            base_date = datetime.now(timezone.utc).date() + timedelta(days=1)
    else:
        base_date = datetime.now(timezone.utc).date() + timedelta(days=1)

    slot_0 = datetime(base_date.year, base_date.month, base_date.day,
                      1, 30, 0, tzinfo=timezone.utc)
    slot_1 = datetime(base_date.year, base_date.month, base_date.day,
                      13, 30, 0, tzinfo=timezone.utc)

    return (
        slot_0.strftime("%Y-%m-%dT%H:%M:%SZ"),
        slot_1.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def build_notification_email(video_key: str, produced: dict, channel_name: str) -> str:
    lines = [
        f"YouTube Shorts are scheduled for {video_key} on '{channel_name}'!",
        "",
    ]
    for key, data in produced.items():
        lines.append(f"  {key}: \"{data['short_title']}\"")
        lines.append(f"    URL: {data['youtube_url']}")
        lines.append(f"    Scheduled: {data['scheduled_publish_at']}")
        lines.append("")
    lines.append("— YouTube Automation")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Produce 2 YouTube Shorts")
    parser.add_argument("--video-key", default=None,
                        help="Optional state key (e.g. video_1). Defaults to shorts_YYYY-MM-DD.")
    args = parser.parse_args()
    video_key = args.video_key or f"shorts_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

    state = get_state()
    videos = state.get("videos", {})

    if video_key not in videos:
        print(f"[shorts_agent] '{video_key}' not in state.videos — registering as standalone.")
        update_state({"videos": {video_key: {}}})
        state = get_state()
        videos = state.get("videos", {})

    video_data = videos[video_key]
    channel_name = os.getenv("CHANNEL_NAME", "TrendingTopics")
    approval_email = os.getenv("APPROVAL_EMAIL")

    # Resume check
    existing_shorts = video_data.get("shorts", {})
    if (existing_shorts.get("short_0", {}).get("status") == "scheduled" and
            existing_shorts.get("short_1", {}).get("status") == "scheduled"):
        print(f"[shorts_agent] Both shorts already scheduled for {video_key}. Nothing to do.")
        sys.exit(0)

    # Inputs from state (script_path is optional — Shorts are independent)
    script_path = video_data.get("script_path")
    if script_path and not os.path.exists(script_path):
        script_path = None  # Don't pass a path that doesn't exist

    scheduled_publish_at = (video_data.get("scheduled_publish_at")
                             or video_data.get("scheduled_at"))

    # Step 1: Ensure fresh trending topics (reuse cache if < 6 hours old)
    trending_path = os.path.join(TMP_DIR, "trending_topics.json")
    needs_refresh = True
    if os.path.exists(trending_path):
        age_hours = (datetime.now().timestamp() - os.path.getmtime(trending_path)) / 3600
        if age_hours < TRENDING_CACHE_MAX_AGE_HOURS:
            print(f"[shorts_agent] Reusing trending cache ({age_hours:.1f}h old).")
            needs_refresh = False

    if needs_refresh:
        print(f"[shorts_agent] Fetching fresh trending topics...")
        try:
            run_tool("scrape_trending_topics.py", ["--output", trending_path])
        except Exception as e:
            log_error(f"scrape_trending_topics failed: {e}")
            sys.exit(1)

    # Step 2: Generate 2 independent Short ideas from trending topics
    ideas_path = os.path.join(TMP_DIR, "shorts", f"{video_key}_short_ideas.json")
    os.makedirs(os.path.dirname(ideas_path), exist_ok=True)

    if not os.path.exists(ideas_path):
        print(f"[shorts_agent] Generating fresh Short ideas for {video_key}...")
        try:
            run_tool("generate_short_ideas.py", [
                "--trending-file", trending_path,
                "--output", ideas_path,
            ])
        except Exception as e:
            log_error(f"generate_short_ideas failed for {video_key}: {e}")
            sys.exit(1)
    else:
        print(f"[shorts_agent] Short ideas already exist, skipping idea generation.")

    # Step 3: Generate Short scripts from the independent ideas
    plan_path = os.path.join(TMP_DIR, "shorts", f"{video_key}_shorts_plan.json")

    if not os.path.exists(plan_path):
        print(f"[shorts_agent] Generating short scripts for {video_key}...")
        try:
            run_tool("generate_short_scripts.py", [
                "--ideas-file", ideas_path,
                "--output", plan_path,
            ])
        except Exception as e:
            log_error(f"generate_short_scripts failed for {video_key}: {e}")
            sys.exit(1)
    else:
        print(f"[shorts_agent] Short scripts already exist, skipping generation.")

    with open(plan_path) as f:
        shorts_plan = json.load(f)

    if len(shorts_plan) < 2:
        log_error(f"shorts_plan for {video_key} has fewer than 2 entries.")
        sys.exit(1)

    # Step 3.5: Fact-check the Shorts scripts
    if os.getenv("FACT_CHECK_ENABLED", "1") == "1":
        report_dir = os.path.join(TMP_DIR, "fact_checks")
        os.makedirs(report_dir, exist_ok=True)
        shorts_report_path = os.path.join(report_dir, f"{video_key}_shorts_fact_check.json")
        print(f"[shorts_agent] Fact-checking Short scripts for {video_key}...")

        fc_rc, fc_out, fc_err = _run_raw("fact_check_script.py", [
            "--script-file", plan_path,
            "--output-report", shorts_report_path,
            "--mode", "shorts",
        ])

        if fc_rc == 0:
            try:
                with open(shorts_report_path) as _f:
                    fc_report = json.load(_f)
                revised = fc_report["summary"].get("auto_revised", 0)
                total = fc_report["summary"].get("total_claims", 0)
                print(f"[shorts_agent] Fact-check passed: {total} claims, {revised} auto-revised.")
            except Exception:
                print(f"[shorts_agent] Fact-check passed.")
            # Reload plan in case claims were revised in place
            with open(plan_path) as f:
                shorts_plan = json.load(f)
        elif fc_rc == 2:
            # DISPUTED in a Short — log and continue (lower stakes than full video)
            log_error(
                f"Disputed claims found in Shorts scripts for {video_key}. "
                f"Check {shorts_report_path}. Continuing production."
            )
            # Still reload in case non-disputed claims were auto-revised
            with open(plan_path) as f:
                shorts_plan = json.load(f)
        else:
            print(f"[shorts_agent] WARNING: Fact-check failed (exit {fc_rc}): {fc_err}", file=sys.stderr)
            print(f"[shorts_agent] Continuing without fact-check (graceful degradation).", file=sys.stderr)

    # Compute publish schedule
    publish_slots = compute_short_publish_times(scheduled_publish_at)
    print(f"[shorts_agent] Publish slots: {publish_slots[0]}, {publish_slots[1]}")

    # Step 2: Produce each short
    produced = {}
    failed = []

    for i in range(2):
        short_key = f"short_{i}"

        # Skip if already scheduled
        if existing_shorts.get(short_key, {}).get("status") == "scheduled":
            print(f"[shorts_agent] {short_key} already scheduled, skipping.")
            produced[short_key] = existing_shorts[short_key]
            continue

        short = shorts_plan[i]
        audio_path = os.path.join(TMP_DIR, "audio", f"{video_key}_{short_key}.mp3")
        short_output = os.path.join(TMP_DIR, "shorts", f"{video_key}_{short_key}.mp4")
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        os.makedirs(os.path.dirname(short_output), exist_ok=True)

        print(f"\n[shorts_agent] Producing {short_key}: \"{short['short_title']}\"")

        try:
            # Step 2a: Generate voiceover
            if not (os.path.exists(audio_path) and os.path.getsize(audio_path) > 0):
                print(f"  [a] Generating voiceover...")
                run_tool("generate_voiceover.py", [
                    "--text", short["spoken_script"],
                    "--output", audio_path,
                ])
            else:
                print(f"  [a] Voiceover already exists, skipping.")

            # Step 2b: Assemble short
            if not (os.path.exists(short_output) and os.path.getsize(short_output) > 100_000):
                print(f"  [b] Assembling Short (ffmpeg)...")
                assemble_args = [
                    "--audio-path", audio_path,
                    "--output-path", short_output,
                    "--hook-overlay", short["hook_overlay"],
                    "--cta-overlay", short["cta_overlay"],
                    "--spoken-script", short["spoken_script"],
                    "--pexels-queries", json.dumps(short["pexels_queries"]),
                ]
                if script_path:
                    assemble_args += ["--script-path", script_path]
                run_tool("assemble_short.py", assemble_args, capture_output=False)
            else:
                print(f"  [b] Short video already assembled, skipping.")

            # Step 2c: Upload to YouTube (private)
            print(f"  [c] Uploading to YouTube (private)...")
            upload_out = run_tool("upload_to_youtube.py", [
                "--video-file", short_output,
                "--title", short["short_title"],
                "--description", short["short_description"],
                "--privacy", "private",
            ])
            parts = upload_out.split()
            short_video_id = parts[0] if parts else ""
            short_url = (parts[1] if len(parts) > 1
                         else f"https://www.youtube.com/watch?v={short_video_id}")
            print(f"  ✓ Uploaded: {short_url}")

            # Step 2d: Schedule publish
            publish_at = publish_slots[i]
            print(f"  [d] Scheduling for {publish_at}...")
            run_tool("publish_youtube_video.py", [
                "--video-id", short_video_id,
                "--publish-at", publish_at,
                "--skip-processing-check",
            ])
            print(f"  ✓ Scheduled: {publish_at}")

            # Step 2e: Update state
            short_meta = {
                "short_title": short["short_title"],
                "youtube_video_id": short_video_id,
                "youtube_url": short_url,
                "scheduled_publish_at": publish_at,
                "audio_path": audio_path,
                "video_path": short_output,
                "status": "scheduled",
            }
            update_state({
                "videos": {
                    video_key: {
                        "shorts": {short_key: short_meta}
                    }
                }
            })
            produced[short_key] = short_meta

        except Exception as e:
            error_msg = f"Failed to produce {short_key} for {video_key}: {e}"
            log_error(error_msg)
            print(f"  ✗ {error_msg}", file=sys.stderr)
            failed.append(short_key)

    # Send notification email
    if produced and approval_email:
        try:
            email_body = build_notification_email(video_key, produced, channel_name)
            subject = f"[YT Automation] {len(produced)} Short(s) scheduled for {video_key}"
            if failed:
                subject += f" ({len(failed)} failed)"
            run_tool("send_email.py", [
                "--to", approval_email,
                "--subject", subject,
                "--body", email_body,
            ])
            print(f"\n[shorts_agent] Notification email sent.")
        except Exception as e:
            print(f"WARNING: Could not send notification email: {e}", file=sys.stderr)

    if len(failed) == 2:
        print("[shorts_agent] Both shorts failed.", file=sys.stderr)
        sys.exit(1)

    count = len(produced)
    print(f"\n[shorts_agent] Done. {count}/2 short(s) scheduled for {video_key}.")


if __name__ == "__main__":
    main()
