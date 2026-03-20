#!/usr/bin/env python3
"""
scrape_trending_topics.py

Aggregates trending topics from multiple sources:
  - Google Trends (pytrends, no API key)
  - RSS news feeds: BBC, Reuters, AP News, NPR (feedparser, no API key)
  - Reddit hot posts: worldnews, technology, finance, science (JSON, no auth)
  - YouTube trending (delegates to scrape_youtube_trending.py)
  - NewsAPI top headlines (optional, requires NEWSAPI_KEY)

Deduplicates using Jaccard n-gram similarity, scores by cross-source
frequency + recency + source diversity, and outputs top N topics.

Output: .tmp/trending_topics.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RSS_FEEDS = {
    "BBC News":     "http://feeds.bbci.co.uk/news/rss.xml",
    "Reuters":      "https://feeds.reuters.com/reuters/topNews",
    "AP News":      "https://rsshub.app/apnews/topics/apf-topnews",
    "NPR":          "https://feeds.npr.org/1001/rss.xml",
    "Al Jazeera":   "https://www.aljazeera.com/xml/rss/all.xml",
}

REDDIT_SUBREDDITS = [
    ("worldnews",   "world"),
    ("technology",  "tech"),
    ("finance",     "finance"),
    ("science",     "science"),
    ("business",    "finance"),
    ("geopolitics", "politics"),
    ("environment", "science"),
]

SOURCE_WEIGHTS = {
    "Google Trends": 1.5,
    "NewsAPI":       1.2,
    "Reddit":        1.1,
    "RSS":           1.0,
    "YouTube":       0.8,
}

CLUSTER_SIMILARITY_THRESHOLD = 0.20  # Jaccard threshold for merging topics
MAX_RESULTS_DEFAULT = 50

HEADERS = {
    "User-Agent": "TrendingTopicsBot/1.0 (automated content research; contact via YouTube)"
}


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------

def fetch_google_trends() -> list[dict]:
    """Fetch real-time trending searches via pytrends."""
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=0, timeout=(10, 30), retries=2, backoff_factor=0.5)

        raw = pt.realtime_trending_searches(pn="US")
        signals = []

        if raw is not None and not raw.empty:
            for _, row in raw.iterrows():
                title = str(row.get("title", "")).strip()
                if not title:
                    continue
                # entityNames may be a list of related entities
                related = []
                if "entityNames" in row and row["entityNames"]:
                    try:
                        entities = row["entityNames"]
                        if isinstance(entities, str):
                            entities = json.loads(entities)
                        related = [str(e) for e in entities if e][:5]
                    except Exception:
                        pass

                signals.append({
                    "text":         title,
                    "source":       "Google Trends",
                    "weight":       SOURCE_WEIGHTS["Google Trends"],
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "url":          "",
                    "category":     "",
                    "summary":      "",
                    "related_queries": related,
                })

        print(f"[Google Trends] {len(signals)} signals", file=sys.stderr)
        return signals

    except Exception as e:
        print(f"[Google Trends] FAILED: {e}", file=sys.stderr)
        return []


def fetch_rss_news() -> list[dict]:
    """Fetch top stories from RSS feeds."""
    try:
        import feedparser
    except ImportError:
        print("[RSS] feedparser not installed", file=sys.stderr)
        return []

    signals = []
    for feed_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                if not title:
                    continue

                # Parse published date
                published_at = datetime.now(timezone.utc).isoformat()
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        published_at = datetime(*entry.published_parsed[:6],
                                                tzinfo=timezone.utc).isoformat()
                    except Exception:
                        pass

                summary = entry.get("summary", "")
                # Strip HTML tags from summary
                summary = re.sub(r"<[^>]+>", " ", summary).strip()[:300]

                signals.append({
                    "text":         title,
                    "source":       f"RSS:{feed_name}",
                    "weight":       SOURCE_WEIGHTS["RSS"],
                    "published_at": published_at,
                    "url":          entry.get("link", ""),
                    "category":     _infer_category_from_feed(feed_name),
                    "summary":      summary,
                    "related_queries": [],
                })
            print(f"[RSS:{feed_name}] {len(feed.entries[:15])} signals", file=sys.stderr)
        except Exception as e:
            print(f"[RSS:{feed_name}] FAILED: {e}", file=sys.stderr)

    return signals


def fetch_reddit_hot() -> list[dict]:
    """Fetch hot posts from relevant subreddits using public JSON endpoint."""
    signals = []
    for subreddit, category in REDDIT_SUBREDDITS:
        try:
            url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=15"
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            posts = data.get("data", {}).get("children", [])
            count = 0
            for post in posts:
                d = post.get("data", {})
                title = d.get("title", "").strip()
                if not title or d.get("stickied"):
                    continue

                score = d.get("score", 0)
                weight = SOURCE_WEIGHTS["Reddit"]
                if score > 5000:
                    weight *= 1.2
                elif score > 1000:
                    weight *= 1.1

                created_utc = d.get("created_utc")
                published_at = datetime.now(timezone.utc).isoformat()
                if created_utc:
                    try:
                        published_at = datetime.fromtimestamp(
                            created_utc, tz=timezone.utc).isoformat()
                    except Exception:
                        pass

                signals.append({
                    "text":         title,
                    "source":       f"Reddit:r/{subreddit}",
                    "weight":       weight,
                    "published_at": published_at,
                    "url":          f"https://reddit.com{d.get('permalink', '')}",
                    "category":     category,
                    "summary":      d.get("selftext", "")[:200],
                    "related_queries": [],
                })
                count += 1

            print(f"[Reddit:r/{subreddit}] {count} signals", file=sys.stderr)
            time.sleep(0.5)  # Be polite to Reddit

        except Exception as e:
            print(f"[Reddit:r/{subreddit}] FAILED: {e}", file=sys.stderr)

    return signals


def fetch_youtube_trending_topics() -> list[dict]:
    """Delegate to existing scrape_youtube_trending.py tool."""
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        print("[YouTube] No YOUTUBE_API_KEY, skipping", file=sys.stderr)
        return []

    try:
        script_dir = Path(__file__).parent
        out_path = Path(".tmp/youtube_trending_raw.json")
        out_path.parent.mkdir(exist_ok=True)

        result = subprocess.run(
            [sys.executable, str(script_dir / "scrape_youtube_trending.py"),
             "--max-results", "30", "--output", str(out_path)],
            capture_output=True, text=True, timeout=60
        )

        if result.returncode != 0:
            print(f"[YouTube] Tool failed: {result.stderr[:200]}", file=sys.stderr)
            return []

        if not out_path.exists():
            return []

        videos = json.loads(out_path.read_text())
        signals = []
        for v in videos:
            title = v.get("title", "").strip()
            if not title:
                continue
            signals.append({
                "text":         title,
                "source":       "YouTube",
                "weight":       SOURCE_WEIGHTS["YouTube"],
                "published_at": v.get("published_at", datetime.now(timezone.utc).isoformat()),
                "url":          f"https://youtube.com/watch?v={v.get('video_id', '')}",
                "category":     _infer_category_from_tags(v.get("tags", [])),
                "summary":      v.get("description", "")[:200],
                "related_queries": v.get("tags", [])[:5],
            })

        print(f"[YouTube] {len(signals)} signals", file=sys.stderr)
        return signals

    except Exception as e:
        print(f"[YouTube] FAILED: {e}", file=sys.stderr)
        return []


def fetch_newsapi_headlines() -> list[dict]:
    """Fetch top headlines from NewsAPI (requires NEWSAPI_KEY)."""
    api_key = os.getenv("NEWSAPI_KEY", "")
    if not api_key:
        print("[NewsAPI] No NEWSAPI_KEY, skipping", file=sys.stderr)
        return []

    try:
        from newsapi import NewsApiClient
        client = NewsApiClient(api_key=api_key)
        response = client.get_top_headlines(language="en", page_size=30)

        signals = []
        for article in response.get("articles", []):
            title = (article.get("title") or "").strip()
            if not title or title == "[Removed]":
                continue

            published_at = article.get("publishedAt", "")
            if not published_at:
                published_at = datetime.now(timezone.utc).isoformat()

            signals.append({
                "text":         title,
                "source":       f"NewsAPI:{article.get('source', {}).get('name', 'Unknown')}",
                "weight":       SOURCE_WEIGHTS["NewsAPI"],
                "published_at": published_at,
                "url":          article.get("url", ""),
                "category":     "",
                "summary":      (article.get("description") or "")[:300],
                "related_queries": [],
            })

        print(f"[NewsAPI] {len(signals)} signals", file=sys.stderr)
        return signals

    except Exception as e:
        print(f"[NewsAPI] FAILED: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Category inference
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS = {
    "tech":        ["tech", "technology", "ai", "software", "hardware", "apple", "google",
                    "microsoft", "openai", "chip", "cpu", "gpu", "phone", "robot", "cyber",
                    "hack", "data", "cloud", "startup", "silicon", "programming"],
    "finance":     ["stock", "market", "fed", "federal reserve", "rate", "inflation", "gdp",
                    "economy", "economic", "crypto", "bitcoin", "etf", "recession", "bank",
                    "treasury", "trade", "tariff", "dollar", "debt", "invest", "earning"],
    "politics":    ["president", "congress", "senate", "election", "vote", "democrat",
                    "republican", "white house", "legislation", "law", "supreme court",
                    "government", "policy", "political", "minister", "parliament", "nato",
                    "sanction", "diplomatic"],
    "science":     ["climate", "space", "nasa", "study", "research", "scientist", "discovery",
                    "planet", "species", "gene", "health", "medical", "vaccine", "virus",
                    "drug", "treatment", "environment", "carbon", "ocean"],
    "sports":      ["nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball", "baseball",
                    "hockey", "tennis", "golf", "olympics", "championship", "league", "match",
                    "player", "coach", "trade", "draft", "score"],
    "entertainment": ["movie", "film", "tv", "show", "actor", "actress", "music", "album",
                      "celebrity", "award", "oscar", "grammy", "netflix", "disney", "marvel",
                      "box office", "streaming"],
    "world":       ["war", "conflict", "military", "attack", "killed", "crisis", "protest",
                    "disaster", "earthquake", "flood", "hurricane", "refugee", "migration",
                    "un", "united nations", "treaty", "summit"],
}


def _infer_category(text: str) -> str:
    text_lower = text.lower()
    scores = {}
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in text_lower)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "world"


def _infer_category_from_feed(feed_name: str) -> str:
    mapping = {
        "BBC News":   "world",
        "Reuters":    "world",
        "AP News":    "world",
        "NPR":        "world",
        "Al Jazeera": "world",
    }
    return mapping.get(feed_name, "world")


def _infer_category_from_tags(tags: list[str]) -> str:
    combined = " ".join(tags).lower()
    return _infer_category(combined) if combined else "world"


# ---------------------------------------------------------------------------
# Clustering (Jaccard n-gram similarity)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    """Extract unigrams and bigrams from lowercased, stripped text."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    unigrams = set(words)
    bigrams = {f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)}
    return unigrams | bigrams


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def cluster_topics(raw_signals: list[dict]) -> list[dict]:
    """
    Greedy clustering: merge signals with Jaccard similarity > threshold.
    Returns list of clusters, each with a representative title.
    """
    tokenized = [_tokenize(s["text"]) for s in raw_signals]
    cluster_ids = list(range(len(raw_signals)))  # each signal starts in its own cluster

    for i in range(len(raw_signals)):
        for j in range(i + 1, len(raw_signals)):
            if cluster_ids[i] == cluster_ids[j]:
                continue
            sim = _jaccard(tokenized[i], tokenized[j])
            if sim >= CLUSTER_SIMILARITY_THRESHOLD:
                # Merge j's cluster into i's cluster
                old_id = cluster_ids[j]
                new_id = cluster_ids[i]
                cluster_ids = [new_id if c == old_id else c for c in cluster_ids]

    # Group signals by cluster
    clusters: dict[int, list] = {}
    for idx, cid in enumerate(cluster_ids):
        clusters.setdefault(cid, []).append(raw_signals[idx])

    result = []
    for cid, members in clusters.items():
        # Representative = highest-weight signal
        rep = max(members, key=lambda s: s["weight"])

        total_weight = sum(s["weight"] for s in members)
        unique_sources = list({s["source"].split(":")[0] for s in members})

        # Best category: most specific non-empty one, else infer from rep text
        categories = [s["category"] for s in members if s["category"]]
        category = max(set(categories), key=categories.count) if categories else _infer_category(rep["text"])

        # Most recent signal timestamp
        timestamps = []
        for s in members:
            try:
                timestamps.append(datetime.fromisoformat(s["published_at"].replace("Z", "+00:00")))
            except Exception:
                pass
        latest = max(timestamps) if timestamps else datetime.now(timezone.utc)

        # Best article URL (prefer non-Reddit, non-YouTube)
        news_url = ""
        for s in members:
            if s["url"] and "reddit.com" not in s["url"] and "youtube.com" not in s["url"]:
                news_url = s["url"]
                break
        if not news_url:
            news_url = rep["url"]

        # Collect related queries
        related = []
        for s in members:
            related.extend(s.get("related_queries", []))
        related = list(dict.fromkeys(related))[:8]  # deduplicate, keep order

        # Best summary
        summary = ""
        for s in members:
            if s.get("summary"):
                summary = s["summary"]
                break

        result.append({
            "representative_title": rep["text"],
            "members":              members,
            "total_weight":         total_weight,
            "unique_sources":       unique_sources,
            "category":             category,
            "latest_signal":        latest.isoformat(),
            "top_article_url":      news_url,
            "related_queries":      related,
            "summary":              summary,
        })

    return result


# ---------------------------------------------------------------------------
# Scoring & ranking
# ---------------------------------------------------------------------------

def score_cluster(cluster: dict) -> float:
    now = datetime.now(timezone.utc)
    try:
        latest = datetime.fromisoformat(cluster["latest_signal"].replace("Z", "+00:00"))
        age_hours = (now - latest).total_seconds() / 3600
    except Exception:
        age_hours = 24

    if age_hours <= 6:
        recency = 1.5
    elif age_hours <= 24:
        recency = 1.2
    else:
        recency = 1.0

    diversity = min(len(cluster["unique_sources"]) / 2.0, 2.0)
    return cluster["total_weight"] * diversity * recency


def build_topic_output(cluster: dict, rank: int) -> dict:
    now = datetime.now(timezone.utc)
    try:
        latest = datetime.fromisoformat(cluster["latest_signal"].replace("Z", "+00:00"))
        age_hours = int((now - latest).total_seconds() / 3600)
    except Exception:
        age_hours = 24

    return {
        "rank":                   rank,
        "title":                  cluster["representative_title"],
        "category":               cluster["category"],
        "sources":                cluster["unique_sources"],
        "score":                  round(score_cluster(cluster), 2),
        "summary":                cluster["summary"][:300] if cluster["summary"] else "",
        "related_queries":        cluster["related_queries"],
        "top_article_url":        cluster["top_article_url"],
        "published_within_hours": age_hours,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Aggregate trending topics from multiple sources")
    parser.add_argument("--max-results", type=int, default=MAX_RESULTS_DEFAULT,
                        help="Maximum number of topics to output (default: 50)")
    parser.add_argument("--output", default=".tmp/trending_topics.json",
                        help="Output JSON file path")
    args = parser.parse_args()

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    print("Fetching trending signals from all sources...", file=sys.stderr)

    # Fetch from all sources (failures are non-fatal per source)
    all_signals = []
    all_signals.extend(fetch_google_trends())
    all_signals.extend(fetch_rss_news())
    all_signals.extend(fetch_reddit_hot())
    all_signals.extend(fetch_youtube_trending_topics())
    all_signals.extend(fetch_newsapi_headlines())

    if not all_signals:
        print("ERROR: All sources failed. No signals collected.", file=sys.stderr)
        sys.exit(1)

    print(f"\nTotal raw signals: {len(all_signals)}", file=sys.stderr)

    # Cluster similar topics
    print("Clustering similar topics...", file=sys.stderr)
    clusters = cluster_topics(all_signals)
    print(f"Clusters formed: {len(clusters)}", file=sys.stderr)

    # Score and rank
    clusters.sort(key=score_cluster, reverse=True)
    top_clusters = clusters[:args.max_results]

    # Build output
    output = []
    for rank, cluster in enumerate(top_clusters, start=1):
        output.append(build_topic_output(cluster, rank))

    Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(output)} trending topics to {args.output}", file=sys.stderr)

    # Print summary to stdout for agent consumption
    print(json.dumps({"success": True, "count": len(output), "output": args.output}))


if __name__ == "__main__":
    main()
