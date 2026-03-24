"""
generate_viral_ideas.py — Generate viral video ideas using a trend researcher prompt (Claude Sonnet)

Accepts either:
  - New format: --trending-file .tmp/trending_topics.json (multi-source aggregated topics)
  - Legacy format: --scraped-file .tmp/scraped_videos.json (YouTube-only, backward compat)

Detects format automatically by checking for "score" vs "views" field.

Usage:
    python3 tools/generate_viral_ideas.py \\
        --trending-file .tmp/trending_topics.json \\
        --channel-name "The Pulse" \\
        --count 10

    # Topic-focused (no trending file):
    python3 tools/generate_viral_ideas.py \\
        --channel-name "The Pulse" \\
        --topic "AI regulation" \\
        --count 10

Output: .tmp/viral_ideas.json
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
OUTPUT_PATH = os.path.join(PROJECT_ROOT, ".tmp", "viral_ideas.json")
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


def build_strategy_section(strategy):
    """Build the CHANNEL STRATEGY context block to inject into the prompt."""
    if not strategy:
        return ""
    positioning = strategy.get("channel_positioning", {})
    brand_voice = positioning.get("brand_voice", "")
    differentiation = positioning.get("differentiation", "")
    pillars = strategy.get("content_pillars", [])
    formats = strategy.get("content_formats", [])

    parts = []
    if brand_voice:
        parts.append(f"Brand Voice: {brand_voice}")
    if differentiation:
        parts.append(f"Differentiation: {differentiation}")
    if pillars:
        pillar_lines = "\n".join(
            f"  - {p['name']}: {p['description']}" for p in pillars if p.get("name")
        )
        parts.append(f"Content Pillars (each idea must map to one of these):\n{pillar_lines}")
    if formats:
        format_lines = "\n".join(
            f"  - {f['format']}: {f.get('why_it_works', '')[:130]}"
            for f in formats if f.get("format")
        )
        parts.append(f"Proven Content Formats (use these structures):\n{format_lines}")

    if not parts:
        return ""
    return "\nCHANNEL STRATEGY — align all ideas to this:\n" + "\n\n".join(parts) + "\n"


def build_trending_section(data: list) -> str:
    """Format trending_topics.json data for the prompt."""
    if not data:
        return ""
    top = data[:25]
    lines = []
    for i, t in enumerate(top, 1):
        sources = ", ".join(t.get("sources", []))
        category = t.get("category", "")
        score = t.get("score", 0)
        summary = t.get("summary", "")[:120]
        related = ", ".join(t.get("related_queries", [])[:3])

        line = f'{i}. "{t["title"]}" — score: {score:.1f} | category: {category} | sources: {sources}'
        if summary:
            line += f'\n   Summary: {summary}'
        if related:
            line += f'\n   Related searches: {related}'
        lines.append(line)

    return f"""
CURRENTLY TRENDING WORLDWIDE (multi-source aggregation — Google Trends, news, Reddit, YouTube):
{chr(10).join(lines)}

Analyze these for YouTube video potential — what stories have the most audience curiosity, what angles are underserved, what topics will still be relevant in 2-4 days when the video publishes.
"""


def build_legacy_section(scraped_videos: list) -> str:
    """Format old scraped_videos.json data (backward compat)."""
    if not scraped_videos:
        return ""
    top = sorted(scraped_videos, key=lambda v: v.get("views", 0), reverse=True)[:20]
    lines = []
    for i, v in enumerate(top, 1):
        lines.append(
            f'{i}. "{v["title"]}" — {v.get("views", 0):,} views\n'
            f'   Channel: {v.get("channel", "")} | Tags: {", ".join(v.get("tags", [])[:5])}'
        )
    return f"""
CURRENTLY TRENDING (YouTube):
{chr(10).join(lines)}

Analyze these for patterns — what titles work, what emotions they target, what gaps exist.
"""


def build_prompt(channel_name, count, trending_topics=None, scraped_videos=None,
                 topic="", analytics_context="", strategy=None):

    # Auto-detect input format
    if trending_topics is not None:
        trending_section = build_trending_section(trending_topics)
    elif scraped_videos is not None:
        trending_section = build_legacy_section(scraped_videos)
    else:
        trending_section = ""

    topic_section = f"\nFocus area: {topic}\n" if topic else ""

    analytics_section = ""
    if analytics_context and analytics_context.strip():
        analytics_section = f"""
PAST PERFORMANCE INSIGHTS FROM THIS CHANNEL (last 4 weeks):
{analytics_context}

Apply these insights: double down on what worked, avoid what flopped.
"""

    strategy_section = build_strategy_section(strategy)

    return f"""Act as a YouTube trend researcher and viral content strategist for a current events explainer channel called "{channel_name}".

CHANNEL IDENTITY: This channel's single positioning is "Something just happened in the world — here's what it actually means for YOU." Every video maps a world event to a direct personal consequence: the viewer's wallet, job security, health, rights, or daily life. We are the implications channel. We do not just explain the news — we explain what the news COSTS or CHANGES for the average person.

The audience is news-curious adults 25-45 who are tired of headlines without context and want to understand the downstream effects of world events on their own lives.

Generate {count} high-potential video ideas that will perform well on YouTube in the next 2-5 days.

RECENCY RULE: Strongly prefer events and stories from the last 7 days. Only draw on older events if the current week has fewer than 3 clearly high-impact topics — and in that case, only use older events that are still actively developing or generating fresh audience searches.
{topic_section}{trending_section}{analytics_section}{strategy_section}
Each idea MUST have a clear personal consequence angle baked into the title and hook — not just "what happened" but "what this means for you." Prioritize:
- Topics where there is an underexplained personal consequence (financial, health, job, rights)
- Angles that translate big abstract events into household-level impact
- Hooks built around the personal cost/risk/opportunity, not just the event itself
- Stories with lasting search interest (people will still search for the implication weeks later)
- Content suited for faceless format: stock footage + AI voiceover

IMPORTANT: Spread ideas across content pillars — Geopolitics/Finance, Power/Control, Science/Health, Tech. Do not cluster everything in one area.

TITLE RULES (critical):
- NEVER use a question mark in titles — audiences want confident analysis, not questions
- Frame every title around personal consequence using "You" framing where natural: "What X Means for You", "How Y Is About to Cost You", "Why Z Will Change Your [wallet/job/rights]"
- Use the formula: [Specific event with number/person] — [Personal consequence the viewer didn't know about]
- Example: "Trump Threatens to Destroy 17% of World's Gas Supply — What Happens to Your Bills" NOT "Will Trump's Iran Threat Affect Gas Prices?"

Return ONLY a valid JSON array with exactly {count} objects. Each object must have:
- "id": integer 1-{count}
- "title": scroll-stopping title (max 80 chars, use power words and tension, NO question marks)
- "hook": the core hook for the first 10 seconds — one punchy sentence that creates immediate curiosity (max 150 chars)
- "angle": what makes this unique vs existing content, why viewers would choose this video (max 200 chars)
- "category": one of: politics, tech, finance, science, sports, entertainment, world
- "target_emotion": one of: curiosity, fear, surprise, aspiration, inspiration
- "urgency": one of: high (breaking/fast-moving), medium (developing story), low (evergreen trending)
- "controversy_level": one of: low, medium, high (how divisive the topic is)
- "potential": one of: High, Medium, Low (based on search demand and viral likelihood)
- "pexels_search_query": concrete stock footage query using specific nouns (e.g. "government building capitol crowd protest" not "politics")
- "news_search_query": specific news search query to find relevant article images (e.g. "Federal Reserve interest rate March 2026")
- "thumbnail_person_query": Pexels photo search query using the ACTUAL PERSON or recognizable figure in this story (e.g. "Donald Trump White House", "Elon Musk Tesla", "Jerome Powell Federal Reserve press conference"). If no specific person, use a recognizable visual symbol of the story (e.g. "Qatar gas field aerial", "Tesla car dashboard autonomous"). This must produce an attention-grabbing face or iconic visual for the thumbnail.
- "content_format": one of: tutorial, breakdown, story, experiment, listicle
- "viral_reason": why this topic has viral potential RIGHT NOW — trend momentum, cultural timing, or audience pain point (max 200 chars)

Respond ONLY with the JSON array, no other text.
"""


def main():
    parser = argparse.ArgumentParser(description="Generate viral video ideas with Claude Sonnet")
    parser.add_argument("--trending-file", default="",
                        help="Path to trending_topics.json from scrape_trending_topics.py")
    parser.add_argument("--scraped-file", default="",
                        help="Legacy: path to scraped_videos.json (YouTube-only)")
    parser.add_argument("--channel-name", default="",
                        help="Channel name (falls back to CHANNEL_NAME env var)")
    parser.add_argument("--topic", default="", help="Optional topic focus")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--analytics-context", default="", help="Performance insights context (optional)")
    parser.add_argument("--output", default="", help="Output path (default: .tmp/viral_ideas.json)")
    parser.add_argument("--strategy-file", default="", help="Path to channel_strategy.json")
    parser.add_argument("--integrate-pipeline", action="store_true",
                        help="Write ideas to state.json for pipeline integration")
    args = parser.parse_args()

    channel_name = args.channel_name or os.getenv("CHANNEL_NAME", "TrendingTopics")
    output_path = args.output or OUTPUT_PATH

    strategy_path = args.strategy_file or DEFAULT_STRATEGY_PATH
    strategy = load_strategy(strategy_path)
    if strategy:
        print(f"  Strategy loaded: {strategy_path}", file=sys.stderr)

    # Load trending data (prefer new format, fall back to legacy)
    trending_topics = None
    scraped_videos = None

    if args.trending_file:
        if not os.path.exists(args.trending_file):
            print(f"ERROR: Trending file not found: {args.trending_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.trending_file) as f:
            data = json.load(f)
        # Detect format by checking first item's fields
        if data and "score" in data[0]:
            trending_topics = data
            print(f"  Loaded {len(data)} trending topics (new format)", file=sys.stderr)
        else:
            scraped_videos = data
            print(f"  Loaded {len(data)} scraped videos (legacy format)", file=sys.stderr)

    elif args.scraped_file:
        if not os.path.exists(args.scraped_file):
            print(f"ERROR: Scraped file not found: {args.scraped_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.scraped_file) as f:
            scraped_videos = json.load(f)
        if not scraped_videos:
            print("WARNING: Scraped videos file is empty, proceeding without trending context.", file=sys.stderr)
            scraped_videos = None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(
        channel_name=channel_name,
        count=args.count,
        trending_topics=trending_topics,
        scraped_videos=scraped_videos,
        topic=args.topic,
        analytics_context=args.analytics_context,
        strategy=strategy,
    )

    print(f"Generating {args.count} viral ideas for '{channel_name}'...", file=sys.stderr)

    ideas = None
    raw = ""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            ideas = json.loads(raw)
            if not isinstance(ideas, list) or len(ideas) == 0:
                raise ValueError("Expected a non-empty JSON array")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse ideas from Claude response: {e}", file=sys.stderr)
                print(f"Raw response: {raw[:500]}", file=sys.stderr)
                sys.exit(1)

    # Validate and ensure all fields present
    required_fields = [
        "id", "title", "hook", "angle", "category", "target_emotion",
        "urgency", "controversy_level", "potential", "pexels_search_query",
        "news_search_query", "thumbnail_person_query", "content_format", "viral_reason",
    ]
    cleaned = []
    for idea in ideas:
        for field in required_fields:
            if field not in idea:
                idea[field] = ""
        cleaned.append(idea)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(cleaned, f, indent=2)

    print(f"Generated {len(cleaned)} viral ideas → {output_path}", file=sys.stderr)

    # Optional: write to state for pipeline integration
    if args.integrate_pipeline:
        try:
            state_path = os.path.join(PROJECT_ROOT, ".tmp", "state.json")
            if os.path.exists(state_path):
                with open(state_path) as f:
                    state = json.load(f)
                state["viral_ideas_path"] = output_path
                with open(state_path, "w") as f:
                    json.dump(state, f, indent=2)
                print("  Integrated into pipeline state", file=sys.stderr)
        except Exception as e:
            print(f"  WARNING: Could not write to state: {e}", file=sys.stderr)

    print(output_path)


if __name__ == "__main__":
    main()
