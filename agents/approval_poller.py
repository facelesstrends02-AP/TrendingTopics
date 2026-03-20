"""
approval_poller.py — Check Gmail for approval replies and route to next agent

Runs every 30 minutes via cron. Checks current pipeline phase:
  - awaiting_idea_approval → parse reply → trigger production_agent
  - awaiting_video_approval → parse reply → trigger publisher_agent
  - anything else → exit immediately (nothing to do)

Usage:
    python3 agents/approval_poller.py
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
AGENTS_DIR = os.path.join(PROJECT_ROOT, "agents")
PYTHON = sys.executable


def run_tool(tool_name, args_list, capture_output=True, check=False):
    """Run a tool script. Returns (returncode, stdout, stderr)."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if check and result.returncode not in (0, 1, 2):
        raise RuntimeError(f"{tool_name} exited with code {result.returncode}: {result.stderr}")
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def run_tool_checked(tool_name, args_list):
    """Run tool, raise on non-zero exit."""
    code, stdout, stderr = run_tool(tool_name, args_list)
    if code != 0:
        raise RuntimeError(f"{tool_name} failed (exit {code}): {stderr}")
    return stdout


def get_state():
    try:
        _, stdout, _ = run_tool("manage_state.py", ["--read"])
        return json.loads(stdout)
    except Exception:
        return {"phase": "idle"}


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def set_phase(phase: str):
    run_tool("manage_state.py", ["--set-phase", phase])


def log_error(msg: str):
    run_tool("manage_state.py", ["--add-error", msg])
    print(f"[ERROR] {msg}", file=sys.stderr)


def send_clarification_email(to, original_subject, mode):
    if mode == "ideas":
        body = (
            "I couldn't quite parse your approval from the last reply.\n\n"
            "Please reply with one of these formats:\n\n"
            "  APPROVE: 1, 3, 7\n"
            "  APPROVE ALL\n"
            "  APPROVE: 1-5\n"
            "  APPROVE ALL EXCEPT: 4, 9\n\n"
            "Use the numbers from the idea list I sent you."
        )
        subject = f"[YT Automation] Clarification needed — idea approval"
    else:
        body = (
            "I couldn't parse your video approval from the last reply.\n\n"
            "Please reply with one of these formats:\n\n"
            "  APPROVE ALL\n"
            "  APPROVE: videoId1, videoId2\n"
            "  REJECT: videoId1\n\n"
            "Use the YouTube video IDs from the links I sent you."
        )
        subject = "[YT Automation] Clarification needed — video approval"

    approval_email = os.getenv("APPROVAL_EMAIL")
    run_tool("send_email.py", [
        "--to", approval_email,
        "--subject", subject,
        "--body", body,
    ])


def handle_idea_approval(state):
    """Poll for idea approval reply, trigger production if approved."""
    message_id = state.get("ideas_email_message_id")
    sent_at = state.get("ideas_email_sent_at", "")

    if not message_id:
        print("[poller] No ideas email message ID found in state.", file=sys.stderr)
        return

    print(f"[poller] Polling for idea approval reply (original: {message_id})...")
    code, reply_body, _ = run_tool("poll_email_replies.py", [
        "--original-message-id", message_id,
        "--since-timestamp", sent_at or "2020-01-01T00:00:00",
    ])

    if code == 1:
        print("[poller] No reply yet.")
        return
    elif code == 2:
        print("[poller] Error polling email.", file=sys.stderr)
        return

    print(f"[poller] Reply received: {reply_body[:100]}...")

    # Parse approval
    parse_code, parse_out, _ = run_tool("parse_approval_email.py", [
        "--mode", "ideas",
        "--email-body", reply_body,
    ])

    if parse_code == 2:
        print("[poller] Ambiguous reply. Sending clarification email.")
        approval_email = os.getenv("APPROVAL_EMAIL")
        send_clarification_email(approval_email, "", "ideas")
        return

    try:
        parsed = json.loads(parse_out)
    except Exception:
        print(f"[poller] Could not parse approval response: {parse_out}", file=sys.stderr)
        return

    approved = parsed.get("approved", [])
    if not approved:
        print("[poller] No ideas approved. Notifying user.")
        approval_email = os.getenv("APPROVAL_EMAIL")
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", "[YT Automation] No ideas approved this week",
            "--body", (
                "No ideas were approved from your reply. "
                "The system will skip production this week.\n\n"
                "A new batch of ideas will be generated next Sunday."
            ),
        ])
        set_phase("completed")
        return

    print(f"[poller] Approved idea IDs: {approved}")
    update_state({
        "approved_idea_ids": approved,
        "idea_approval_received_at": datetime.now(timezone.utc).isoformat(),
    })
    set_phase("production_queued")

    # Trigger production agent
    print("[poller] Launching production_agent...")
    subprocess.Popen([PYTHON, os.path.join(AGENTS_DIR, "production_agent.py")])
    print("[poller] production_agent launched.")


def handle_video_approval(state):
    """Poll for video approval reply, trigger publisher if approved."""
    message_id = state.get("review_email_message_id")
    sent_at = state.get("review_email_sent_at", "")
    videos = state.get("videos", {})

    if not message_id:
        print("[poller] No review email message ID found in state.", file=sys.stderr)
        return

    print(f"[poller] Polling for video approval reply (original: {message_id})...")
    code, reply_body, _ = run_tool("poll_email_replies.py", [
        "--original-message-id", message_id,
        "--since-timestamp", sent_at or "2020-01-01T00:00:00",
    ])

    if code == 1:
        print("[poller] No reply yet.")
        return
    elif code == 2:
        print("[poller] Error polling email.", file=sys.stderr)
        return

    print(f"[poller] Reply received: {reply_body[:100]}...")

    parse_code, parse_out, _ = run_tool("parse_approval_email.py", [
        "--mode", "videos",
        "--email-body", reply_body,
    ])

    if parse_code == 2:
        print("[poller] Ambiguous reply. Sending clarification email.")
        approval_email = os.getenv("APPROVAL_EMAIL")
        send_clarification_email(approval_email, "", "videos")
        return

    try:
        parsed = json.loads(parse_out)
    except Exception:
        print(f"[poller] Could not parse video approval: {parse_out}", file=sys.stderr)
        return

    approved_ids = parsed.get("approved_video_ids", [])
    rejected_ids = parsed.get("rejected_video_ids", [])

    # "__ALL__" means approve everything
    if approved_ids == "__ALL__":
        approved_ids = [v.get("youtube_video_id") for v in videos.values() if v.get("youtube_video_id")]

    # If approved_ids is empty but rejected_ids has some, approve the rest
    if not approved_ids and rejected_ids:
        all_ids = [v.get("youtube_video_id") for v in videos.values() if v.get("youtube_video_id")]
        approved_ids = [vid for vid in all_ids if vid not in rejected_ids]

    print(f"[poller] Approved video IDs: {approved_ids}")
    print(f"[poller] Rejected video IDs: {rejected_ids}")

    if not approved_ids:
        print("[poller] No videos approved.")
        approval_email = os.getenv("APPROVAL_EMAIL")
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", "[YT Automation] No videos approved for publishing",
            "--body", (
                "No videos were approved for publishing. "
                "The produced videos remain unlisted on YouTube.\n\n"
                "A new batch of ideas will be generated next Sunday."
            ),
        ])
        set_phase("completed")
        return

    update_state({
        "approved_video_ids": approved_ids,
        "rejected_video_ids": rejected_ids,
        "video_approval_received_at": datetime.now(timezone.utc).isoformat(),
    })
    set_phase("publishing_queued")

    print("[poller] Launching publisher_agent...")
    subprocess.Popen([PYTHON, os.path.join(AGENTS_DIR, "publisher_agent.py")])
    print("[poller] publisher_agent launched.")


def main():
    state = get_state()
    phase = state.get("phase", "idle")

    print(f"[poller] Current phase: {phase}")

    if phase == "awaiting_idea_approval":
        handle_idea_approval(state)

    elif phase == "awaiting_video_approval":
        handle_video_approval(state)

    elif phase in ("idle", "completed", "production_in_progress",
                   "publishing_in_progress", "production_queued", "publishing_queued"):
        print(f"[poller] Phase '{phase}' — nothing to poll.")

    else:
        print(f"[poller] Unknown phase '{phase}' — skipping.")


if __name__ == "__main__":
    main()
