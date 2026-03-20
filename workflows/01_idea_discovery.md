# Workflow: Idea Discovery

## Objective
Every Sunday night, aggregate trending topics from multiple sources, use Claude Sonnet to generate 10 high-potential video ideas, write them to Google Sheet, and email for approval.

## Trigger
- **Cron:** `0 22 * * 0` (Sunday 10pm local time)
- **Command:** `python3 agents/idea_agent.py`

## Required Inputs
- `ANTHROPIC_API_KEY` — Claude Sonnet for idea generation
- `YOUTUBE_API_KEY` — YouTube trending as one source
- `APPROVAL_EMAIL` — where to send the approval request
- `CHANNEL_NAME` — your YouTube channel name (set in `.env`)
- `NEWSAPI_KEY` — optional, free tier (100 req/day at newsapi.org); improves trending quality

## Steps

### Step 0a: Channel Strategy Check (non-fatal)
- If `channel_strategy.json` doesn't exist, run `channel_strategy_agent.py` to generate it
- Strategy is injected into the prompt for better idea alignment
- Non-fatal: if generation fails, proceed without it

### Step 0: Load Analytics Context (non-fatal)
- Tool: `tools/load_analytics_context.py`
- Reads `.tmp/analytics_insights.json` from last Sunday's analytics run
- Returns a text block with top performers, underperformers, and content patterns
- Non-fatal: returns empty string if file missing (first run safe)

### Step 1: Aggregate Trending Topics
- Tool: `tools/scrape_trending_topics.py --max-results 50`
- Output: `.tmp/trending_topics.json`
- Sources combined:
  1. **Google Trends** (`pytrends`, weight 1.5) — real-time trending searches, no API key
  2. **RSS news feeds** (feedparser, weight 1.0) — BBC, Reuters, AP News, NPR, Al Jazeera
  3. **Reddit hot posts** (JSON endpoint, weight 1.1) — r/worldnews, r/technology, r/finance, r/science
  4. **YouTube trending** (weight 0.8) — delegates to `scrape_youtube_trending.py`
  5. **NewsAPI** (weight 1.2, optional) — top headlines if `NEWSAPI_KEY` set
- Deduplication: Jaccard n-gram similarity (threshold 0.20) groups same-event signals
- Scoring: `weight × source_diversity × recency_multiplier` (recency: 1.5x if <6h, 1.2x if <24h)
- **Hard failure:** if ALL sources fail, log error, send error email, exit 1
- **Partial failure:** each source wrapped in try/except — at least one must succeed

### Step 2: Generate Ideas
- Tool: `tools/generate_viral_ideas.py --trending-file .tmp/trending_topics.json --channel-name $CHANNEL_NAME --count 10`
- Model: Claude Sonnet (`claude-sonnet-4-6`)
- Output: `.tmp/ideas.json`
- Idea schema:
  - `id`, `title`, `hook`, `angle`, `category` (politics/tech/finance/science/sports/entertainment/world)
  - `target_emotion`, `urgency` (high/medium/low), `controversy_level` (low/medium/high)
  - `potential` (High/Medium/Low), `content_format`
  - `pexels_search_query`, `news_search_query` (for news image fetching in production)
  - `viral_reason`
- Retry: attempts prompt twice on JSON parse failure
- **Hard failure:** if both attempts fail, log error, send error email, exit 1

### Step 3: Write to Google Sheet
- Tool: `tools/write_ideas_to_sheet.py --ideas-file .tmp/ideas.json`
- Creates workbook named `{CHANNEL_NAME} - {YEAR}` on first run
- Monthly tabs: `Ideas - Jan`, `Ideas - Feb`, etc.
- Appends weekly section with headers including Category, Urgency, News Query
- Color-codes Potential column (High=green, Medium=yellow, Low=red)
- Saves sheet ID back to `GOOGLE_SHEET_ID` in `.env` for reuse
- **Soft failure:** if sheet write fails, email includes path to local ideas.json instead

### Step 4: Send Approval Email
- Tool: `tools/send_email.py`
- To: `APPROVAL_EMAIL`
- Subject: `[YT Automation] 10 Ideas Ready - Week of {date} - Reply to Approve`
- Body: idea list with titles + hooks + potential ratings, sheet link, approval instructions
- Approval formats accepted:
  - `APPROVE: 1, 3, 7` — specific IDs
  - `APPROVE ALL` — all 10
  - `APPROVE: 1-5` — range
  - `APPROVE ALL EXCEPT: 4, 9` — exclusion
- Saves Gmail message ID to state for thread polling

### Step 5: Update State
- Sets phase to `awaiting_idea_approval`
- Saves: week, sheet_id, sheet_url, ideas_email_message_id, ideas_email_sent_at

## Output
- `.tmp/trending_topics.json` — 50 ranked trending topics
- `.tmp/ideas.json` — 10 video ideas with full metadata
- Google Sheet — ideas written with color-coded formatting
- Approval email sent to `APPROVAL_EMAIL`
- State phase: `awaiting_idea_approval`

## Edge Cases
- **Google Trends 429:** pytrends backs off automatically (retries=2, backoff_factor=0.5)
- **RSS feed down:** logged and skipped, other sources continue
- **Reddit 429:** User-Agent header required; backed off between subreddit requests (0.5s)
- **NewsAPI 100 req/day limit:** only called once per weekly run (~1 request)
- **All sources fail:** exits 1, sends error email
- **Claude JSON parse fails twice:** exits 1, sends error email
- **Google Sheets auth expired:** `token.json` auto-refreshed by Google client library
- **No analytics context:** treated as first run, idea generation proceeds without it

## Cost Per Run
| Component | Cost |
|-----------|------|
| Claude Sonnet (10 ideas) | ~$0.03 |
| YouTube API | Free (150-300 units) |
| Google Trends | Free |
| RSS feeds | Free |
| Reddit | Free |
| NewsAPI (optional) | Free (1 request) |
| **Total** | **~$0.03/week** |

## Cron Schedule
```bash
0 21 * * 0   python3 agents/analytics_agent.py   # Sunday 9pm — must run BEFORE ideas
0 22 * * 0   python3 agents/idea_agent.py         # Sunday 10pm
*/30 * * * * python3 agents/approval_poller.py   # Every 30 min (all week)
```
