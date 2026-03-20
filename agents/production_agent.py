"""
production_agent.py — Content production pipeline for approved ideas

Triggered by approval_poller.py when ideas are approved. For each approved idea:
  1. Generate script (Claude Sonnet)
  2. Generate voiceover (OpenAI TTS)
  3. Fetch Pexels stock footage
  4. Assemble video (moviepy)
  5. Upload to YouTube as unlisted
  6. Generate and upload thumbnail (Pexels photo + Pillow composite)
  7. Email user with unlisted review links

Usage:
    python3 agents/production_agent.py
"""

import json
import os
import shutil
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


def update_state(data: dict):
    run_tool("manage_state.py", ["--write", json.dumps(data)])


def set_phase(phase: str):
    run_tool("manage_state.py", ["--set-phase", phase])


def log_error(msg: str):
    run_tool("manage_state.py", ["--add-error", msg])
    print(f"[ERROR] {msg}", file=sys.stderr)


def get_state():
    _, stdout, _ = _run_raw("manage_state.py", ["--read"])
    return json.loads(stdout)


def _run_raw(tool_name, args_list):
    cmd = [PYTHON, os.path.join(TOOLS_DIR, tool_name)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def produce_video(idea_id, video_key, ideas_path):
    """
    Run the full production pipeline for a single idea.
    Returns dict with video metadata, or None on failure.
    """
    script_path = os.path.join(TMP_DIR, "scripts", f"{video_key}_script.json")
    audio_path = os.path.join(TMP_DIR, "audio", f"{video_key}_voiceover.mp3")
    captions_path = os.path.join(TMP_DIR, "captions", f"{video_key}_captions.json")
    footage_dir = os.path.join(TMP_DIR, "footage", video_key)
    output_path = os.path.join(TMP_DIR, "output", f"{video_key}_final.mp4")

    os.makedirs(os.path.dirname(script_path), exist_ok=True)
    os.makedirs(os.path.dirname(audio_path), exist_ok=True)
    os.makedirs(os.path.dirname(captions_path), exist_ok=True)
    os.makedirs(footage_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # ── Step A: Generate Script ─────────────────────────────────────────────
    script_valid = False
    if os.path.exists(script_path):
        try:
            with open(script_path) as _f:
                _cached = json.load(_f)
            if _cached.get("idea_id") == idea_id:
                script_valid = True
                print(f"  [A] Script already exists for idea {idea_id}, skipping generation.")
            else:
                print(f"  [A] Cached script is for a different idea, regenerating.")
                os.remove(script_path)
        except Exception:
            pass

    if not script_valid:
        print(f"  [A] Generating retention-optimized script for idea {idea_id}...")
        try:
            script_args = [
                "--idea-id", str(idea_id),
                "--ideas-file", ideas_path,
                "--output", script_path,
            ]
            if os.getenv("SHORT_VIDEO") == "1":
                script_args.append("--short")
            run_tool("generate_retention_script.py", script_args)
        except Exception as e:
            raise RuntimeError(f"Script generation failed: {e}")

    # Load script to get title
    with open(script_path) as f:
        script_data = json.load(f)
    title = script_data.get("title", f"Video {idea_id}")

    # ── Step B: Generate Voiceover ──────────────────────────────────────────
    if script_valid and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
        print(f"  [B] Voiceover already exists, skipping generation.")
        from mutagen.mp3 import MP3
        duration = MP3(audio_path).info.length
    else:
        if os.path.exists(audio_path):
            os.remove(audio_path)  # Remove stale audio from a different idea
        print(f"  [B] Generating voiceover for '{title}'...")
        try:
            duration_str = run_tool("generate_voiceover.py", [
                "--script-file", script_path,
                "--output", audio_path,
            ])
            duration = float(duration_str) if duration_str else 0
        except Exception as e:
            raise RuntimeError(f"Voiceover generation failed: {e}")

    # ── Step B.5: Generate Captions (Whisper) ───────────────────────────────
    captions_valid = (script_valid and os.path.exists(captions_path)
                      and os.path.getsize(captions_path) > 0)
    if captions_valid:
        print(f"  [B.5] Captions already exist, skipping.")
    else:
        if os.path.exists(captions_path):
            os.remove(captions_path)
        print(f"  [B.5] Generating word-level captions (Whisper)...")
        try:
            run_tool("generate_captions.py", [
                "--audio-file", audio_path,
                "--output", captions_path,
            ])
            captions_valid = True
        except Exception as e:
            print(f"  WARNING: Caption generation failed (non-fatal): {e}", file=sys.stderr)
            captions_path = None

    # ── Step C: Fetch Pexels Footage ────────────────────────────────────────
    manifest_path = os.path.join(footage_dir, "footage_manifest.json")
    footage_valid = script_valid and os.path.exists(manifest_path)
    if footage_valid:
        print(f"  [C] Footage already exists, skipping download.")
    else:
        if os.path.exists(footage_dir):
            shutil.rmtree(footage_dir)  # Remove stale footage from a different idea
        print(f"  [C] Fetching mixed footage (news images + Pexels stock)...")
        try:
            run_tool("fetch_mixed_footage.py", [
                "--script-file", script_path,
                "--output-dir", footage_dir,
            ])
            footage_valid = True
        except Exception as e:
            raise RuntimeError(f"Footage fetch failed: {e}")

    # ── Step D: Assemble Video ──────────────────────────────────────────────
    if footage_valid and os.path.exists(output_path) and os.path.getsize(output_path) > 1_000_000:
        print(f"  [D] Video already assembled, skipping render.")
    else:
        if os.path.exists(output_path):
            os.remove(output_path)  # Remove stale video from a different idea
        print(f"  [D] Assembling video (this takes 8-15 min)...")
        try:
            assemble_args = [
                "--script-file", script_path,
                "--audio-file", audio_path,
                "--footage-dir", footage_dir,
                "--output", output_path,
            ]
            if captions_path and os.path.exists(captions_path):
                assemble_args += ["--captions-file", captions_path]
            run_tool("assemble_video.py", assemble_args, capture_output=False)  # Show render progress
        except Exception as e:
            raise RuntimeError(f"Video assembly failed: {e}")

    # ── Step E: Upload to YouTube (unlisted) ────────────────────────────────
    print(f"  [E] Uploading to YouTube as unlisted...")
    try:
        upload_out = run_tool("upload_to_youtube.py", [
            "--video-file", output_path,
            "--script-file", script_path,
            "--privacy", "unlisted",
        ])
        # stdout: "video_id https://youtube.com/watch?v=video_id"
        parts = upload_out.split()
        video_id = parts[0] if parts else ""
        video_url = parts[1] if len(parts) > 1 else f"https://youtube.com/watch?v={video_id}"
    except Exception as e:
        raise RuntimeError(f"YouTube upload failed: {e}")

    # ── Step F: Generate Thumbnail ───────────────────────────────────────────
    print(f"  [F] Generating thumbnail...")
    thumbnail_path = os.path.join(TMP_DIR, "thumbnails", f"{video_key}_thumbnail.jpg")
    os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
    thumbnail_uploaded = False
    try:
        run_tool("generate_thumbnail.py", [
            "--script-file", script_path,
            "--output-file", thumbnail_path,
        ])

        # ── Step G: Upload Thumbnail to YouTube ──────────────────────────────
        print(f"  [G] Uploading thumbnail to YouTube...")
        run_tool("upload_thumbnail.py", [
            "--video-id", video_id,
            "--thumbnail-file", thumbnail_path,
        ])
        thumbnail_uploaded = True
        print(f"  ✓ Thumbnail uploaded")
    except Exception as e:
        print(f"  WARNING: Thumbnail failed (non-fatal): {e}", file=sys.stderr)

    # ── Step H: SEO Optimization ─────────────────────────────────────────────
    channel_name = os.getenv("CHANNEL_NAME", "TrendingTopics")
    seo_path = os.path.join(TMP_DIR, "seo", f"{video_key}_seo.json")
    os.makedirs(os.path.dirname(seo_path), exist_ok=True)
    try:
        print(f"  [H] Running SEO optimization...")
        seo_args = [
            "--script-file", script_path,
            "--channel-name", channel_name,
            "--output", seo_path,
        ]
        if video_id:
            seo_args += ["--video-id", video_id, "--update-youtube"]
        run_tool("generate_seo_metadata.py", seo_args)
        print(f"  ✓ SEO metadata applied")
    except Exception as e:
        print(f"  WARNING: SEO optimization failed (non-fatal): {e}", file=sys.stderr)

    return {
        "idea_id": idea_id,
        "title": title,
        "script_path": script_path,
        "audio_path": audio_path,
        "captions_path": captions_path,
        "footage_dir": footage_dir,
        "output_path": output_path,
        "youtube_video_id": video_id,
        "youtube_url": video_url,
        "status": "uploaded_unlisted",
        "published": False,
        "duration_seconds": duration,
        "thumbnail_path": thumbnail_path if thumbnail_uploaded else None,
        "thumbnail_uploaded": thumbnail_uploaded,
    }


def build_review_email(produced_videos, channel_name):
    """Build the video review email body."""
    lines = [
        f"Your {len(produced_videos)} video(s) for '{channel_name}' are ready for review!",
        "",
        "Watch each video on YouTube (links are UNLISTED — only you can see them):",
        "",
    ]

    for i, (key, vid) in enumerate(produced_videos.items(), 1):
        lines.append(f"Video {i}: \"{vid['title']}\"")
        lines.append(f"   {vid['youtube_url']}")
        lines.append(f"   Video ID: {vid['youtube_video_id']}")
        lines.append("")

    lines += [
        "─" * 50,
        "To publish, reply to this email with:",
        "",
        "  APPROVE ALL                       (publish all videos)",
        "  APPROVE: videoId1, videoId2       (approve specific videos)",
        "  REJECT: videoId1                  (skip specific videos)",
        "",
        "Approved videos will be made PUBLIC on YouTube.",
        "",
        "—YouTube Automation",
    ]

    return "\n".join(lines)


def main():
    state = get_state()
    approved_ids = state.get("approved_idea_ids", [])
    channel_name = os.getenv("CHANNEL_NAME", "TrendingTopics")
    approval_email = os.getenv("APPROVAL_EMAIL")

    if not approved_ids:
        print("[production_agent] No approved idea IDs found in state.")
        sys.exit(0)

    if not approval_email:
        print("ERROR: APPROVAL_EMAIL not set in .env", file=sys.stderr)
        sys.exit(1)

    ideas_path = os.path.join(TMP_DIR, "ideas.json")
    if not os.path.exists(ideas_path):
        log_error("ideas.json not found in .tmp/")
        sys.exit(1)

    set_phase("production_in_progress")
    print(f"[production_agent] Producing {len(approved_ids)} video(s): {approved_ids}")

    produced = {}
    failed = []

    for i, idea_id in enumerate(approved_ids, 1):
        video_key = f"video_{i}"
        print(f"\n[production_agent] Processing idea {idea_id} ({i}/{len(approved_ids)})...")

        try:
            video_meta = produce_video(idea_id, video_key, ideas_path)
            produced[video_key] = video_meta

            # Update state incrementally
            update_state({"videos": {video_key: video_meta}})
            print(f"  ✓ {video_key} complete: {video_meta['youtube_url']}")

        except Exception as e:
            error_msg = f"Failed to produce {video_key} (idea {idea_id}): {e}"
            log_error(error_msg)
            print(f"  ✗ {error_msg}", file=sys.stderr)
            failed.append(idea_id)

    if not produced:
        log_error("All video productions failed.")
        run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", "[YT Automation] ERROR: Video production failed",
            "--body", f"All {len(approved_ids)} video productions failed. Check .tmp/cron.log for details.",
        ])
        sys.exit(1)

    # Send review email
    print(f"\n[production_agent] Sending review email for {len(produced)} video(s)...")
    email_body = build_review_email(produced, channel_name)

    week_str = state.get("week", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    subject = f"[YT Automation] {len(produced)} Video(s) Ready for Review - Reply to Publish"

    if failed:
        subject += f" ({len(failed)} failed)"
        email_body += f"\n\nNote: {len(failed)} video(s) failed to produce (idea IDs: {failed})."

    try:
        message_id = run_tool("send_email.py", [
            "--to", approval_email,
            "--subject", subject,
            "--body", email_body,
        ])
        print(f"  → Review email sent (ID: {message_id})")
        update_state({
            "review_email_message_id": message_id,
            "review_email_sent_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        log_error(f"Review email send failed: {e}")
        print(f"WARNING: Could not send review email: {e}", file=sys.stderr)

    set_phase("awaiting_video_approval")
    print("[production_agent] Done. Waiting for video approval.")


if __name__ == "__main__":
    main()
