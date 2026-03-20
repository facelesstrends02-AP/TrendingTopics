# Workflow 11: YouTube SEO Optimization

## Objective
Optimize a YouTube video's title, description, and tags for maximum discoverability on both
YouTube search and Google. Generates semantic keywords, chapter markers, and related video topics,
then applies the updated metadata directly to the video via the YouTube Data API.

## When to Run
- **Automatically**: Runs as Step H in `production_agent.py` for every produced video (right after thumbnail upload)
- **Manually**: To retroactively optimize any existing video uploaded before this feature existed
- **Standalone**: To preview/audit SEO metadata without updating YouTube

## Agent
`agents/seo_agent.py`

## Tools Used
1. `tools/generate_seo_metadata.py` — Claude Sonnet SEO specialist prompt + optional YouTube API update

## Required Inputs
- `ANTHROPIC_API_KEY` in `.env`
- `CHANNEL_NAME` in `.env`
- `APPROVAL_EMAIL` in `.env`
- OAuth `token.json` (for YouTube API update)
- A script JSON file (the video's script from production)

## Usage Examples
```bash
# Optimize and update a specific YouTube video
python3 agents/seo_agent.py \
  --video-id abc123xyz \
  --script-file .tmp/scripts/video_1_script.json

# Preview SEO data without updating YouTube (dry run)
python3 agents/seo_agent.py \
  --script-file .tmp/scripts/video_1_script.json \
  --dry-run

# Override channel name
python3 agents/seo_agent.py \
  --video-id abc123xyz \
  --script-file .tmp/scripts/video_1_script.json \
  --channel-name "The Pulse"

# Custom output path
python3 agents/seo_agent.py \
  --video-id abc123xyz \
  --script-file .tmp/scripts/video_1_script.json \
  --output .tmp/seo/custom_seo.json
```

## Output
- `.tmp/seo/{video_key}_seo.json` — full SEO metadata
- YouTube video metadata updated (if `--video-id` provided and not `--dry-run`)
- Email sent to `APPROVAL_EMAIL` with SEO summary

## Output Schema
```json
{
  "original_title": "5 Morning Habits That Changed My Life",
  "seo_title": "5 Morning Habits That Changed My Life (Science-Backed)",
  "description": "150-250 word SEO description with timestamps, keywords, CTA, hashtags",
  "semantic_keywords": ["morning routine productivity", "daily habits successful people", ...],
  "tags": ["morning routine", "self development", "productive morning", ...],
  "chapter_markers": [
    {"timestamp": "0:00", "title": "Introduction"},
    {"timestamp": "0:15", "title": "Habit 1: Hydration Before Coffee"},
    ...
  ],
  "related_video_topics": [
    "how to be a morning person",
    "evening routine for better sleep",
    ...
  ],
  "search_phrases": [
    "morning habits successful people",
    "how to build a morning routine",
    ...
  ],
  "updated_youtube": true,
  "generated_at": "2026-03-12T10:30:00Z"
}
```

## How It Integrates with Production Pipeline
In `production_agent.py`, Step H runs after every successful thumbnail upload:

```
Step E: upload_to_youtube.py → video_id
Step F: generate_thumbnail.py
Step G: upload_thumbnail.py
Step H: generate_seo_metadata.py --video-id {video_id} --update-youtube  ← automatic
```

Step H is **non-fatal**: if it fails (API quota, network issue), production continues and you'll see
a WARNING in the logs. The video is still uploaded and reviewed as normal.

## YouTube API: What Gets Updated
The `videos.update()` call patches the video's `snippet`:
- `title` → SEO-optimized title (max 100 chars)
- `description` → SEO description (max 5000 chars)
- `tags` → optimized tag list
- `categoryId` → preserved from original script (typically 26 = Howto & Style)

## API Quota Cost
- `videos.list` (to read current snippet): 1 unit
- `videos.update`: 50 units
- **Total per video: ~51 units** (daily quota: 10,000 units)

At 3 videos/week, this adds 153 units/week — well within limits.

## Edge Cases
- **Video still processing**: YouTube API may reject `videos.update()` if the video is still processing.
  Production_agent runs SEO immediately after upload, so this can occasionally fail.
  Use `seo_agent.py --video-id` manually 5-10 minutes after upload if this happens.
- **Quota exceeded**: If YouTube quota is exhausted, Step H fails (non-fatal). Run `seo_agent.py` manually the next day.
- **No script file**: SEO agent requires a script file. For manually uploaded videos, create a minimal script JSON with `title` and `segments`.
- **token.json expired**: Run `bash setup.sh` to refresh OAuth token.

## Retroactively Optimizing Old Videos
To optimize videos uploaded before this workflow existed:
```bash
# 1. Find the video's script (if it exists)
ls .tmp/scripts/

# 2. Run seo_agent.py with the video's YouTube ID
python3 agents/seo_agent.py --video-id YOUR_VIDEO_ID --script-file .tmp/scripts/video_N_script.json
```

If the script no longer exists, create a minimal one:
```json
{
  "title": "Your Video Title",
  "description": "",
  "tags": [],
  "category_id": "26",
  "segments": [{"text": "your video content summary", "duration_estimate": 120}]
}
```
