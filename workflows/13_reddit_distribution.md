# Workflow 13: Reddit Distribution

## Objective
Automatically post each published video to 2 relevant subreddits ~30 minutes after it goes public on YouTube. Reddit is the highest-ROI off-platform distribution channel for this niche — a single front-page post can deliver 5,000-20,000 views in 48 hours.

## Trigger
`agents/reddit_scheduler.py` — runs Mon/Wed/Fri at 8:00 AM IST (02:30 UTC), 30 minutes after videos go public.

Add the cron entry when ready:
```
30 2 * * 1,3,5   cd /path/to/TrendingTopics && venv/bin/python agents/reddit_scheduler.py >> .tmp/cron.log 2>&1
```

Until then, run manually after each video goes live:
```bash
venv/bin/python3 agents/reddit_agent.py --video-key video_1
```

## One-Time Setup: Reddit API Credentials

1. Log into your Reddit account (or create one dedicated to the channel)
2. Go to https://www.reddit.com/prefs/apps
3. Click **"create another app"**
4. Fill in:
   - Name: `TrendingTopics`
   - Type: **script** (important — not web app)
   - Redirect URI: `http://localhost:8080` (any URL, unused)
5. Click **"create app"**
6. Copy the values into `.env`:
   ```
   REDDIT_CLIENT_ID=<the short string under your app name>
   REDDIT_CLIENT_SECRET=<the "secret" field>
   REDDIT_USERNAME=<your reddit username>
   REDDIT_PASSWORD=<your reddit password>
   REDDIT_USER_AGENT=TrendingTopics/1.0 by u/<your_username>
   ```
7. Install PRAW: `venv/bin/pip install praw`

## Subreddit Mapping

Video category is determined from `ideas.json` → `category` field.

| Category | Subreddits |
|----------|-----------|
| `politics` | r/geopolitics, r/worldpolitics |
| `world` | r/geopolitics, r/worldpolitics |
| `finance` | r/Economics, r/personalfinance |
| `tech` | r/technology, r/Futurology |
| `science` | r/Futurology, r/health |
| `entertainment` | r/OutOfTheLoop, r/todayilearned |
| `sports` | r/sports, r/worldnews |

## Post Format

**Link post** (preferred): Title = video title, URL = YouTube link. No body text required.

**Self post fallback** (if subreddit bans link posts): Title + body built from idea's `hook` and `angle` fields + YouTube URL at the bottom. No extra Claude API call — uses text already generated during idea creation.

## What Gets Skipped (Non-Fatal)

The tool handles these gracefully — logs a warning and moves on:
- Subreddit is private, quarantined, or banned this account
- Karma or account age requirements not met
- Reddit rate limits (retries once after 65 seconds)

A video posting to 1/2 subreddits is fine. 0/2 is logged as an error in state.

## Testing

```bash
# Dry run — prints what would be posted without actually posting
venv/bin/python3 agents/reddit_agent.py --video-key video_1 --dry-run

# Test a real post to Reddit's official test subreddit
venv/bin/python3 tools/post_to_reddit.py \
  --title "Test post — ignore" \
  --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --subreddit "test"
```

## Known Reddit Restrictions

- **r/worldnews**: Only allows links to news articles, not YouTube. Not in our subreddit map for this reason.
- **r/science**: Requires posts to be peer-reviewed research. Not suitable for this content.
- **New Reddit accounts**: May be blocked from posting in large subreddits for 30-90 days. Let the account age before expecting consistent results. Post occasionally to small subreddits to build karma first.
- **Flair requirements**: Some subreddits require flair to post. If posts fail consistently on a specific subreddit, check if flair is required and either add flair selection to the tool or remove that subreddit from the mapping.

## State Tracking

After posting, `state.videos.{video_key}` is updated with:
```json
{
  "reddit_posts": [
    {"subreddit": "geopolitics", "url": "https://reddit.com/r/...", "status": "posted"},
    {"subreddit": "worldpolitics", "url": "", "status": "skipped"}
  ],
  "reddit_posted": true,
  "reddit_posted_at": "2026-03-24T02:35:00+00:00"
}
```

## Output

Completion email sent to `APPROVAL_EMAIL` with subject:
`Reddit posts live — {video title}`

Body lists all post URLs and any skipped subreddits.

---

## Manual Posting Guide (while Reddit API is inactive)

Until Reddit API credentials are set up, a copy-paste-ready posting guide is automatically generated for each video immediately after assembly completes.

**What gets generated:**
- Post title and body (hook + angle from ideas.json)
- Recommended subreddits with direct submission links
- Per-subreddit notes (link vs self post, karma requirements, flair)
- Best posting times in IST
- Step-by-step instructions for Reddit newcomers
- New account tips (karma building, rate limiting)

**Where to find it:**
- File: `.tmp/reddit/video_N_reddit_guide.md`
- Email: sent automatically to `APPROVAL_EMAIL` with subject `Reddit Guide — {title}`

**Manual run:**
```bash
venv/bin/python3 tools/generate_reddit_guide.py --video-key video_1
```

**Switch to automated posting** once Reddit API credentials are configured in `.env`:
```
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USERNAME=...
REDDIT_PASSWORD=...
REDDIT_USER_AGENT=TrendingTopics/1.0 by u/<your_username>
```
Then use `agents/reddit_agent.py` instead (see top of this workflow).
