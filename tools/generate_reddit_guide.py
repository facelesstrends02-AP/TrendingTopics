"""
generate_reddit_guide.py — Generate a manual Reddit posting guide for a video

Reads video metadata from state.json + ideas.json and produces a copy-paste-ready
markdown guide. Generates 2 posts per video (optimal for a new account posting 3
videos/week = 6 posts/week). Each post card includes customised title, body, exact
IST posting time, and direct submit URL — nothing left to figure out.

Usage:
    python3 tools/generate_reddit_guide.py --video-key video_1

Output:
    .tmp/reddit/video_1_reddit_guide.md   (always written)
    Email to APPROVAL_EMAIL               (if configured)
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
TMP_DIR = os.path.join(PROJECT_ROOT, ".tmp")
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
PYTHON = sys.executable

# Per-subreddit metadata: post type, title transform hint, body style, notes
SUBREDDIT_INFO = {
    "geopolitics": {
        "type": "link",
        "title_style": "verbatim",  # use video title as-is
        "body_style": "comment",    # paste body as first comment after posting
        "flair": None,
        "notes": "No flair required. Karma threshold ~10. Link posts preferred.",
    },
    "worldpolitics": {
        "type": "link",
        "title_style": "verbatim",
        "body_style": "comment",
        "flair": None,
        "notes": "Very active, posts move fast. Low karma requirement. No flair.",
    },
    "Economics": {
        "type": "link",
        "title_style": "verbatim",
        "body_style": "comment",
        "flair": "Video",           # select this flair when posting
        "notes": "Select flair 'Video' or 'News' when posting.",
    },
    "personalfinance": {
        "type": "self",
        "title_style": "personal",  # reframe title to personal impact angle
        "body_style": "full",       # paste full body + YouTube link in text box
        "flair": "Discussion",
        "notes": "Text posts perform better here. Lead with personal impact. Select flair 'Discussion'.",
    },
    "technology": {
        "type": "link",
        "title_style": "verbatim",
        "body_style": "comment",
        "flair": None,
        "notes": "High traffic. Title must be descriptive, not clickbait. No flair needed.",
    },
    "Futurology": {
        "type": "link",
        "title_style": "future",    # reframe to focus on future implication
        "body_style": "comment",
        "flair": "Discussion",
        "notes": "Select flair 'Discussion' or 'Video'. Future-implication framing works best.",
    },
    "health": {
        "type": "link",
        "title_style": "verbatim",
        "body_style": "comment",
        "flair": None,
        "notes": "Avoid sensational health claims. Add source citation as first comment.",
    },
    "OutOfTheLoop": {
        "type": "self",
        "title_style": "question",  # must be "What is X and why does it matter?"
        "body_style": "full",
        "flair": None,
        "notes": "Title MUST be a 'What is X?' question. Self post required. Include YouTube link in body.",
    },
    "todayilearned": {
        "type": "self",
        "title_style": "til",       # must start with "TIL"
        "body_style": "full",
        "flair": None,
        "notes": "Title MUST start with 'TIL'. Self post. Keep it to one sentence of fact.",
    },
    "sports": {
        "type": "link",
        "title_style": "verbatim",
        "body_style": "comment",
        "flair": "General",
        "notes": "Select the relevant sport flair or 'General'.",
    },
    "worldnews": {
        "type": "link",
        "title_style": "verbatim",
        "body_style": "comment",
        "flair": None,
        "notes": "WARNING: r/worldnews only allows links to news articles, not YouTube. Skip unless you have a news source link.",
    },
}

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

# Post 1 time and Post 2 time (30 min later) by day of week
# Videos publish Mon/Wed/Fri at 7:30 AM IST; post 30 min after.
# Other days: weekend/off-day peak times.
POST_TIMES_IST = {
    "Monday":    ("8:00 AM", "8:30 AM"),
    "Tuesday":   ("8:00 AM", "8:30 AM"),
    "Wednesday": ("8:00 AM", "8:30 AM"),
    "Thursday":  ("8:00 AM", "8:30 AM"),
    "Friday":    ("8:00 AM", "8:30 AM"),
    "Saturday":  ("11:00 AM", "11:30 AM"),
    "Sunday":    ("11:00 AM", "11:30 AM"),
}


def customise_title(title: str, hook: str, angle: str, style: str) -> str:
    """Return a subreddit-appropriate post title given the style hint."""
    if style == "verbatim":
        return title
    if style == "personal":
        # Strip channel-style formatting, keep the personal consequence part
        # Titles often follow pattern "X Happened — Here's What It Means For You"
        # Just return as-is; the personal framing is already in the title
        return title
    if style == "future":
        # Prepend future framing if not already there
        if not any(w in title.lower() for w in ["will", "future", "next", "soon", "could", "might"]):
            return f"[Video] {title}"
        return title
    if style == "question":
        # OutOfTheLoop: "What is [topic] and why does it matter?"
        # Extract core topic from title - take first clause before dash/comma
        core = title.split("—")[0].split(",")[0].strip().rstrip(".")
        return f"What is happening with {core}, and why does it matter?"
    if style == "til":
        # todayilearned: "TIL that [one-sentence fact from hook]"
        fact = hook.split(".")[0].strip() if hook else title
        if len(fact) > 200:
            fact = fact[:197] + "..."
        return f"TIL that {fact}"
    return title


def build_post_body(title: str, hook: str, angle: str, youtube_url: str, style: str) -> str:
    """Build the text body appropriate for the post style."""
    if style == "comment":
        # For link posts: this goes as a first comment, not in the post itself
        parts = []
        if hook:
            parts.append(hook)
        if angle:
            parts.append(angle)
        parts.append(f"▶ Full breakdown: {youtube_url}")
        return "\n\n".join(parts)
    if style == "full":
        # For self posts: this is the entire post body
        parts = []
        if hook:
            parts.append(hook)
        if angle:
            parts.append(angle)
        parts.append(f"▶ Watch: {youtube_url}")
        return "\n\n".join(parts)
    # fallback
    return f"{hook}\n\n{youtube_url}" if hook else youtube_url


def build_post_card(n: int, sub: str, post_time: str, title: str, hook: str,
                    angle: str, youtube_url: str, today_name: str) -> list:
    """Return lines for a single numbered post card."""
    info = SUBREDDIT_INFO.get(sub, {"type": "link", "title_style": "verbatim",
                                    "body_style": "comment", "flair": None, "notes": ""})
    post_type = info["type"]
    title_style = info["title_style"]
    body_style = info["body_style"]
    flair = info.get("flair")
    notes = info["notes"]

    post_title = customise_title(title, hook, angle, title_style)
    post_body = build_post_body(title, hook, angle, youtube_url, body_style)

    submit_url = f"https://www.reddit.com/r/{sub}/submit?type={'link' if post_type == 'link' else 'self'}"

    lines = [
        f"## Post {n} — r/{sub}",
        f"",
        f"**Post at:** {post_time} IST ({today_name})",
        f"**Type:** {'Link post' if post_type == 'link' else 'Text (self) post'}",
    ]
    if flair:
        lines.append(f"**Flair:** Select \"{flair}\" after posting")
    lines += [
        f"**Submit URL:** {submit_url}",
        f"",
        f"### Title",
        f"",
        f"```",
        post_title,
        f"```",
        f"",
    ]

    if post_type == "link":
        lines += [
            f"### URL to paste",
            f"",
            f"```",
            youtube_url or "(YouTube URL not yet available)",
            f"```",
            f"",
            f"### First Comment (paste this immediately after posting)",
            f"",
            f"```",
            post_body,
            f"```",
        ]
    else:
        lines += [
            f"### Body text",
            f"",
            f"```",
            post_body,
            f"```",
        ]

    lines += [
        f"",
        f"> **Notes:** {notes}",
        f"",
        f"---",
        f"",
    ]
    return lines


def build_guide(video_key: str, video_data: dict, idea: dict) -> str:
    title = video_data.get("title", "(no title)")
    youtube_url = video_data.get("youtube_url") or video_data.get("public_url", "")
    category = (idea.get("category", "world") if idea else "world").lower()
    hook = (idea.get("hook", "") if idea else "").strip()
    angle = (idea.get("angle", "") if idea else "").strip()

    subreddits = CATEGORY_SUBREDDITS.get(category, DEFAULT_SUBREDDITS)
    # Always exactly 2 posts
    sub1, sub2 = subreddits[0], subreddits[1] if len(subreddits) > 1 else subreddits[0]

    today_name = datetime.now().strftime("%A")
    time1, time2 = POST_TIMES_IST.get(today_name, ("8:00 AM", "8:30 AM"))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    info1 = SUBREDDIT_INFO.get(sub1, {})
    info2 = SUBREDDIT_INFO.get(sub2, {})

    lines = [
        f"# Reddit Posting Guide — {title[:70]}",
        f"",
        f"_Generated: {generated_at}_",
        f"",
        f"**2 posts total. Space them 30 minutes apart.**",
        f"",
        f"| Post | Subreddit | Type | Time (IST) |",
        f"|------|-----------|------|------------|",
        f"| Post 1 | r/{sub1} | {'Link' if info1.get('type') == 'link' else 'Text'} | {time1} |",
        f"| Post 2 | r/{sub2} | {'Link' if info2.get('type') == 'link' else 'Text'} | {time2} |",
        f"",
        f"**Video:** {youtube_url or '(not yet published)'}",
        f"",
        f"---",
        f"",
    ]

    lines += build_post_card(1, sub1, time1, title, hook, angle, youtube_url, today_name)
    lines += build_post_card(2, sub2, time2, title, hook, angle, youtube_url, today_name)

    lines += [
        f"## General Tips",
        f"",
        f"- **New account**: Some large subreddits block accounts under 30 days old or < 10 karma. If rejected, try commenting on a few popular posts first to build karma.",
        f"- **Never post the same URL twice** to the same subreddit — instant shadow-ban risk.",
        f"- **If a post gets removed**: check the sub's rules, switch to a text post instead, or try a different subreddit.",
        f"- **Best engagement signal**: reply to every comment in the first hour — Reddit's algorithm rewards early engagement.",
        f"",
    ]

    return "\n".join(lines)


def get_state():
    result = subprocess.run(
        [PYTHON, os.path.join(TOOLS_DIR, "manage_state.py"), "--read"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"manage_state.py failed: {result.stderr.strip()}")
    return json.loads(result.stdout.strip())


def load_ideas():
    path = os.path.join(TMP_DIR, "ideas.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def save_guide(video_key: str, guide_text: str) -> str:
    reddit_dir = os.path.join(TMP_DIR, "reddit")
    os.makedirs(reddit_dir, exist_ok=True)
    out_path = os.path.join(reddit_dir, f"{video_key}_reddit_guide.md")
    with open(out_path, "w") as f:
        f.write(guide_text)
    return out_path


def send_email(subject: str, body: str):
    approval_email = os.getenv("APPROVAL_EMAIL", "")
    if not approval_email:
        print("  WARNING: APPROVAL_EMAIL not set, skipping email.", file=sys.stderr)
        return
    result = subprocess.run(
        [PYTHON, os.path.join(TOOLS_DIR, "send_email.py"),
         "--to", approval_email,
         "--subject", subject,
         "--body", body],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  WARNING: Email failed: {result.stderr.strip()}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Generate a manual Reddit posting guide for a video")
    parser.add_argument("--video-key", required=True, help="e.g. video_1")
    args = parser.parse_args()

    print(f"[reddit_guide] Generating guide for {args.video_key}", file=sys.stderr)

    try:
        state = get_state()
    except Exception as e:
        print(f"ERROR: Could not read state: {e}", file=sys.stderr)
        sys.exit(1)

    video_data = state.get("videos", {}).get(args.video_key)
    if not video_data:
        print(f"ERROR: {args.video_key} not found in state.", file=sys.stderr)
        sys.exit(1)

    idea_id = video_data.get("idea_id")
    ideas = load_ideas()
    idea = next((i for i in ideas if i.get("id") == idea_id), None)
    if not idea:
        print(f"  WARNING: idea_id {idea_id} not found in ideas.json, using defaults.", file=sys.stderr)

    guide_text = build_guide(args.video_key, video_data, idea)

    out_path = save_guide(args.video_key, guide_text)
    print(f"  Guide saved → {out_path}", file=sys.stderr)

    print(f"\n[reddit_guide] Done. Guide at: {out_path}")


if __name__ == "__main__":
    main()
