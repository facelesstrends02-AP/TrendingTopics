"""
title_thumbnail_agent.py — Generate multiple title/thumbnail variants for a video topic

Run manually to explore CTR-optimized title and thumbnail options before committing to production.
For each variant, generates a thumbnail preview using the concept's Pexels query.

Does NOT interact with state.json.

Orchestrates:
  1. Generate 3-5 title + thumbnail concept variants with Claude Sonnet
  2. Render thumbnail preview image for each variant (Pexels + Pillow)
  3. Save variants JSON + preview images to .tmp/title_variants/
  4. Send email with all variants for review

Usage:
    python3 agents/title_thumbnail_agent.py --topic "how to wake up at 5am"
    python3 agents/title_thumbnail_agent.py --idea-id 3 --ideas-file .tmp/ideas.json
    python3 agents/title_thumbnail_agent.py --topic "discipline" --count 3
    python3 agents/title_thumbnail_agent.py --dry-run --topic "morning habits"
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
VARIANTS_DIR = os.path.join(TMP_DIR, "title_variants")
PYTHON = sys.executable


def run_tool(tool_name, args_list, capture_output=True):
    """Run a tool script and return stdout. Raises on non-zero exit."""
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"{tool_name} failed (exit {result.returncode}): {stderr}")
    return result.stdout.strip() if result.stdout else ""


def build_email(variants_data, thumbnail_paths, niche):
    """Build a human-readable email listing all title/thumbnail variants."""
    topic = variants_data.get("topic", "")
    recommended_id = variants_data.get("recommended_variant_id", 1)
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    lines = [
        f"Title & Thumbnail Variants for '{topic}'",
        f"Niche: {niche} — Generated: {date_str}",
        "",
        f"Recommended: Variant {recommended_id}",
        "",
        "─" * 52,
        "",
    ]

    for variant in variants_data.get("variants", []):
        vid = variant.get("variant_id", "?")
        is_recommended = " ← RECOMMENDED" if vid == recommended_id else ""
        trigger = variant.get("psychological_trigger", "")
        why = variant.get("why_it_works", "")
        thumb = variant.get("thumbnail", {})
        thumb_path = thumbnail_paths.get(vid, "")

        lines.append(f"VARIANT {vid}{is_recommended}")
        lines.append(f"  Title:    \"{variant.get('title', '')}\"")
        lines.append(f"  Trigger:  {trigger}")
        lines.append(f"  Why:      {why}")
        lines.append(f"  Thumbnail concept:")
        lines.append(f"    Visual:  {thumb.get('main_visual', '')}")
        lines.append(f"    Text:    {thumb.get('text_overlay', '')}")
        lines.append(f"    Emotion: {thumb.get('emotion', '')}")
        lines.append(f"    Colors:  {thumb.get('color_strategy', '')}")
        if thumb_path:
            lines.append(f"    Preview: {thumb_path}")
        lines.append("")

    lines += [
        "─" * 52,
        "To use a variant, copy its title into your production workflow.",
        "Thumbnail previews saved to .tmp/title_variants/",
        "",
        "—YouTube Automation",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate title/thumbnail variants")
    parser.add_argument("--topic", default="", help="Video topic")
    parser.add_argument("--idea-id", type=int, default=None, help="Idea ID from ideas file")
    parser.add_argument("--ideas-file", default="", help="Path to ideas.json")
    parser.add_argument("--niche", default=None, help="Override NICHE from .env")
    parser.add_argument("--count", type=int, default=5, help="Number of variants to generate")
    parser.add_argument("--dry-run", action="store_true", help="Print email without sending")
    args = parser.parse_args()

    niche = args.niche or os.getenv("NICHE", "Self Development")
    approval_email = os.getenv("APPROVAL_EMAIL")
    if not approval_email:
        print("ERROR: APPROVAL_EMAIL not set in .env", file=sys.stderr)
        sys.exit(1)

    if not args.topic and args.idea_id is None:
        print("ERROR: Provide either --topic or --idea-id + --ideas-file", file=sys.stderr)
        sys.exit(1)

    topic_label = args.topic or f"idea-{args.idea_id}"
    print(f"[title_thumbnail_agent] Generating {args.count} variants for '{topic_label}'")

    os.makedirs(VARIANTS_DIR, exist_ok=True)

    # ── Step 1: Generate Variants ─────────────────────────────────────────────
    print("[1/3] Generating title/thumbnail variants with Claude Sonnet...")
    safe_label = topic_label.replace(" ", "_")[:40]
    variants_path = os.path.join(VARIANTS_DIR, f"{safe_label}_variants.json")

    generate_args = ["--niche", niche, "--count", str(args.count), "--output", variants_path]
    if args.topic:
        generate_args += ["--topic", args.topic]
    elif args.idea_id is not None:
        generate_args += ["--idea-id", str(args.idea_id)]
        if args.ideas_file:
            generate_args += ["--ideas-file", args.ideas_file]

    try:
        run_tool("generate_titles_thumbnails.py", generate_args)
        with open(variants_path) as f:
            variants_data = json.load(f)
        print(f"  → {len(variants_data.get('variants', []))} variants generated: {variants_path}")
    except Exception as e:
        print(f"ERROR: Variant generation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Step 2: Render Thumbnail Preview for Each Variant ────────────────────
    print("[2/3] Rendering thumbnail previews...")
    thumbnail_paths = {}

    for variant in variants_data.get("variants", []):
        vid = variant.get("variant_id")
        thumb = variant.get("thumbnail", {})
        title_text = variant.get("title", "")
        text_overlay = thumb.get("text_overlay", "")
        search_query = thumb.get("pexels_search_query", "")
        sub_text = thumb.get("emotion", "")

        # Use text_overlay as thumbnail text (max 4 words from the concept)
        thumb_text = text_overlay or title_text[:30]
        output_file = os.path.join(VARIANTS_DIR, f"variant_{vid}_thumbnail.jpg")

        try:
            run_tool("generate_thumbnail.py", [
                "--thumbnail-text", thumb_text,
                "--search-query", search_query,
                "--sub-text", sub_text,
                "--output-file", output_file,
            ])
            thumbnail_paths[vid] = output_file
            print(f"  → Variant {vid} thumbnail: {output_file}")
        except Exception as e:
            print(f"  WARNING: Thumbnail for variant {vid} failed (non-fatal): {e}", file=sys.stderr)

    # ── Step 3: Send Email ────────────────────────────────────────────────────
    print("[3/3] Composing email...")
    email_body = build_email(variants_data, thumbnail_paths, niche)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"[YT Automation] {args.count} Title/Thumbnail Variants — {topic_label} ({date_str})"

    if args.dry_run:
        print("[DRY RUN] Email that would be sent:")
        print(f"  To: {approval_email}")
        print(f"  Subject: {subject}")
        print(f"  Body:\n{email_body}")
        print("[title_thumbnail_agent] Dry run complete.")
        return

    try:
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", subject,
            "--body", email_body,
        ])
        print(f"  → Email sent to {approval_email}")
    except Exception as e:
        print(f"  WARNING: Could not send email: {e}", file=sys.stderr)
        print(f"  Variants saved at: {variants_path}")

    print(f"[title_thumbnail_agent] Done. Variants → {variants_path}")
    print(f"  Thumbnails → {VARIANTS_DIR}")


if __name__ == "__main__":
    main()
