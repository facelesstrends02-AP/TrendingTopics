"""
seo_agent.py — YouTube SEO optimizer for published or unlisted videos

Run manually to optimize any existing video's metadata on YouTube.
Also called automatically by production_agent.py as Step H.

Acts as a YouTube SEO specialist: produces an SEO-optimized title, description (150-250 words),
semantic keywords, chapter markers, and related video topics, then applies them to the video via API.

Does NOT interact with state.json.

Orchestrates:
  1. Generate SEO metadata with Claude Sonnet
  2. Update YouTube video metadata via API
  3. Save SEO JSON to .tmp/seo/
  4. Send email summary of changes

Usage:
    python3 agents/seo_agent.py --video-id abc123xyz --script-file .tmp/scripts/video_1_script.json
    python3 agents/seo_agent.py --video-id abc123xyz --niche "Finance"
    python3 agents/seo_agent.py --script-file .tmp/scripts/video_1_script.json --dry-run
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


def build_seo_email(seo_data, video_id, script_path):
    """Build a human-readable email summarizing SEO changes."""
    original_title = seo_data.get("original_title", "")
    seo_title = seo_data.get("seo_title", "")
    description = seo_data.get("description", "")
    keywords = seo_data.get("semantic_keywords", [])
    search_phrases = seo_data.get("search_phrases", [])
    chapters = seo_data.get("chapter_markers", [])
    related = seo_data.get("related_video_topics", [])
    updated = seo_data.get("updated_youtube", False)
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    lines = [
        f"SEO OPTIMIZATION COMPLETE",
        f"Date: {date_str}",
        f"Video ID: {video_id or 'N/A'}",
        f"Status: {'✓ YouTube metadata updated' if updated else '⚠ Metadata NOT applied to YouTube (no --video-id or update failed)'}",
        "",
        "─" * 52,
        "",
        "TITLE CHANGE:",
        f"  Before: {original_title}",
        f"  After:  {seo_title}",
        "",
        "SEO DESCRIPTION (first 300 chars):",
        f"  {description[:300]}...",
        "",
        "CHAPTER MARKERS:",
    ]
    for ch in chapters:
        lines.append(f"  {ch.get('timestamp', '')} — {ch.get('title', '')}")

    lines += [
        "",
        "TARGET SEARCH PHRASES:",
    ]
    for phrase in search_phrases:
        lines.append(f"  • {phrase}")

    lines += [
        "",
        "SEMANTIC KEYWORDS:",
        f"  {', '.join(keywords[:8])}",
        "",
        "RELATED VIDEO TOPICS (internal linking opportunities):",
    ]
    for topic in related:
        lines.append(f"  → {topic}")

    lines += [
        "",
        "─" * 52,
        f"Full SEO data saved locally.",
        "",
        "—YouTube Automation",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="YouTube SEO optimizer")
    parser.add_argument("--video-id", default="", help="YouTube video ID to update")
    parser.add_argument("--script-file", default="", help="Path to video script JSON")
    parser.add_argument("--niche", default=None, help="Override NICHE from .env")
    parser.add_argument("--output", default="", help="Output SEO JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Generate SEO data but do NOT update YouTube")
    args = parser.parse_args()

    niche = args.niche or os.getenv("NICHE", "Self Development")
    approval_email = os.getenv("APPROVAL_EMAIL")

    if not args.script_file:
        print("ERROR: --script-file is required", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.script_file):
        print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
        sys.exit(1)

    video_key = os.path.basename(args.script_file).replace("_script.json", "").replace("_retention_script.json", "")
    output_path = args.output or os.path.join(TMP_DIR, "seo", f"{video_key}_seo.json")

    print(f"[seo_agent] Optimizing SEO for: {args.script_file}")
    if args.video_id:
        print(f"  YouTube video ID: {args.video_id}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # ── Step 1: Generate SEO Metadata ────────────────────────────────────────
    print("[1/3] Generating SEO metadata with Claude Sonnet...")

    seo_args = [
        "--script-file", args.script_file,
        "--niche", niche,
        "--output", output_path,
    ]

    if args.video_id and not args.dry_run:
        seo_args += ["--video-id", args.video_id, "--update-youtube"]
    elif args.dry_run and args.video_id:
        print("  [DRY RUN] YouTube update skipped.")

    try:
        run_tool("generate_seo_metadata.py", seo_args)
        with open(output_path) as f:
            seo_data = json.load(f)
        print(f"  → SEO data: {output_path}")
    except Exception as e:
        print(f"ERROR: SEO generation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Print summary
    print(f"\n  Original title: {seo_data.get('original_title', '')}")
    print(f"  SEO title:      {seo_data.get('seo_title', '')}")
    print(f"  YouTube updated: {seo_data.get('updated_youtube', False)}")
    chapters = seo_data.get("chapter_markers", [])
    if chapters:
        print(f"  Chapters: {len(chapters)} markers")

    # ── Step 2: Send Email ────────────────────────────────────────────────────
    if not approval_email:
        print("\n[2/3] Skipping email (APPROVAL_EMAIL not set).", file=sys.stderr)
    else:
        print("\n[2/3] Sending SEO summary email...")
        email_body = build_seo_email(seo_data, args.video_id, args.script_file)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        title_short = seo_data.get("seo_title", "video")[:50]
        subject = f"[YT Automation] SEO Applied — \"{title_short}\" ({date_str})"

        if args.dry_run:
            print("[DRY RUN] Email that would be sent:")
            print(f"  To: {approval_email}")
            print(f"  Subject: {subject}")
            print(f"  Body:\n{email_body}")
        else:
            try:
                run_tool("send_email.py", [
                    "--to", approval_email,
                    "--subject", subject,
                    "--body", email_body,
                ])
                print(f"  → Email sent to {approval_email}")
            except Exception as e:
                print(f"  WARNING: Could not send email: {e}", file=sys.stderr)

    print(f"\n[seo_agent] Done. SEO data → {output_path}")


if __name__ == "__main__":
    main()
