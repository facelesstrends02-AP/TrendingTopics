# TrendingTopics

An automated faceless YouTube channel pipeline built on the **WAT framework** (Workflows, Agents, Tools). The system runs end-to-end on a weekly schedule — from idea discovery to publishing — with human approval checkpoints via email.

---

## How It Works

The pipeline runs every Sunday and produces 3 videos per week with minimal manual input:

1. **Sunday 9pm** — Analytics agent fetches YouTube stats, generates insights, writes to Google Sheets, emails summary
2. **Sunday 10pm** — Idea agent scrapes trending YouTube content, generates 10 ideas informed by past performance, writes to Google Sheets, emails you the list
3. **You reply** `APPROVE: 1, 3, 7` — Approval poller detects your reply and queues selected ideas for production
4. **Production agent** — Generates script (Claude Sonnet) → voiceover (OpenAI TTS) → footage (Pexels) → assembles video (moviepy) → uploads unlisted to YouTube → generates thumbnail → emails you the preview link
5. **You reply** `APPROVE ALL` — Publisher agent makes videos public, logs them to the registry, done

---

## YouTube Shorts Pipeline

For every published long-form video, the system automatically generates **2 YouTube Shorts** derived from its strongest news segments, scheduled for the day after the main video goes live.

### How it works

1. **Shorts scheduler** (`agents/shorts_scheduler.py`) runs Mon/Wed/Fri at 11pm IST — 2 hours after the full video publishes
2. **Shorts agent** (`agents/shorts_agent.py`) picks 2 `point_N` segments from the full video script (spread across the video for topic variety)
3. **Per short:** generates a condensed script (Claude Sonnet) → voiceover (OpenAI TTS) → assembles portrait video (ffmpeg) → uploads to YouTube → schedules publish
4. **Email notification** with both Short URLs is sent on completion

### Output format

- **Resolution:** 1080 × 1920 (9:16 portrait, YouTube Shorts native)
- **Duration:** ≤ 60 seconds
- **Visual layers:** B-roll footage → per-sentence captions (rotating colors) → hook overlay (first 3.5s) → CTA overlay (last 8s)

### Schedule

| Full video published | Short 0 | Short 1 |
|---|---|---|
| Monday | Tuesday 7:00am IST | Tuesday 7:00pm IST |
| Wednesday | Thursday 7:00am IST | Thursday 7:00pm IST |
| Friday | Saturday 7:00am IST | Saturday 7:00pm IST |

### Cost

~$0.01 per full video (2 Shorts combined) — Claude Sonnet script + OpenAI TTS × 2.

---

## Architecture: WAT Framework

```
Workflows  →  Agents  →  Tools
(What to do)  (Decisions)  (Execution)
```

- **Workflows** (`workflows/`) — Markdown SOPs defining objectives, inputs, outputs, and edge cases
- **Agents** (`agents/`) — Orchestrators that read workflows and call tools in sequence
- **Tools** (`tools/`) — Deterministic Python scripts for API calls, file ops, data transforms

This separation keeps AI focused on reasoning while deterministic code handles execution.

---

## Setup

### Prerequisites
- macOS (uses Homebrew for ffmpeg)
- Python 3.10+
- A YouTube channel
- API keys for: Anthropic, OpenAI, YouTube Data API v3, Pexels
- A Gmail account with OAuth enabled

### 1. Clone the repo
```bash
git clone https://github.com/abhispandey/TrendingTopics.git
cd TrendingTopics
```

### 2. Configure environment
```bash
cp .env.example .env
# Fill in all API keys in .env
```

### 3. Add Google OAuth credentials
- Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
- Create an OAuth 2.0 Client ID (Desktop app)
- Download as `credentials.json` and place it in the project root
- Enable: YouTube Data API v3, Gmail API, Google Sheets API

### 4. Run setup
```bash
bash setup.sh
# This installs ffmpeg, creates venv, installs dependencies, and runs the OAuth flow
```

### 5. Set up cron jobs
```bash
crontab -e
```
Add:
```
0 21 * * 0    cd /path/to/TrendingTopics && .venv/bin/python agents/analytics_agent.py
0 22 * * 0    cd /path/to/TrendingTopics && .venv/bin/python agents/idea_agent.py
*/30 * * * *  cd /path/to/TrendingTopics && .venv/bin/python agents/approval_poller.py
30 17 * * 1,3,5  cd /path/to/TrendingTopics && .venv/bin/python agents/shorts_scheduler.py >> .tmp/cron.log 2>&1
```

---

## Project Structure

```
agents/          # 12 orchestrator agents (idea, production, publisher, analytics, shorts, etc.)
tools/           # 31 deterministic Python scripts
workflows/       # 13 Markdown SOPs
.env.example     # Template for required environment variables
requirements.txt # Python dependencies
setup.sh         # One-time setup script
```

---

## APIs Used

| Service | Purpose |
|---------|---------|
| Anthropic (Claude Haiku + Sonnet) | Idea generation, scripting, analytics insights |
| OpenAI TTS | Voiceover generation |
| YouTube Data API v3 | Trending scrape + video upload |
| Pexels | Stock footage + thumbnail photos |
| Gmail API | Sending summaries + parsing approval replies |
| Google Sheets API | Logging ideas, analytics, strategy |

---

## Cost

~$5–7/month for 3 videos/week (AI generation) + ~$0.01/month for analytics queries.

---

## State Machine

Videos move through these phases tracked in `.tmp/state.json`:

```
idle → ideas_generated → awaiting_idea_approval → production_queued
     → production_in_progress → awaiting_video_approval
     → publishing_queued → publishing_in_progress → completed
```
