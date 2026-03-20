"""
manage_state.py — Read/write pipeline state from .tmp/state.json

Usage:
    python3 tools/manage_state.py --read
    python3 tools/manage_state.py --set-phase awaiting_idea_approval
    python3 tools/manage_state.py --write '{"sheet_url": "https://...", "week": "2026-03-08"}'
    python3 tools/manage_state.py --add-error "Something went wrong"
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".tmp", "state.json")

VALID_PHASES = [
    "idle",
    "ideas_generated",
    "awaiting_idea_approval",
    "production_queued",
    "production_in_progress",
    "awaiting_video_approval",
    "publishing_queued",
    "publishing_in_progress",
    "completed",
]

DEFAULT_STATE = {
    "week": None,
    "phase": "idle",
    "sheet_id": None,
    "sheet_url": None,
    "ideas_email_sent_at": None,
    "ideas_email_message_id": None,
    "approved_idea_ids": [],
    "videos": {},
    "review_email_sent_at": None,
    "review_email_message_id": None,
    "thumbnails_generated": False,
    "analytics_run_at": None,
    "errors": [],
}


# ---------------------------------------------------------------------------
# Per-video state schema (for documentation and import by agents).
# Actual entries are written dynamically by production_agent.py / shorts_agent.py.
# ---------------------------------------------------------------------------
DEFAULT_VIDEO_ENTRY = {
    "idea_id": None,
    "title": None,
    "script_path": None,
    "audio_path": None,
    "captions_path": None,
    "footage_dir": None,
    "output_path": None,
    "youtube_video_id": None,
    "youtube_url": None,
    "status": None,           # "uploaded_unlisted" | "scheduled" | "published"
    "published": False,
    "scheduled": False,
    "scheduled_publish_at": None,  # ISO UTC string, e.g. "2026-03-20T03:30:00Z"
    "duration_seconds": None,
    "thumbnail_path": None,
    "thumbnail_uploaded": False,
    "shorts_scheduling_triggered": False,
    "shorts": {},
    # shorts schema (populated by agents/shorts_agent.py):
    # "shorts": {
    #   "short_0": {
    #     "short_title": str,
    #     "youtube_video_id": str,
    #     "youtube_url": str,
    #     "scheduled_publish_at": str,   # ISO UTC
    #     "audio_path": str,
    #     "video_path": str,
    #     "status": "scheduled"
    #   },
    #   "short_1": { ... }
    # }
}


def load_state():
    if not os.path.exists(STATE_PATH):
        return dict(DEFAULT_STATE)
    with open(STATE_PATH, "r") as f:
        return json.load(f)


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, STATE_PATH)


def deep_merge(base, update):
    """Merge update into base dict recursively."""
    result = dict(base)
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description="Manage pipeline state")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--read", action="store_true", help="Print current state as JSON")
    group.add_argument("--set-phase", metavar="PHASE", help="Set the pipeline phase")
    group.add_argument("--write", metavar="JSON", help="Deep-merge JSON fragment into state")
    group.add_argument("--add-error", metavar="MSG", help="Append error message to errors list")
    group.add_argument("--reset", action="store_true", help="Reset to default idle state")

    args = parser.parse_args()

    if args.read:
        state = load_state()
        print(json.dumps(state, indent=2))

    elif args.set_phase:
        phase = args.set_phase
        if phase not in VALID_PHASES:
            print(f"ERROR: Invalid phase '{phase}'. Valid phases: {', '.join(VALID_PHASES)}", file=sys.stderr)
            sys.exit(1)
        state = load_state()
        state["phase"] = phase
        save_state(state)
        print(f"Phase set to: {phase}")

    elif args.write:
        try:
            update = json.loads(args.write)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)
        state = load_state()
        state = deep_merge(state, update)
        save_state(state)
        print("State updated.")

    elif args.add_error:
        state = load_state()
        error_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": args.add_error,
        }
        state.setdefault("errors", []).append(error_entry)
        save_state(state)
        print(f"Error logged: {args.add_error}")

    elif args.reset:
        new_state = dict(DEFAULT_STATE)
        new_state["week"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_state(new_state)
        print("State reset to idle.")


if __name__ == "__main__":
    main()
