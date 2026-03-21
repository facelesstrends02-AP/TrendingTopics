"""
reddit_agent.py — Post a published video to Reddit across relevant subreddits

Reads video metadata from state.json, looks up the original idea for category/hook/angle,
maps the category to target subreddits, posts to each, updates state, and emails results.

Usage:
    python3 agents/reddit_agent.py --video-key video_1
    python3 agents/reddit_agent.py --video-key video_1 --dry-run

State reads:  state.videos.{video_key}.youtube_url / title / idea_id
State writes: state.videos.{video_key}.reddit_posts  (list of {subreddit, url, status})
              state.videos.{video_key}.reddit_posted  (bool)
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

# Maps idea category → list of subreddits (most permissive/relevant first)
CATEGORY_SUBREDDITS = {
    "politics":      ["geopolitics", "worldpolitics"],
    "world":         ["geopolitics", "worldpolitics"],
    "finance":       ["Economics", "personalfinance"],
    "tech":          ["technology", "Futurology"],
    "science":       ["Futurology", "health"],
    "entertainment": ["OutOfTheLoop", "todayilearned"],
    "sports":        ["sports", "worldnews"],
}
DEFAULT_SUBREDDITS = ["geopolitics", "worldpolitics"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_tool(tool_name, args_list):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def run_tool_nonfatal(tool_name, args_list):
    """Run a tool, return (success, stdout, stderr) without raising."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stdout.strip(), result.stderr.strip()


def get_state():
    raw = run_tool("manage_state.py", ["--read"])
    return json.loads(raw)


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def load_ideas():
    ideas_path = os.path.join(TMP_DIR, "ideas.json")
    if not os.path.exists(ideas_path):
        return []
    with open(ideas_path) as f:
        return json.load(f)


def build_post_body(idea: dict) -> str:
    """Build the self-text body from idea hook + angle (no extra API call needed)."""
    parts = []
    hook = idea.get("hook", "").strip()
    angle = idea.get("angle", "").strip()
    if hook:
        parts.append(hook)
    if angle:
        parts.append(angle)
    return "\n\n".join(parts)


def send_email(subject: str, body: str):
    approval_email = os.getenv("APPROVAL_EMAIL", "")
    if not approval_email:
        print("  WARNING: APPROVAL_EMAIL not set, skipping email.", file=sys.stderr)
        return
    run_tool_nonfatal("send_email.py", [
        "--to", approval_email,
        "--subject", subject,
        "--body", body,
    ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Post a published video to Reddit")
    parser.add_argument("--video-key", required=True, help="e.g. video_1")
    parser.add_argument("--dry-run", action="store_true", help="Print without posting")
    args = parser.parse_args()

    print(f"[reddit_agent] Starting for {args.video_key}", file=sys.stderr)

    # --- Read state ---
    try:
        state = get_state()
    except Exception as e:
        print(f"ERROR: Could not read state: {e}", file=sys.stderr)
        sys.exit(1)

    video_data = state.get("videos", {}).get(args.video_key)
    if not video_data:
        print(f"ERROR: {args.video_key} not found in state.", file=sys.stderr)
        sys.exit(1)

    youtube_url = video_data.get("youtube_url") or video_data.get("public_url", "")
    title = video_data.get("title", "")
    idea_id = video_data.get("idea_id")

    if not youtube_url:
        print(f"ERROR: No YouTube URL found for {args.video_key}.", file=sys.stderr)
        sys.exit(1)

    if not title:
        print(f"ERROR: No title found for {args.video_key}.", file=sys.stderr)
        sys.exit(1)

    # --- Load idea metadata for category + post body ---
    ideas = load_ideas()
    idea = next((i for i in ideas if i.get("id") == idea_id), None)

    if idea:
        category = idea.get("category", "world").lower()
        post_body = build_post_body(idea)
        print(f"  Category: {category}", file=sys.stderr)
    else:
        print(f"  WARNING: idea_id {idea_id} not found in ideas.json, using defaults.", file=sys.stderr)
        category = "world"
        post_body = ""

    subreddits = CATEGORY_SUBREDDITS.get(category, DEFAULT_SUBREDDITS)
    print(f"  Subreddits: {subreddits}", file=sys.stderr)
    print(f"  Title: {title}", file=sys.stderr)
    print(f"  URL: {youtube_url}", file=sys.stderr)

    # --- Post to each subreddit ---
    results = []
    for subreddit in subreddits:
        print(f"\n  Posting to r/{subreddit}...", file=sys.stderr)
        tool_args = [
            "--title", title,
            "--url", youtube_url,
            "--subreddit", subreddit,
            "--body", post_body,
        ]
        if args.dry_run:
            tool_args.append("--dry-run")

        success, stdout, stderr = run_tool_nonfatal("post_to_reddit.py", tool_args)
        if stderr:
            print(f"  {stderr}", file=sys.stderr)

        if stdout and "reddit.com" in stdout:
            results.append({"subreddit": subreddit, "url": stdout, "status": "posted"})
            print(f"  ✓ r/{subreddit}: {stdout}", file=sys.stderr)
        else:
            results.append({"subreddit": subreddit, "url": "", "status": "skipped"})
            print(f"  ✗ r/{subreddit}: skipped or failed", file=sys.stderr)

    # --- Update state ---
    if not args.dry_run:
        try:
            update_state({
                "videos": {
                    args.video_key: {
                        "reddit_posts": results,
                        "reddit_posted": True,
                        "reddit_posted_at": datetime.now(timezone.utc).isoformat(),
                    }
                }
            })
            print("\n  State updated with Reddit post results.", file=sys.stderr)
        except Exception as e:
            print(f"  WARNING: Could not update state: {e}", file=sys.stderr)

    # --- Send completion email ---
    posted = [r for r in results if r["status"] == "posted"]
    skipped = [r for r in results if r["status"] == "skipped"]

    subject = f"Reddit posts live — {title[:60]}"
    lines = [f"Reddit distribution complete for:\n{title}\n{youtube_url}\n"]

    if posted:
        lines.append("Posted successfully:")
        for r in posted:
            lines.append(f"  • r/{r['subreddit']}: {r['url']}")
    if skipped:
        lines.append("\nSkipped (restricted or failed):")
        for r in skipped:
            lines.append(f"  • r/{r['subreddit']}")

    lines.append("\n— Trends Daily Pipeline")
    email_body = "\n".join(lines)

    if not args.dry_run:
        send_email(subject, email_body)
        print("\n  Completion email sent.", file=sys.stderr)
    else:
        print(f"\n[DRY RUN] Email would be sent:\n{email_body}", file=sys.stderr)

    print(f"\n[reddit_agent] Done. Posted to {len(posted)}/{len(results)} subreddits.")


if __name__ == "__main__":
    main()
