# Workflow: Content Production Pipeline

## Objective

For each approved video idea, generate a complete script, AI voiceover, fetch stock footage, assemble the final video, and upload it to YouTube as unlisted for review.

## Required Inputs

- `state.approved_idea_ids` — list of approved idea numbers (from state.json)
- `.tmp/ideas.json` — the ideas file from this week's run
- `ANTHROPIC_API_KEY` — Claude Sonnet for script generation
- `OPENAI_API_KEY` — OpenAI TTS for voiceover
- `PEXELS_API_KEY` — primary stock footage source
- `PIXABAY_API_KEY` — optional secondary stock footage source (free key at pixabay.com/api)
- `credentials.json` + `token.json` — YouTube OAuth for upload

## Tools Used (per video)

1. `tools/generate_retention_script.py --idea-id N --ideas-file .tmp/ideas.json --output .tmp/scripts/video_N_script.json`
2. `tools/generate_voiceover.py --script-file ... --output .tmp/audio/video_N_voiceover.mp3`
3. `tools/fetch_mixed_footage.py --script-file ... --output-dir .tmp/footage/video_N/`
4. `tools/assemble_video.py --script-file ... --audio-file ... --footage-dir ... --output .tmp/output/video_N_final.mp4`
5. `tools/upload_to_youtube.py --video-file ... --script-file ... --privacy unlisted`

After all videos:

6. `tools/send_email.py` (review email with unlisted links)
7. `tools/manage_state.py --set-phase awaiting_video_approval`

## Steps (executed by `agents/production_agent.py`)

1. Load state, get `approved_idea_ids`
2. Set phase to `production_in_progress`
3. For each approved idea:
   - **Script**: Claude Sonnet generates 8-10 minute segmented script
     - Each segment has: text, visual_cue, overlay_text, pexels_search_queries (array of 3), news_search_query, duration_estimate
     - Structure: Hook → Bridge → Context → 4 Points (with pattern interrupts between) → Engagement → CTA
     - Category-aware framing: politics/tech/finance/science/sports/entertainment/world each have distinct guidance
     - `news_search_query` per segment is a specific dateable query for finding relevant news article images
     - `pexels_search_queries` has 3 varied B-roll queries per segment (specific → complementary → broad fallback)
   - **Voiceover**: OpenAI TTS `tts-1` model
     - Splits long text at sentence boundaries (4096 char API limit)
     - Concatenates chunks with pydub
     - Actual duration written to state
   - **Footage**: Mixed — news images + Pexels stock (`fetch_mixed_footage.py`)
     - **Priority per segment:**
       1. News image: scrapes `og:image` from a news article matching `news_search_query` → `clip_NNN_news.jpg`
       2. Video clips (per query, waterfall across sources):
          - Original query → Pexels
          - Original query → Pixabay (if `PIXABAY_API_KEY` set)
          - Simplified query → Pexels → Pixabay (only if original failed on both)
          - Channel name → Pexels → Pixabay (last resort)
       3. Pexels photo fallback (if no video found at all) → `clip_NNN_photo.jpg`
       4. Empty (non-fatal) — segment skipped in assembly
     - News image scraping: NewsAPI search (if key set) or Google News RSS → article → `og:image` meta tag
     - Minimum 800px width validated on downloaded images
     - Manifest contains mix of `.mp4` and `.jpg` filenames
   - **Assembly**: moviepy renders final video
     - 1920x1080, 30fps, H.264/AAC
     - **Video clips:** rapid cuts every 3–6 seconds with Ken Burns zoom alternating direction
     - **Still images (.jpg):** rendered as full-segment ImageClip with slow Ken Burns zoom (news photos get held for their full segment duration)
     - Hard cuts between all sub-clips (no cross-fade)
     - Text overlays span the full segment duration regardless of media type
     - Optional background music at 10% volume
     - Render time: ~8-15 minutes per 10-min video
   - **Upload**: YouTube resumable upload, unlisted privacy
     - Metadata from script: title, description, tags, category
4. Update state after each video (incremental saves)
5. Compose review email with all unlisted YouTube links
6. Send email, save message ID to state
7. Set phase to `awaiting_video_approval`

## Expected Outputs

- `.tmp/scripts/video_N_script.json` — script per video
- `.tmp/audio/video_N_voiceover.mp3` — voiceover per video
- `.tmp/footage/video_N/` — downloaded stock clips + manifest
- `.tmp/output/video_N_final.mp4` — final rendered video
- YouTube videos uploaded as unlisted
- Review email sent with unlisted links

## Cost Per Run (3 videos)

| Step | Model/API | Est. Cost |
|---|---|---|
| Script (3x) | Claude Sonnet | ~$0.15 |
| Voiceover (3x ~9k chars) | OpenAI TTS `tts-1` | ~$0.40 |
| Footage | Pexels + Pixabay (both free) + news images (free) | $0 |
| Assembly | local CPU | $0 |
| Upload | YouTube API | $0 |
| **Total per week** | | **~$0.55** |

## Triggered By

`agents/approval_poller.py` when `phase = awaiting_idea_approval` and a valid approval reply is found.

## Edge Cases & Notes

**moviepy out of memory on long videos:**
- If video is 15+ segments, process in batches of 5 segments, render partial files, then concatenate
- Add `--batch-size 5` argument to `assemble_video.py` if OOM errors occur
- Monitor: `Activity Monitor` during render

**News image not found:**
- `fetch_mixed_footage.py` falls back gracefully: news image → Pexels video → Pexels photo
- If news image scraping fails (site blocks bots, no og:image), Pexels video is used instead
- To manually override: place a `.jpg` file at `.tmp/footage/video_N/clip_NNN_news.jpg` before re-running assembly

**Footage irrelevant or missing:**
- Script generation prompts Claude for concrete specific queries per segment
- "capitol building congress crowd protest" not "politics"
- Each query is tried on Pexels first, then Pixabay; simplified query only if both fail
- If `PIXABAY_API_KEY` is not set, Pixabay steps are silently skipped
- If still failing: edit the `pexels_search_queries` arrays in `.tmp/scripts/video_N_script.json` manually and re-run `fetch_mixed_footage.py`
- Old scripts with `pexels_search_query` (single string) still work — backward compatible

**OpenAI TTS character limit:**
- API limit: 4096 chars per request
- `generate_voiceover.py` handles splitting automatically at sentence boundaries
- If pydub throws an error: `brew install ffmpeg` and ensure PATH is set

**YouTube upload quota:**
- Each upload costs 1600 API units
- Daily limit: 10,000 units
- 3 uploads per day = 4800 units — well within limit
- If quota exceeded: error message says to wait 24h; quota resets midnight Pacific

**YouTube video still processing:**
- `publish_youtube_video.py` waits up to 10 minutes for processing before publishing
- If video is still processing after 10 min, it attempts publish anyway (YouTube usually handles it)

**Partial failure (some videos fail, some succeed):**
- `production_agent.py` continues to next video on failure
- Failed video IDs are logged in state.errors
- Review email is still sent for successfully produced videos
- Failed videos noted in email body

**Resume after crash:**
- Each tool checks if its output file already exists and skips if present
- `fetch_pexels_footage.py` skips already-downloaded clips
- Re-run `production_agent.py` to resume from where it crashed
