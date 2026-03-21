# Workflow 12: YouTube Shorts Pipeline

## Objective

For each publishing day, automatically generate 2 YouTube Shorts on **independent trending topics** — completely decoupled from the week's full video — and schedule them for the day after the main video goes live. This maximises viral potential by targeting whatever is trending at trigger time, not just the topic of the long-form video.

---

## Trigger

`agents/shorts_scheduler.py` runs on a cron schedule. It checks `state.json` for any video whose `scheduled_publish_at` date matches **today** and hasn't already had shorts triggered (`shorts_scheduling_triggered != true`).

**Cron entry** (Mon/Wed/Fri 23:00 IST = 17:30 UTC — 2 hours after the full video's 9pm IST publish):
```
30 17 * * 1,3,5   cd /path/to/TrendingTopics && python agents/shorts_scheduler.py >> .tmp/cron.log 2>&1
```

---

## Schedule Formula

| Full video published | Short 0 | Short 1 |
|---|---|---|
| Monday | Tuesday 7:00am IST (01:30 UTC) | Tuesday 7:00pm IST (13:30 UTC) |
| Wednesday | Thursday 7:00am IST (01:30 UTC) | Thursday 7:00pm IST (13:30 UTC) |
| Friday | Saturday 7:00am IST (01:30 UTC) | Saturday 7:00pm IST (13:30 UTC) |

IST to UTC: subtract 5 hours 30 minutes.

---

## Tools Used (in order, per video key)

```
shorts_scheduler.py
  └── shorts_agent.py --video-key video_1
        ├── tools/scrape_trending_topics.py    # Refresh trending cache if > 6h old (free)
        ├── tools/generate_short_ideas.py      # Claude: 2 fresh Short ideas from trending topics
        ├── tools/generate_short_scripts.py    # Claude: 2 Short scripts from ideas (--ideas-file)
        ├── For each of 2 shorts:
        │   ├── tools/generate_voiceover.py    # OpenAI TTS → MP3
        │   ├── tools/assemble_short.py        # Pure ffmpeg → 1080×1920 portrait MP4
        │   ├── tools/upload_to_youtube.py     # Upload as private
        │   ├── tools/publish_youtube_video.py # Schedule publish time
        │   └── (state update per short)
        └── tools/send_email.py                # Notification email with both URLs
```

---

## Source Selection

`generate_short_ideas.py` reads `trending_topics.json` (multi-source: Google Trends, NewsAPI, Reddit, RSS) and calls Claude to pick 2 topics best suited for the 45-60 second Shorts format — a single punchy fact/revelation, not a multi-point breakdown. The 2 ideas are chosen from different categories for variety.

`generate_short_scripts.py` is called with `--ideas-file` (independent mode), passing the 2 Short ideas directly instead of filtering point_N segments from the full video script.

**Trending cache**: `shorts_agent.py` reuses `.tmp/trending_topics.json` if it is less than 6 hours old (Sunday's weekly scrape typically covers Mon/Wed/Fri trigger times). A fresh scrape runs automatically if the cache is stale.

---

## Output Format

- **Resolution:** 1080 × 1920 (9:16 portrait, YouTube Shorts native)
- **Codec:** H.264 / AAC
- **Max duration:** 60 seconds (trimmed at 60s if audio is longer)
- **Frame rate:** 30fps

### Visual layers (bottom to top):
1. B-roll clips (portrait from Pexels, or landscape converted to pillarbox)
2. Per-sentence captions — centered, bottom third, white text with black stroke, rotating colors: Gold, Coral, Teal, Lime, Orange
3. Hook overlay — yellow ALL CAPS text, upper quarter, shown for first 3.5 seconds
4. CTA overlay — white text on dark band, bottom, shown for last 8 seconds

---

## How Per-Sentence Captions Work

1. `spoken_script` is split into individual sentences (split on `.`, `!`, `?`)
2. Each sentence's duration is allocated proportionally by word count: `duration_i = (words_i / total_words) * total_audio_duration`
3. Each sentence gets its own B-roll clip trimmed to that duration (clips cycle from the Pexels pool, looped if too short)
4. During the final ffmpeg pass, `drawtext` filter with `enable='between(t,start,end)'` burns each sentence into its time window
5. Clips switch precisely at sentence boundaries — each cut = one new thought

This approach gives natural-feeling cuts that match the narration rhythm, rather than arbitrary time-based cuts.

---

## State Schema

**Per short** (`state.videos.{video_key}.shorts.short_{0,1}`):
```json
{
  "short_title": "BREAKING: Event Explained in 60 Seconds #Shorts",
  "youtube_video_id": "abc123",
  "youtube_url": "https://www.youtube.com/watch?v=abc123",
  "scheduled_publish_at": "2026-03-17T01:30:00Z",
  "audio_path": ".tmp/audio/video_1_short_0.mp3",
  "video_path": ".tmp/shorts/video_1_short_0.mp4",
  "status": "scheduled"
}
```

**Per video** (added by shorts pipeline):
- `shorts_scheduling_triggered: true` — set by `shorts_scheduler.py` to prevent double-launching
- `shorts.short_0`, `shorts.short_1` — set by `shorts_agent.py` after each short completes

---

## YouTube API Quota

| Operation | Units |
|---|---|
| Upload video × 2 | 3,200 (1,600 each) |
| Schedule publish × 2 | 100 |
| **Total per video** | **~3,300** |

Daily quota: 10,000 units. Max 3 full-video uploads + 2 Shorts per day safely within quota.

---

## Edge Cases

### No Pexels clips found for any query
`assemble_short.py` falls back to a plain black background. The narration and overlays still render correctly. The Short will be text/audio only — acceptable for news content.

### One short fails, the other succeeds
`shorts_agent.py` wraps each short in a `try/except`. A failure on `short_0` logs the error to state and continues to produce `short_1`. The partial result is still uploaded and scheduled.

### Audio longer than 60 seconds
The final ffmpeg command uses `-t {min(audio_dur, 60)}` to hard-cap at 60s. Generate shorter scripts (90–115 words target) to avoid this. Check: `ffprobe -v quiet -show_format {audio_path} | grep duration`.

### ffmpeg not installed
`assemble_short.py` will raise `FileNotFoundError` on the first `subprocess.run` call. Install with: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Linux).

### Video not yet published when scheduler runs
`shorts_scheduler.py` skips videos with no `youtube_video_id`. The scheduler runs 2 hours after the scheduled publish time, by which point the upload should be complete. If the upload was delayed, re-run manually.

### Font download fails (no internet / GitHub rate limit)
`ensure_font()` in `assemble_short.py` tries system font fallbacks (`Futura.ttc`, `AvenirNext.ttc`, `Arial Bold.ttf`, `DejaVuSans-Bold.ttf`). If all fallbacks are absent, ffmpeg uses its built-in default font — captions still render but may look different.

---

## Manual Operations

### Re-trigger shorts for a specific video
```bash
python agents/shorts_agent.py --video-key video_1
```
The agent is **idempotent**: it skips shorts already in `status: scheduled` and resumes from where it left off (e.g. if voiceover was generated but assembly failed).

### Reset and regenerate shorts plan only
```bash
rm .tmp/shorts/video_1_short_ideas.json .tmp/shorts/video_1_shorts_plan.json
python agents/shorts_agent.py --video-key video_1
```

### Generate Short ideas standalone (for testing)
```bash
python tools/generate_short_ideas.py \
  --trending-file .tmp/trending_topics.json \
  --output .tmp/shorts/video_1_short_ideas.json
```

### Generate Short scripts standalone from ideas (no upload)
```bash
python tools/generate_short_scripts.py \
  --ideas-file .tmp/shorts/video_1_short_ideas.json \
  --output .tmp/shorts/video_1_shorts_plan.json
```

### Generate Short scripts standalone from full video script (original mode, still supported)
```bash
python tools/generate_short_scripts.py \
  --script-path .tmp/scripts/video_1_script.json \
  --output .tmp/shorts/video_1_shorts_plan.json
```

### Assemble a short standalone (for testing)
```bash
python tools/assemble_short.py \
  --script-path .tmp/scripts/video_1_script.json \
  --audio-path .tmp/audio/video_1_short_0.mp3 \
  --output-path .tmp/shorts/video_1_short_0_test.mp4 \
  --hook-overlay "BREAKING: THIS CHANGES EVERYTHING" \
  --cta-overlay "FOLLOW FOR UPDATES" \
  --spoken-script "Here is what happened today..." \
  --pexels-queries '["breaking news protest", "world leaders meeting", "newspaper headline"]'
```

### Verify output dimensions and duration
```bash
ffprobe -v quiet -print_format json -show_streams .tmp/shorts/video_1_short_0.mp4 \
  | python -c "import json,sys; s=json.load(sys.stdin)['streams'][0]; print(s['width'], s['height'], s.get('duration','?'))"
```
Expected: `1080 1920 <≤60>`

---

## Cost Estimate (per publishing day)

| Component | Cost |
|---|---|
| Trending scrape (free APIs) | $0 |
| Claude Sonnet (generate_short_ideas.py) | ~$0.003 |
| Claude Sonnet (generate_short_scripts.py) | ~$0.003 |
| OpenAI TTS × 2 (≈200 words each) | ~$0.008 |
| Pexels / YouTube API | $0 |
| **Total per publishing day** | **~$0.014** |
| **Per week (3 days)** | **~$0.042** |
