"""
generate_retention_script.py — Generate a retention-optimized video script using Claude Sonnet

Acts as a professional YouTube scriptwriter specialized in retention optimization.
Produces a script structured with:
  - Pattern interrupt hook in the first 5 seconds
  - Curiosity-driven loop that keeps viewers watching
  - Clear problem → insight → payoff structure
  - Strategic pattern interrupts every 30-60 seconds
  - Moments that encourage comments and engagement
  - Subscriber-driving CTA at the end

Output: JSON file with segmented script, compatible with all downstream pipeline tools.

Usage:
    python3 tools/generate_retention_script.py \
        --idea-id 3 \
        --ideas-file .tmp/ideas.json \
        --output .tmp/scripts/video_3_script.json

    # From a topic directly (no ideas file needed):
    python3 tools/generate_retention_script.py \
        --topic "5 habits that changed my life" \
        --output .tmp/scripts/standalone_script.json

Output: JSON file with segmented script
Exit code: 0 on success, 1 on failure
"""

import argparse
import json
import os
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_STRATEGY_PATH = os.path.join(PROJECT_ROOT, "channel_strategy.json")


def load_strategy(path):
    """Load channel strategy JSON if it exists, return {} otherwise."""
    try:
        if path and os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def build_voice_and_positioning(idea, strategy):
    """Build the brand voice + channel positioning block from strategy."""
    if not strategy:
        return "- Voice: Warm, direct, conversational — like a knowledgeable friend giving real advice."

    positioning = strategy.get("channel_positioning", {})
    brand_voice = positioning.get("brand_voice", "")
    unique_angle = positioning.get("unique_angle", "")
    differentiation = positioning.get("differentiation", "")

    # Find the matching content format entry for richer guidance
    idea_format = idea.get("content_format", "").lower()
    format_guidance = ""
    for f in strategy.get("content_formats", []):
        if idea_format and idea_format in f.get("format", "").lower():
            format_guidance = f"\n- Content Format in Use ({f['format']}): {f.get('why_it_works', '')}"
            break

    lines = []
    if brand_voice:
        lines.append(f"- Brand Voice: {brand_voice}")
    if unique_angle:
        lines.append(f"- Channel Angle: {unique_angle}")
    if differentiation:
        lines.append(f"- Differentiation: {differentiation}")
    if format_guidance:
        lines.append(format_guidance)
    return "\n".join(lines) if lines else "- Voice: Warm, direct, conversational."


def build_short_prompt(idea, channel_name, strategy=None):
    """Build a compact ~2-minute script prompt for test runs."""
    voice_block = build_voice_and_positioning(idea, strategy)
    category = idea.get("category", "world")
    news_query_base = idea.get("news_search_query", idea.get("title", ""))
    category_framing = {
        "politics":      "Explain the political situation clearly for a general audience. Be factual, not partisan.",
        "tech":          "Break down the technology story — what it is, why it matters, what comes next.",
        "finance":       "Explain the economic story clearly — what happened, why it matters for everyday people.",
        "science":       "Make the science accessible — explain the discovery, its implications, and real-world impact.",
        "sports":        "Cover the sports story with context — the event, the stakes, the significance.",
        "entertainment": "Cover the entertainment story with insight — cultural significance, not just gossip.",
        "world":         "Explain the global event clearly — what's happening, who's involved, what's at stake.",
    }.get(category, "Explain this story clearly for a general, curious audience.")

    return f"""Act as a professional YouTube scriptwriter for a channel called "{channel_name}".

Write a SHORT (~2 minute) retention-optimized video script for this idea:
Title: {idea['title']}
Hook concept: {idea.get('hook', '')}
Category: {category} — {category_framing}
News search base: {news_query_base}

REQUIREMENTS:
- Total length: ~1.5-2 minutes spoken (~200-260 words at 130 words/min)
- Format: Faceless, stock footage + AI voiceover. Second-person ("you"), no "I" statements.
- Be factual, concise, punchy.
{voice_block}

STRUCTURE (4 segments only):
  1. hook (15-20s): Bold pattern interrupt — unexpected stat or tension-creating statement
  2. main_point (75-90s): Core insight → concrete example → why it matters to the viewer
  3. engagement (15-20s): One specific question for viewers to answer in comments
  4. cta (20-25s): Value-specific subscribe CTA

Return ONLY a valid JSON object:
{{
  "idea_id": {idea['id']},
  "title": "{idea['title']}",
  "thumbnail_text": "SHORT THUMBNAIL TEXT (max 5 words, all caps)",
  "description": "YouTube video description (100-150 words, keywords, engagement CTA)",
  "tags": ["tag1", "tag2"],
  "category_id": "26",
  "total_duration_estimate": 120,
  "segments": [
    {{
      "segment_id": 1,
      "type": "hook",
      "text": "Exact spoken words",
      "visual_cue": "Specific footage description",
      "overlay_text": "On-screen text or null",
      "duration_estimate": 18,
      "pexels_search_queries": ["specific scene query", "alternate angle same theme", "broad fallback"],
      "news_search_query": "specific dateable query for news image"
    }}
  ]
}}

Segment types in order: hook, main_point, engagement, cta
Each segment must have EXACTLY 3 pexels_search_queries (specific → alternate → broad fallback).
Respond ONLY with the JSON object, no other text.
"""


def build_prompt(idea, channel_name, strategy=None):
    voice_block = build_voice_and_positioning(idea, strategy)
    category = idea.get("category", "world")
    news_query_base = idea.get("news_search_query", idea.get("title", ""))

    category_framing = {
        "politics":      "Explain the political situation clearly for a general audience. Be factual, not partisan.",
        "tech":          "Break down the technology story — what it is, why it matters, what comes next.",
        "finance":       "Explain the economic story clearly — what happened, why it matters for everyday people.",
        "science":       "Make the science accessible — explain the discovery, its implications, and real-world impact.",
        "sports":        "Cover the sports story with context — the event, the stakes, the significance.",
        "entertainment": "Cover the entertainment story with insight — cultural significance, not just gossip.",
        "world":         "Explain the global event clearly — what's happening, who's involved, what's at stake.",
    }.get(category, "Explain this story clearly for a general, curious audience.")

    return f"""Act as a professional YouTube scriptwriter specialized in retention optimization for a current events explainer channel.

CHANNEL IDENTITY: "{channel_name}" is the Implications Channel. Every video answers: "Something just happened — here's what it actually means for YOU." We do not just report events. We map world events to their direct personal consequences: the viewer's wallet, job, health, or rights. This is our only reason to exist and must be present in every script.

This script is for a channel called "{channel_name}".
Category: {category}
Framing: {category_framing}

Write a complete, retention-engineered video script for this idea:

Title: {idea['title']}
Hook concept: {idea.get('hook', '')}
Unique angle: {idea.get('angle', '')}
Target emotion: {idea.get('target_emotion', 'curiosity')}
Content format: {idea.get('content_format', 'breakdown')}
News search base: {news_query_base}

Channel: {channel_name}

IMPLICATIONS REQUIREMENT (non-negotiable):
- Every script MUST contain an explicit "personal implication" moment — a sentence that directly translates the world event to the viewer's life. Example: "That means your gas bill could rise by $40-60 per month starting this summer." or "If you work in tech, this is the category of roles being cut first." or "Your personal location data is being sold to government agencies right now without your knowledge or consent."
- The bridge and each point must answer: "Why does this matter to the person watching?" not just "What happened?"
- The hook must state the personal consequence immediately, not just the event.

RETENTION ENGINEERING REQUIREMENTS:
- Total length: ~8-10 minutes spoken at natural pace (~130 words/min = 1040-1300 words total)
- Format: Faceless channel — news images + stock footage + AI voiceover. Second-person ("you"), no "I" statements.
- Treat viewers as intelligent but non-specialist: explain context, acronyms, and background clearly.
- Be factual and accurate. No speculation unless clearly framed as such.
{voice_block}

TITLE RULES: NEVER use a question mark in the title. State the implication confidently.

HOOK (first 5 seconds): Must be a PATTERN INTERRUPT — something unexpected, counterintuitive, or surprising that breaks the viewer's scroll. NOT a question. A bold statement, shocking stat, or tension-creating sentence. In the hook text, immediately tease the payoff: "By the end of this video, you'll know exactly [specific thing viewer will understand/be able to do]." This creates a commitment loop.

CURIOSITY LOOP: Open a loop in the hook that only gets resolved in point_4 — give viewers a reason to stay all the way through.

STRUCTURE:
  1. Pattern interrupt hook (15-20 seconds) → immediately creates curiosity/tension
  2. Bridge: reinforce the promise, tease what's coming (20-30 seconds)
  3. Context: set up the problem space, why this matters (30-45 seconds)
  4. Four main points (~75-90 seconds each): problem → insight → concrete example → actionable takeaway
     - Include a PATTERN INTERRUPT between each point (10-15 seconds): rhetorical question, surprising stat
     - Point 4 should resolve the curiosity loop opened in the hook
  5. Engagement moment: a specific question for viewers to answer in the comments (20-30 seconds)
  6. CTA: Drive subscriptions with a specific value promise, not generic "subscribe" — tell them EXACTLY what they'll get next (30-45 seconds)

Return ONLY a valid JSON object with this exact structure:
{{
  "idea_id": {idea['id']},
  "title": "{idea['title']}",
  "thumbnail_text": "SHORT THUMBNAIL TEXT (max 5 words, all caps, creates curiosity or tension)",
  "thumbnail_person_query": "Pexels search query using the ACTUAL PERSON or iconic figure in this story (e.g. 'Donald Trump White House', 'Elon Musk Tesla factory', 'Jerome Powell Federal Reserve'). If no person, use the most iconic visual of the story. This drives thumbnail CTR.",
  "description": "YouTube video description (150-300 words, includes timestamps, relevant keywords, engagement CTA)",
  "tags": ["tag1", "tag2"],
  "category_id": "26",
  "total_duration_estimate": 540,
  "segments": [
    {{
      "segment_id": 1,
      "type": "hook",
      "text": "The exact spoken words — pattern interrupt, bold statement, no warm-up",
      "visual_cue": "Specific footage description — what to show (concrete, not abstract)",
      "overlay_text": "On-screen text that reinforces the hook tension (or null)",
      "duration_estimate": 20,
      "pexels_search_queries": [
        "primary scene (most specific, e.g. 'capitol building congress crowd protest')",
        "different subject/setting for same theme (e.g. 'government building city hall officials')",
        "broader fallback that always returns results (e.g. 'city skyline aerial urban')"
      ],
      "news_search_query": "specific query to find relevant news article image for this segment (e.g. 'Federal Reserve Jerome Powell press conference 2026')"
    }}
  ]
}}

Segment types in order: hook, bridge, context, point_1, pattern_interrupt_1, point_2, pattern_interrupt_2, point_3, pattern_interrupt_3, point_4, engagement, cta
- hook: 15-20s, bold pattern interrupt
- bridge: 20-30s, reinforce promise, tease content
- context: 30-45s, set up the problem/premise
- point_1: 75-90s, problem → insight → example → takeaway. REQUIRED extra field: "chapter_title" (3-5 ALL CAPS words, max 30 chars, teases what this section reveals — e.g. "ECONOMIC FALLOUT", "WHO GETS HIT FIRST")
- pattern_interrupt_1: 10-15s, rhetorical question or surprising stat (overlay_text is the key phrase). Optionally add "sfx": use "beep_0.5sec" for sharp stats or punchy 1-line questions, "bell" for reflective/insight moments. Omit "sfx" entirely for flowing transitions that don't need a sound cue.
- point_2: 75-90s, deeper insight → example → takeaway. REQUIRED extra field: "chapter_title" (same rules as point_1 — e.g. "THE HIDDEN SIGNAL", "RIPPLE EFFECTS")
- pattern_interrupt_2: 10-15s, rhetorical question or surprising stat. Same "sfx" rule as pattern_interrupt_1.
- point_3: 75-90s, insight → example → takeaway. REQUIRED extra field: "chapter_title" (same rules — e.g. "WHO CONTROLS THIS", "THE REAL COST")
- pattern_interrupt_3: 10-15s, rhetorical question or surprising stat. Same "sfx" rule as pattern_interrupt_1.
- point_4: 75-90s, payoff insight that resolves the curiosity loop from the hook. REQUIRED extra field: "chapter_title" (same rules — e.g. "WHAT YOU CAN DO NOW", "THE BOTTOM LINE")
- engagement: 20-30s, specific comment prompt question. Optionally add "sfx": "bell" if the moment calls for a soft chime to invite reflection. Omit if not needed.
- cta: 30-45s, value-specific subscription CTA

The pexels_search_queries array must have EXACTLY 3 varied queries per segment:
- Query 1: Most specific scene for this segment
- Query 2: Different subject/setting, same theme
- Query 3: Broader fallback (will always return results)
All queries: specific concrete nouns. BAD: "politics". GOOD: "parliament building crowd protest sign".

The news_search_query must be a specific, dateable query to find a relevant news article image for this exact segment (e.g. "NATO summit leaders 2026", "Federal Reserve rate cut Jerome Powell"). Use the video's base news query: "{news_query_base}" as context, but make each segment's query specific to that segment's content.

Respond ONLY with the JSON object, no other text.
"""


def main():
    parser = argparse.ArgumentParser(description="Generate retention-optimized video script")
    parser.add_argument("--idea-id", type=int, default=None, help="Idea ID from ideas file")
    parser.add_argument("--ideas-file", default="", help="Path to ideas.json")
    parser.add_argument("--topic", default="", help="Topic string (used when no ideas file)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--strategy-file", default="", help="Path to channel_strategy.json (auto-detected if not set)")
    parser.add_argument("--short", action="store_true", help="Generate a short ~2-minute script (test mode)")
    args = parser.parse_args()

    if not args.idea_id and not args.topic:
        print("ERROR: Provide either --idea-id + --ideas-file, or --topic", file=sys.stderr)
        sys.exit(1)

    # Build idea dict
    if args.topic:
        idea = {
            "id": 0,
            "title": args.topic,
            "hook": "",
            "angle": "",
            "target_emotion": "curiosity",
            "content_format": "breakdown",
        }
    else:
        if not args.ideas_file or not os.path.exists(args.ideas_file):
            print(f"ERROR: Ideas file not found: {args.ideas_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.ideas_file) as f:
            ideas = json.load(f)
        idea = next((i for i in ideas if i.get("id") == args.idea_id), None)
        if not idea:
            print(f"ERROR: Idea ID {args.idea_id} not found in {args.ideas_file}", file=sys.stderr)
            sys.exit(1)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    channel_name = os.getenv("CHANNEL_NAME", "Our Channel")

    strategy_path = args.strategy_file or DEFAULT_STRATEGY_PATH
    strategy = load_strategy(strategy_path)
    if strategy:
        print(f"  Strategy loaded: {strategy_path}", file=sys.stderr)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_short_prompt(idea, channel_name, strategy) if args.short else build_prompt(idea, channel_name, strategy)

    print(f"Generating retention script for: '{idea['title']}'...", file=sys.stderr)

    script = None
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=7000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            script = json.loads(raw)

            if "segments" not in script or not script["segments"]:
                raise ValueError("Script has no segments")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse script: {e}", file=sys.stderr)
                sys.exit(1)

    # Recalculate duration from word count
    total_words = sum(len(s.get("text", "").split()) for s in script.get("segments", []))
    estimated_duration = int(total_words / 130 * 60)
    script["total_duration_estimate"] = estimated_duration

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(script, f, indent=2)

    print(f"Retention script saved → {args.output} (~{estimated_duration}s, {total_words} words)", file=sys.stderr)
    print(args.output)


if __name__ == "__main__":
    main()
