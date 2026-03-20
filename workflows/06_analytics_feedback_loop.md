# Workflow 06: Analytics & Feedback Loop

## Objective
Every Sunday at 9pm, fetch performance data for all published videos, analyze trends with Claude Haiku, and feed insights into the idea generation process (which runs 1 hour later at 10pm). Send a weekly analytics email to the user.

## Cron Schedule (Updated)

```bash
# Add to crontab -e:
0 21 * * 0   cd /path/to/LearningAgentic && python3 agents/analytics_agent.py >> .tmp/cron.log 2>&1
0 22 * * 0   cd /path/to/LearningAgentic && python3 agents/idea_agent.py >> .tmp/cron.log 2>&1
*/30 * * * * cd /path/to/LearningAgentic && python3 agents/approval_poller.py >> .tmp/cron.log 2>&1
```

The 1-hour gap between analytics (9pm) and idea generation (10pm) ensures insights are ready before `idea_agent.py` runs.

## Data Flow

```
[Sunday 9pm] analytics_agent.py
    ↓
    reads .tmp/published_videos_registry.json
    (persistent, append-only — survives weekly state resets)
    ↓
    tools/fetch_video_analytics.py
    → YouTube Data API v3 videos.list
    → .tmp/analytics_YYYY-MM-DD.json
    ↓
    tools/analyze_performance.py
    → Claude Haiku (~$0.001/run)
    → .tmp/analytics_insights.json
    ↓
    tools/write_analytics_to_sheet.py
    → "Analytics" tab in GOOGLE_SHEET_ID spreadsheet
    ↓
    tools/send_email.py
    → Weekly analytics summary email to user

[Sunday 10pm] idea_agent.py
    ↓
    tools/load_analytics_context.py
    → reads .tmp/analytics_insights.json
    → formats as plain-text context block
    ↓
    tools/generate_ideas.py --analytics-context "..."
    → Claude Haiku uses both external trends AND internal performance data
    → Ideas biased toward what actually worked on this channel
```

## Key Design: Published Videos Registry

`publisher_agent.py` appends to `.tmp/published_videos_registry.json` every time a video is made public. This file:
- **Persists across weeks** (unlike `state.json` which resets every Sunday)
- **Accumulates all published video IDs** since the channel started
- **Is the analytics source of truth** for which videos to fetch stats for

Format:
```json
[
  {
    "youtube_video_id": "abc123",
    "title": "7 Habits of Highly Disciplined People",
    "published_at": "2026-02-15T18:30:00+00:00",
    "week": "2026-02-09",
    "public_url": "https://youtube.com/watch?v=abc123"
  }
]
```

## Tools Used

### `tools/fetch_video_analytics.py`
- Calls `youtube.videos().list(part="statistics,snippet", id=...)`
- Batches up to 50 IDs per request (well within quota)
- Computes `engagement_rate = (likes + comments) / views`
- Quota: ~100 units per call (vs. 10,000 daily limit)
- **No new API needed** — uses same YouTube Data API v3 already configured

### `tools/analyze_performance.py`
- Sends analytics JSON to Claude Haiku
- Outputs structured JSON with: `top_performers`, `underperformers`, `patterns`, `double_down_topics`, `avoid_topics`, `content_recommendations`, `insights_summary`
- Cost: ~$0.001 per run (Haiku is cheap for summarization tasks)
- Handles edge cases: 0 videos → empty insights, 1 video → still produces useful output

### `tools/write_analytics_to_sheet.py`
- Finds or creates "Analytics" tab in existing `GOOGLE_SHEET_ID` spreadsheet
- Appends weekly rows: Week | Title | Video ID | Published | Views | Likes | Comments | Engagement Rate
- Color-codes engagement rate: green (≥3%), yellow (1.5-3%), red (<1.5%)
- Appends insights summary block below data rows

### `tools/load_analytics_context.py`
- Reads `.tmp/analytics_insights.json`
- Formats as human-readable text block for prompt injection
- Returns empty string if no data exists → `generate_ideas.py` skips the analytics section entirely
- **First-run safe**: no errors if file is missing

## Google Sheet Structure

The existing spreadsheet (same `GOOGLE_SHEET_ID`) gets a new "Analytics" tab:

| Week | Title | Video ID | Published | Views | Likes | Comments | Engagement Rate | Fetched At |
|------|-------|----------|-----------|-------|-------|----------|-----------------|------------|
| 2026-03-09 | 7 Habits of... | abc123 | 2026-02-15 | 45,000 | 1,800 | 90 | 4.20% | 2026-03-09T21:05Z |

Followed by an insights summary block:
```
--- INSIGHTS for week 2026-03-09 ---
Videos analyzed: 3
Summary: Discipline and numbered lists consistently outperform...
Double down on: Discipline topics | Numbered list titles
Avoid: Morning routines | Generic motivation
Pattern: Numbered titles average 2.3x more views
```

## How Feedback Influences Idea Generation

The analytics context is injected into the Claude Haiku prompt inside `generate_ideas.py` between the trending videos list and the generation instructions:

```
TOP TRENDING VIDEOS:
1. "7 Signs You Have High Emotional..." — 2.3M views
...

PAST PERFORMANCE INSIGHTS FROM THIS CHANNEL (last 4 weeks):
TOP PERFORMING VIDEOS (replicate these patterns):
  - "7 Habits of Highly Disciplined People" — 45,000 views, 4.2% engagement
    Why it worked: Numbered list + discipline angle beats generic motivation

UNDERPERFORMING VIDEOS (avoid these patterns):
  - "Morning Routine for Success" — 8,000 views
    Why it flopped: Oversaturated angle, generic title

OBSERVED PATTERNS:
  - Numbered list titles average 2.3x more views
  - "Discipline" topics outperform "Motivation" by 40%

DOUBLE DOWN ON:
  - Discipline/focus over motivation
  - Specific techniques with names

AVOID OR REFRAME:
  - Morning routines (saturated)
  - Generic confidence content

Apply these insights: double down on what worked, avoid what flopped...

Based on this analysis, generate 10 ORIGINAL video ideas...
```

This gives Claude both **external market signals** (what's trending) and **internal channel signals** (what actually worked on this specific channel), which is the ideal input for idea generation.

## Why YouTube Data API v3 (Not YouTube Analytics API)

The **YouTube Analytics API** provides richer data (watch time, CTR, impressions, audience retention), but requires:
- Enabling a separate API in Google Cloud Console
- A different OAuth scope (`https://www.googleapis.com/auth/yt-analytics.readonly`)
- Re-running the OAuth flow

The **YouTube Data API v3** (`videos.list`) gives us views, likes, and comments with:
- No extra setup — same API already configured
- Same OAuth token
- Sufficient signal for content strategy decisions

**Future upgrade path:** Once the channel grows and watch time/CTR become more important metrics, add the YouTube Analytics API scope to `setup.sh` SCOPES list and create a new tool `tools/fetch_youtube_analytics_api.py` to fetch richer data.

## First-Run Behavior

On first run (before any videos are published):
1. `analytics_agent.py` detects empty registry → sends "no data yet" email → exits cleanly
2. `idea_agent.py` runs `load_analytics_context.py` → gets empty string → runs `generate_ideas.py` without analytics context
3. Everything works normally, just without the feedback boost

After the first videos are published, the feedback loop activates automatically on the next Sunday cycle.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| No published videos | analytics_agent exits early, sends notice email |
| YouTube API quota exceeded | Error logged, analytics skipped for this week |
| Claude Haiku fails | Analytics written without insights section |
| Google Sheets write fails | Analytics saved locally, sheet update skipped |
| `load_analytics_context.py` fails | idea_agent continues without context (non-fatal) |
| insights_insights.json corrupt | load_analytics_context returns empty string |

## Cost

| Component | Monthly cost |
|-----------|-------------|
| YouTube Data API v3 `videos.list` | Free (~400 units/month) |
| Claude Haiku analysis | ~$0.004/month |
| Google Sheets API | Free |
| Gmail API (analytics email) | Free |
| **Total** | **~$0.01/month** |
