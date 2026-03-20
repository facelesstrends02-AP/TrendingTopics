"""
generate_ideas.py — Generate video ideas from trending topics using Claude Sonnet

Accepts trending_topics.json from scrape_trending_topics.py.

Usage:
    python3 tools/generate_ideas.py \\
        --trending-file .tmp/trending_topics.json \\
        --channel-name "The Pulse" \\
        --count 10

Output: .tmp/ideas.json
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
OUTPUT_PATH = os.path.join(PROJECT_ROOT, ".tmp", "ideas.json")


def build_prompt(trending_topics, channel_name, count, analytics_context=""):
    top = trending_topics[:25]
    topic_lines = []
    for i, t in enumerate(top, 1):
        sources = ", ".join(t.get("sources", []))
        category = t.get("category", "")
        score = t.get("score", 0)
        summary = t.get("summary", "")[:150]
        line = f'{i}. "{t["title"]}" — score: {score:.1f} | {category} | {sources}'
        if summary:
            line += f'\n   {summary}'
        topic_lines.append(line)

    topics_block = "\n".join(topic_lines)

    analytics_section = ""
    if analytics_context and analytics_context.strip():
        analytics_section = f"""
PAST PERFORMANCE INSIGHTS FROM THIS CHANNEL (last 4 weeks):
{analytics_context}

Apply these insights: double down on what worked, avoid what flopped, follow the observed patterns.
"""

    return f"""You are a YouTube content strategist for a trending news and events channel called "{channel_name}".

This channel covers anything trending in the world — politics, tech, finance, science, sports, entertainment, and global events. The audience is curious, news-aware adults who want clear, engaging explanations of current events.

Below are the top trending topics worldwide right now, scored by cross-source frequency and recency:

TRENDING TOPICS:
{topics_block}
{analytics_section}
Based on these trends, generate {count} ORIGINAL video ideas for a faceless YouTube channel. Each idea must be:
1. Based on a real, currently trending topic from the list above
2. Framed as a compelling YouTube video — not just a news report, but an engaging explanation/analysis
3. Suitable for stock footage + news images + AI voiceover format
4. 8-10 minute video length potential
5. Written for a general audience — assume viewers are intelligent but not specialists

Return ONLY a valid JSON array with exactly {count} objects. Each object must have these fields:
- "id": integer 1-{count}
- "title": compelling video title (max 80 chars, use power words)
- "hook": the opening sentence of the video (creates immediate curiosity, max 150 chars)
- "angle": what makes this take unique vs just reading the news (max 200 chars)
- "category": one of: politics, tech, finance, science, sports, entertainment, world
- "target_emotion": one of: curiosity, inspiration, fear, aspiration, surprise
- "urgency": one of: high, medium, low
- "controversy_level": one of: low, medium, high
- "potential": one of: High, Medium, Low (based on audience interest and search demand)
- "pexels_search_query": concrete stock footage query using specific nouns
- "news_search_query": specific query to find relevant news article images (e.g. "Trump tariffs China trade war 2026")

Respond ONLY with the JSON array, no other text.
"""


def main():
    parser = argparse.ArgumentParser(description="Generate video ideas from trending topics")
    parser.add_argument("--trending-file", required=True,
                        help="Path to trending_topics.json from scrape_trending_topics.py")
    parser.add_argument("--channel-name", default="",
                        help="Channel name (falls back to CHANNEL_NAME env var)")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--analytics-context", default="", help="Performance insights context (optional)")
    args = parser.parse_args()

    channel_name = args.channel_name or os.getenv("CHANNEL_NAME", "TrendingTopics")

    if not os.path.exists(args.trending_file):
        print(f"ERROR: Trending file not found: {args.trending_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.trending_file) as f:
        trending_topics = json.load(f)

    if not trending_topics:
        print("ERROR: Trending topics file is empty.", file=sys.stderr)
        sys.exit(1)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(trending_topics, channel_name, args.count, args.analytics_context)

    print(f"Generating {args.count} ideas for '{channel_name}'...", file=sys.stderr)

    ideas = None
    raw = ""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
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

    # Validate and clean up
    required_fields = [
        "id", "title", "hook", "angle", "category", "target_emotion",
        "urgency", "controversy_level", "potential",
        "pexels_search_query", "news_search_query",
    ]
    cleaned = []
    for idea in ideas:
        for field in required_fields:
            if field not in idea:
                idea[field] = ""
        cleaned.append(idea)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(cleaned, f, indent=2)

    print(f"Generated {len(cleaned)} ideas → {OUTPUT_PATH}", file=sys.stderr)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
