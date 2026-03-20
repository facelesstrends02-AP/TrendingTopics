"""
video_script_agent.py — On-demand retention-optimized script generator

Run manually to write a script for any topic without going through the full pipeline.
Uses the retention-optimized scriptwriting prompt (pattern interrupts, curiosity loops,
engagement moments, subscriber-driving CTA).

Does NOT interact with state.json.

Orchestrates:
  1. Accept topic or idea reference
  2. Generate retention-optimized script with Claude Sonnet
  3. Save script and print summary
  4. Optionally email the script

Usage:
    python3 agents/video_script_agent.py --topic "5 habits that changed my life"
    python3 agents/video_script_agent.py --idea-id 3 --ideas-file .tmp/ideas.json
    python3 agents/video_script_agent.py --topic "stoic discipline" --email
    python3 agents/video_script_agent.py --topic "morning routine" --dry-run
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


def build_script_email(script, script_path, niche):
    """Build a human-readable email with the full script."""
    title = script.get("title", "")
    thumbnail_text = script.get("thumbnail_text", "")
    total_duration = script.get("total_duration_estimate", 0)
    segments = script.get("segments", [])
    tags = script.get("tags", [])

    total_words = sum(len(s.get("text", "").split()) for s in segments)
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    lines = [
        f"RETENTION-OPTIMIZED SCRIPT",
        f"Title: {title}",
        f"Niche: {niche}",
        f"Generated: {date_str}",
        f"Duration: ~{total_duration}s | Words: {total_words}",
        f"Thumbnail text: {thumbnail_text}",
        "",
        "─" * 52,
        "",
    ]

    for seg in segments:
        seg_type = seg.get("type", "").upper()
        duration = seg.get("duration_estimate", 0)
        text = seg.get("text", "")
        overlay = seg.get("overlay_text", "")
        visual = seg.get("visual_cue", "")

        lines.append(f"[{seg_type}] (~{duration}s)")
        lines.append(f"  SPOKEN: {text}")
        if overlay:
            lines.append(f"  ON-SCREEN: {overlay}")
        if visual:
            lines.append(f"  FOOTAGE: {visual}")
        lines.append("")

    lines += [
        "─" * 52,
        f"Tags: {', '.join(tags[:10])}",
        "",
        f"Script file: {script_path}",
        "",
        "To produce this video, move the script to .tmp/scripts/video_N_script.json",
        "and run: python3 agents/production_agent.py",
        "",
        "—YouTube Automation",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="On-demand retention-optimized script writer")
    parser.add_argument("--topic", default="", help="Video topic")
    parser.add_argument("--idea-id", type=int, default=None, help="Idea ID from ideas file")
    parser.add_argument("--ideas-file", default="", help="Path to ideas.json")
    parser.add_argument("--output", default="", help="Output script path")
    parser.add_argument("--email", action="store_true", help="Send script via email")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without running Claude")
    args = parser.parse_args()

    if not args.topic and args.idea_id is None:
        print("ERROR: Provide either --topic or --idea-id + --ideas-file", file=sys.stderr)
        sys.exit(1)

    niche = os.getenv("NICHE", "Self Development")
    approval_email = os.getenv("APPROVAL_EMAIL")

    topic_label = args.topic or f"idea-{args.idea_id}"

    # Determine output path
    safe_label = topic_label.replace(" ", "_")[:40]
    output_path = args.output or os.path.join(TMP_DIR, "scripts", f"standalone_{safe_label}_script.json")

    print(f"[video_script_agent] Writing retention script for: '{topic_label}'")

    if args.dry_run:
        print(f"[DRY RUN] Would generate retention script for: '{topic_label}'")
        print(f"  Output: {output_path}")
        print(f"  Tool: generate_retention_script.py")
        print(f"  Model: claude-sonnet-4-6")
        print("[video_script_agent] Dry run complete.")
        return

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # ── Step 1: Generate Script ───────────────────────────────────────────────
    print("[1/2] Generating retention-optimized script with Claude Sonnet...")

    generate_args = ["--output", output_path]
    if args.topic:
        generate_args += ["--topic", args.topic]
    elif args.idea_id is not None:
        generate_args += ["--idea-id", str(args.idea_id)]
        if args.ideas_file:
            generate_args += ["--ideas-file", args.ideas_file]

    try:
        run_tool("generate_retention_script.py", generate_args)
        with open(output_path) as f:
            script = json.load(f)
    except Exception as e:
        print(f"ERROR: Script generation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Print summary
    title = script.get("title", "")
    segments = script.get("segments", [])
    total_words = sum(len(s.get("text", "").split()) for s in segments)
    duration = script.get("total_duration_estimate", 0)
    seg_types = [s.get("type", "") for s in segments]

    print(f"\n  Title:    {title}")
    print(f"  Duration: ~{duration}s ({total_words} words)")
    print(f"  Segments: {' → '.join(seg_types)}")
    print(f"  File:     {output_path}")

    # ── Step 2: Email Script (optional) ──────────────────────────────────────
    if args.email:
        if not approval_email:
            print("WARNING: APPROVAL_EMAIL not set — cannot send email.", file=sys.stderr)
        else:
            print("[2/2] Sending script via email...")
            email_body = build_script_email(script, output_path, niche)
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            subject = f"[YT Automation] Retention Script Ready — \"{title}\" ({date_str})"
            try:
                run_tool("send_email.py", [
                    "--to", approval_email,
                    "--subject", subject,
                    "--body", email_body,
                ])
                print(f"  → Script emailed to {approval_email}")
            except Exception as e:
                print(f"  WARNING: Could not send email: {e}", file=sys.stderr)

    print(f"\n[video_script_agent] Done. Script → {output_path}")


if __name__ == "__main__":
    main()
