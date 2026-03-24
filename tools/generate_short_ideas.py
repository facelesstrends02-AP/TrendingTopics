"""
generate_short_ideas.py — Generate 2 independent YouTube Shorts ideas from trending topics (Claude Sonnet)

Unlike generate_viral_ideas.py (full 8-10 min videos), this targets the Shorts format:
a single punchy fact/revelation that can be fully explained in 45-60 seconds.
Ideas are picked fresh from today's trending topics — completely decoupled from the
week's full-video topic.

Usage:
    python3 tools/generate_short_ideas.py \
        --trending-file .tmp/trending_topics.json \
        --output .tmp/shorts/video_1_short_ideas.json

Output: JSON array of exactly 2 Short idea objects
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
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, ".tmp", "short_ideas.json")

REQUIRED_FIELDS = ["id", "title", "hook", "angle", "category",
                   "pexels_queries", "news_search_query"]


def build_trending_section(data: list) -> str:
    if not data:
        return ""
    top = data[:20]
    lines = []
    for i, t in enumerate(top, 1):
        sources = ", ".join(t.get("sources", []))
        score = t.get("score", 0)
        summary = t.get("summary", "")[:100]
        line = f'{i}. "{t["title"]}" — score: {score:.1f} | sources: {sources}'
        if summary:
            line += f'\n   {summary}'
        lines.append(line)
    return "\n".join(lines)


def build_prompt(channel_name: str, trending_topics: list) -> str:
    trending_section = build_trending_section(trending_topics)

    return f"""You are a YouTube Shorts strategist for "{channel_name}" — the Implications Channel.

Every Short must answer: "Something just happened — here's what it means for YOU personally." The viewer's wallet, job, health, or rights must be the focus, not just the event.

SHORTS FORMAT CONSTRAINT: Each idea must work as a standalone 45-60 second video — a SINGLE punchy fact or revelation. NOT a multi-point breakdown. The entire story must be told in one clear arc: hook → personal implication → CTA.

Good Shorts topics: a single surprising number, a single decision that changes something for the viewer, a single reversal or contradiction, a single "you didn't know this" fact. They create immediate curiosity and resolve fully within 60 seconds.

Bad Shorts topics: broad subjects that need 5 minutes to explain, multi-step analyses, "here are 5 reasons why..." topics.

CURRENTLY TRENDING (multi-source — Google Trends, news, Reddit, YouTube):
{trending_section}

Pick the 2 BEST topics from the trending list above that fit the Shorts format. Choose topics from different categories for variety.

RECENCY RULE: Strongly prefer topics from the last 7 days. Only use older topics if the current week has fewer than 2 clearly high-impact, Shorts-suitable stories.

TITLE RULES:
- NEVER use a question mark
- Lead with personal consequence: "What X Means for You", "How Y Is About to Cost You"
- Max 60 characters

Return ONLY a valid JSON array of exactly 2 objects. No markdown, no explanation.

Each object must have:
- "id": integer (1 or 2)
- "title": string, max 60 chars, no question mark, personal consequence angle
- "hook": string, the single most shocking or consequential sentence (max 120 chars) — this is the opening line of the Short
- "angle": string, what makes this a perfect 60-second story — the single fact/revelation at its core (max 150 chars)
- "category": one of: politics, tech, finance, science, world
- "pexels_queries": array of exactly 3 strings — specific B-roll search terms (specific → complementary → broad fallback)
- "news_search_query": string, dateable search query to find relevant footage (e.g. "Federal Reserve rate cut March 2026")

Respond ONLY with the JSON array, starting with [ and ending with ]."""


def main():
    parser = argparse.ArgumentParser(
        description="Generate 2 independent YouTube Shorts ideas from trending topics")
    parser.add_argument("--trending-file", required=True,
                        help="Path to trending_topics.json from scrape_trending_topics.py")
    parser.add_argument("--channel-name", default="",
                        help="Channel name (falls back to CHANNEL_NAME env var)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="Output path (default: .tmp/short_ideas.json)")
    args = parser.parse_args()

    channel_name = args.channel_name or os.getenv("CHANNEL_NAME", "TrendingTopics")

    if not os.path.exists(args.trending_file):
        print(f"ERROR: Trending file not found: {args.trending_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.trending_file) as f:
        trending_topics = json.load(f)

    if not trending_topics:
        print("ERROR: Trending file is empty.", file=sys.stderr)
        sys.exit(1)

    print(f"  Loaded {len(trending_topics)} trending topics.", file=sys.stderr)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(channel_name, trending_topics)

    print(f"Generating 2 Short ideas for '{channel_name}'...", file=sys.stderr)

    ideas = None
    raw = ""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            if raw.startswith("```"):
                lines = raw.split("\n")
                inner = lines[1:] if lines[0].startswith("```") else lines
                if inner and inner[-1].strip() == "```":
                    inner = inner[:-1]
                raw = "\n".join(inner).strip()

            ideas = json.loads(raw)
            if not isinstance(ideas, list) or len(ideas) != 2:
                raise ValueError(f"Expected a list of exactly 2 ideas, got {len(ideas) if isinstance(ideas, list) else type(ideas).__name__}")
            break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == 0:
                print(f"Attempt 1 failed ({e}), retrying...", file=sys.stderr)
            else:
                print(f"ERROR: Could not parse ideas from Claude response: {e}", file=sys.stderr)
                print(f"Raw response: {raw[:500]}", file=sys.stderr)
                sys.exit(1)

    # Validate and fill missing fields
    cleaned = []
    for idea in ideas:
        for field in REQUIRED_FIELDS:
            if field not in idea:
                idea[field] = "" if field != "pexels_queries" else []
        # Ensure pexels_queries is a list of 3
        queries = list(idea.get("pexels_queries") or [])
        while len(queries) < 3:
            queries.append("breaking news world event")
        idea["pexels_queries"] = queries[:3]
        cleaned.append(idea)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(cleaned, f, indent=2)

    print(f"Generated 2 Short ideas → {args.output}", file=sys.stderr)
    for idea in cleaned:
        print(f"  [{idea['category']}] {idea['title']}", file=sys.stderr)

    print(args.output)


if __name__ == "__main__":
    main()
