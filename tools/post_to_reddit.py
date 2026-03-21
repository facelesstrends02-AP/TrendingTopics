"""
post_to_reddit.py — Post a YouTube video to a single subreddit

Tries a link post first. If the subreddit requires self-text (no link posts
allowed), falls back to a self post with the body text + URL at the end.

Usage:
    python3 tools/post_to_reddit.py \
        --title "Why Iran Ditching the Dollar Is About to Cost You" \
        --url "https://www.youtube.com/watch?v=abc123" \
        --subreddit "geopolitics" \
        --body "Iran just moved 17% of its oil trade out of USD..."

    # Dry run (print without posting):
    python3 tools/post_to_reddit.py --title "..." --url "..." --subreddit "..." --dry-run

Output (stdout): Reddit post URL on success
Exit code: 0 on success or graceful skip, 1 on hard failure
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()


def get_reddit_client():
    import praw
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    username = os.getenv("REDDIT_USERNAME", "").strip()
    password = os.getenv("REDDIT_PASSWORD", "").strip()
    user_agent = os.getenv("REDDIT_USER_AGENT", "TrendingTopics/1.0").strip()

    missing = [k for k, v in {
        "REDDIT_CLIENT_ID": client_id,
        "REDDIT_CLIENT_SECRET": client_secret,
        "REDDIT_USERNAME": username,
        "REDDIT_PASSWORD": password,
    }.items() if not v]

    if missing:
        print(f"ERROR: Missing Reddit credentials in .env: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        username=username,
        password=password,
        user_agent=user_agent,
    )


def try_link_post(subreddit_obj, title, url):
    """Attempt a link post. Returns submission or raises."""
    return subreddit_obj.submit(title=title, url=url)


def try_self_post(subreddit_obj, title, body, url):
    """Fall back to a self post with URL in body."""
    full_body = f"{body}\n\n▶ Full breakdown: {url}" if body else f"▶ Full breakdown: {url}"
    return subreddit_obj.submit(title=title, selftext=full_body)


def post(title, url, subreddit_name, body, dry_run):
    if dry_run:
        print(f"[DRY RUN] Would post to r/{subreddit_name}:", file=sys.stderr)
        print(f"  Title: {title}", file=sys.stderr)
        print(f"  URL:   {url}", file=sys.stderr)
        if body:
            print(f"  Body:  {body[:120]}...", file=sys.stderr)
        print(f"https://www.reddit.com/r/{subreddit_name}/ (dry run)", file=sys.stderr)
        print(f"https://www.reddit.com/r/{subreddit_name}/")
        return

    import prawcore

    reddit = get_reddit_client()
    sub = reddit.subreddit(subreddit_name)

    for attempt in range(2):
        try:
            # Try link post first
            try:
                submission = try_link_post(sub, title, url)
                post_url = f"https://www.reddit.com{submission.permalink}"
                print(f"  Posted (link) to r/{subreddit_name}: {post_url}", file=sys.stderr)
                print(post_url)
                return
            except Exception as e:
                err = str(e).lower()
                # If subreddit doesn't allow link posts, fall back to self post
                if "no_links" in err or "links are not allowed" in err or "link posts are not allowed" in err:
                    print(f"  r/{subreddit_name} doesn't allow link posts, trying self post...", file=sys.stderr)
                    submission = try_self_post(sub, title, body, url)
                    post_url = f"https://www.reddit.com{submission.permalink}"
                    print(f"  Posted (self) to r/{subreddit_name}: {post_url}", file=sys.stderr)
                    print(post_url)
                    return
                raise

        except prawcore.exceptions.Forbidden:
            print(f"  WARNING: r/{subreddit_name} is restricted or banned for this account. Skipping.", file=sys.stderr)
            return  # Non-fatal — skip this subreddit

        except prawcore.exceptions.TooManyRequests:
            if attempt == 0:
                print(f"  Rate limited. Waiting 65 seconds...", file=sys.stderr)
                time.sleep(65)
                continue
            print(f"  ERROR: Still rate limited after retry. Skipping r/{subreddit_name}.", file=sys.stderr)
            return

        except Exception as e:
            err_str = str(e)
            # Handle subreddit-specific posting restrictions gracefully
            if any(x in err_str.lower() for x in [
                "banned", "private", "quarantine", "not allowed", "flair",
                "karma", "account age", "you are not allowed"
            ]):
                print(f"  WARNING: Cannot post to r/{subreddit_name}: {err_str[:120]}. Skipping.", file=sys.stderr)
                return
            if attempt == 0:
                print(f"  Attempt 1 failed ({err_str[:100]}). Retrying...", file=sys.stderr)
                time.sleep(5)
                continue
            print(f"  ERROR posting to r/{subreddit_name}: {err_str}", file=sys.stderr)
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Post a YouTube video to a subreddit")
    parser.add_argument("--title", required=True, help="Post title (video title)")
    parser.add_argument("--url", required=True, help="YouTube video URL")
    parser.add_argument("--subreddit", required=True, help="Subreddit name (no r/ prefix)")
    parser.add_argument("--body", default="", help="Self-text body (hook + angle)")
    parser.add_argument("--dry-run", action="store_true", help="Print without posting")
    args = parser.parse_args()

    post(
        title=args.title,
        url=args.url,
        subreddit_name=args.subreddit,
        body=args.body,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
