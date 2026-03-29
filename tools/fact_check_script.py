"""
fact_check_script.py — Fact-check a video script before voiceover is generated

Single Claude Haiku call that simultaneously extracts every specific verifiable claim from
a script and rates each one. Claims with uncertain or speculative language are auto-revised
with hedged wording (in-place). DISPUTED claims (actively wrong) pause the pipeline.

Supports two modes:
  full   (default) — reads segments[].text from full video script JSON
  shorts            — reads spoken_script fields from Shorts plan JSON

Usage:
    python3 tools/fact_check_script.py \
        --script-file .tmp/scripts/video_1_script.json \
        --output-report .tmp/fact_checks/video_1_fact_check.json

    python3 tools/fact_check_script.py \
        --script-file .tmp/shorts/video_1_shorts_plan.json \
        --output-report .tmp/fact_checks/video_1_shorts_fact_check.json \
        --mode shorts

Exit codes:
    0 = all claims handled (verified or auto-revised in place)
    1 = tool error (non-fatal; production_agent logs warning and continues)
    2 = DISPUTED claims found (production_agent pauses this video, emails user)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are a fact-checker for a YouTube news channel called Trends Daily.
Your job is to read a video script, extract every specific verifiable claim, and rate each one.

A verifiable claim is:
- A specific statistic or number ("the average American household spent over $1,600 on electricity")
- A causal assertion ("driven by the natural gas crisis tied to the war in Ukraine")
- A prediction or trend stated as fact ("electricity prices will continue rising")
- A specific attribution ("according to the US Energy Information Administration")
- A direct personal implication stated with certainty ("your gas bill will rise by $40-60")
- A specific event or quantity assertion ("Germany had over 700,000 of these registered by mid-2024")

Do NOT extract: general opinions, stylistic framing, metaphors, rhetorical questions, CTA text,
or channel branding. Do not extract vague statements like "things are changing" or "experts worry".

For each claim:
1. Copy the claim_text VERBATIM as it appears in the script (exact string, no paraphrasing)
2. Note which segment_type it came from
3. Mark is_personal_implication: true if it directly tells the viewer what will happen to them
4. Assign a rating:
   - VERIFIED: you have high confidence this is accurate based on your training knowledge
   - LIKELY_ACCURATE: directionally correct but the specific figure is not confirmed
   - UNVERIFIABLE: too recent (after Aug 2025), too specific/local, or simply not in your knowledge
   - SPECULATIVE: framed as fact but is actually a prediction, forecast, or opinion
   - DISPUTED: your training knowledge clearly contradicts this claim
5. Write a one-sentence rationale
6. For UNVERIFIABLE: provide suggested_revision — minimally changed text with hedged language
   (e.g. "costs $X" → "costs approximately $X"; "will" → "may"; add "as of 2024" anchor)
   For SPECULATIVE: add framing ("analysts project", "this could mean", "one possible outcome")
   For LIKELY_ACCURATE with a very specific number: soften slightly if the figure seems inflated
   For VERIFIED: suggested_revision must be null
   For DISPUTED: suggested_revision must be null (pipeline will pause for manual review)

Your training knowledge cutoff is August 2025.

Return ONLY a valid JSON array. No markdown, no preamble. Each item:
{
  "claim_text": "exact verbatim text from script",
  "segment_type": "hook|bridge|context|point_1|point_2|point_3|point_4|pattern_interrupt_1|pattern_interrupt_2|pattern_interrupt_3|engagement|cta|spoken_script",
  "is_personal_implication": true or false,
  "rating": "VERIFIED|LIKELY_ACCURATE|UNVERIFIABLE|SPECULATIVE|DISPUTED",
  "rationale": "one sentence",
  "suggested_revision": "revised text or null"
}

If there are no verifiable claims, return an empty array [].
Respond ONLY with the JSON array."""


def extract_script_text(script_data, mode):
    """Return (full_text_for_prompt, title, segments_list_for_revision)."""
    if mode == "shorts":
        title = "YouTube Shorts"
        parts = []
        for i, short in enumerate(script_data):
            text = short.get("spoken_script", "")
            if text:
                parts.append(f"[spoken_script short_{i}]\n{text}")
        return "\n\n".join(parts), title, script_data
    else:
        title = script_data.get("title", "Untitled")
        parts = []
        for seg in script_data.get("segments", []):
            seg_type = seg.get("type", "unknown")
            text = seg.get("text", "")
            if text:
                parts.append(f"[{seg_type}]\n{text}")
        return "\n\n".join(parts), title, script_data.get("segments", [])


def call_claude(full_text, title, client):
    """Run fact-check prompt against the script text. Returns list of claim dicts."""
    user_msg = f'Fact-check this video script titled: "{title}"\n\n---\n{full_text}\n---'

    claims = None
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=3000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            claims = json.loads(raw)
            if not isinstance(claims, list):
                raise ValueError("Response is not a JSON array")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse fact-check response: {e}", file=sys.stderr)
                sys.exit(1)

    return claims


def apply_revisions(script_data, claims, mode):
    """
    Apply suggested_revision text replacements in-place to the script data.
    Returns (revised_script_data, revision_log).
    """
    revision_log = []
    revisable_ratings = {"UNVERIFIABLE", "SPECULATIVE", "LIKELY_ACCURATE"}

    for claim in claims:
        if claim.get("rating") not in revisable_ratings:
            continue
        revision = claim.get("suggested_revision")
        if not revision or revision == claim["claim_text"]:
            continue

        original = claim["claim_text"]
        replaced = False

        if mode == "shorts":
            for short in script_data:
                spoken = short.get("spoken_script", "")
                if original in spoken:
                    short["spoken_script"] = spoken.replace(original, revision, 1)
                    replaced = True
                    break
        else:
            for seg in script_data.get("segments", []):
                text = seg.get("text", "")
                if original in text:
                    seg["text"] = text.replace(original, revision, 1)
                    replaced = True
                    break

        if replaced:
            revision_log.append({
                "claim_text": original,
                "revised_to": revision,
                "rating": claim["rating"],
                "segment_type": claim.get("segment_type"),
            })
            claim["revised_in_script"] = True
        else:
            claim["revised_in_script"] = False
            print(
                f"  WARNING: Could not find claim text in script for revision "
                f"(may have been paraphrased): \"{original[:60]}...\"",
                file=sys.stderr,
            )

    return script_data, revision_log


def build_report(script_file, title, mode, claims, revision_log):
    """Assemble the full fact-check report dict."""
    ratings = [c.get("rating") for c in claims]
    disputed = [c for c in claims if c.get("rating") == "DISPUTED"]
    auto_revised = sum(1 for c in claims if c.get("revised_in_script"))

    return {
        "script_file": script_file,
        "video_title": title,
        "mode": mode,
        "model": MODEL,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total_claims": len(claims),
            "verified": ratings.count("VERIFIED"),
            "likely_accurate": ratings.count("LIKELY_ACCURATE"),
            "unverifiable": ratings.count("UNVERIFIABLE"),
            "speculative": ratings.count("SPECULATIVE"),
            "disputed": ratings.count("DISPUTED"),
            "auto_revised": auto_revised,
            "pipeline_paused": len(disputed) > 0,
        },
        "claims": claims,
        "disputed_claims": disputed,
        "revision_log": revision_log,
    }


def main():
    parser = argparse.ArgumentParser(description="Fact-check a video script")
    parser.add_argument("--script-file", required=True, help="Path to script JSON file")
    parser.add_argument("--output-report", required=True, help="Path to write fact-check report JSON")
    parser.add_argument("--mode", choices=["full", "shorts"], default="full",
                        help="Script format: 'full' (default) or 'shorts'")
    args = parser.parse_args()

    # Validate inputs
    if not os.path.exists(args.script_file):
        print(f"ERROR: Script file not found: {args.script_file}", file=sys.stderr)
        sys.exit(1)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    # Load script
    try:
        with open(args.script_file) as f:
            script_data = json.load(f)
    except Exception as e:
        print(f"ERROR: Could not read script file: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract text for prompt
    full_text, title, _ = extract_script_text(script_data, args.mode)

    if not full_text.strip():
        print("WARNING: Script has no text content to fact-check.", file=sys.stderr)
        # Write empty report and exit clean
        os.makedirs(os.path.dirname(os.path.abspath(args.output_report)), exist_ok=True)
        report = build_report(args.script_file, title, args.mode, [], [])
        with open(args.output_report, "w") as f:
            json.dump(report, f, indent=2)
        sys.exit(0)

    print(f"Fact-checking '{title}' ({args.mode} mode)...", file=sys.stderr)

    # Run Claude
    client = anthropic.Anthropic(api_key=api_key)
    claims = call_claude(full_text, title, client)

    print(f"  Extracted {len(claims)} claims.", file=sys.stderr)

    # Apply revisions in-place to script data
    script_data, revision_log = apply_revisions(script_data, claims, args.mode)

    # Write revised script back (overwrite original)
    with open(args.script_file, "w") as f:
        json.dump(script_data, f, indent=2)

    disputed = [c for c in claims if c.get("rating") == "DISPUTED"]
    auto_revised = len(revision_log)

    if auto_revised > 0:
        print(f"  Auto-revised {auto_revised} claim(s) with hedged language.", file=sys.stderr)
    if disputed:
        print(f"  DISPUTED: {len(disputed)} claim(s) contradicted by training knowledge.", file=sys.stderr)

    # Write report
    report = build_report(args.script_file, title, args.mode, claims, revision_log)
    os.makedirs(os.path.dirname(os.path.abspath(args.output_report)), exist_ok=True)
    with open(args.output_report, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Fact-check report saved → {args.output_report}", file=sys.stderr)

    # Exit 2 if disputed claims found (pipeline pause signal)
    if disputed:
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
