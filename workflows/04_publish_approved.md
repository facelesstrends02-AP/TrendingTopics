# Workflow: Video Publishing

## Objective

For each approved video, change its YouTube privacy status from unlisted to public. Send a completion email with the public links.

## Required Inputs

- `state.approved_video_ids` — list of YouTube video IDs to publish
- `state.videos` — video metadata (title, youtube_video_id, youtube_url)
- `credentials.json` + `token.json` — YouTube OAuth

## Tools Used

1. `tools/publish_youtube_video.py --video-id <id>` (one per approved video)
2. `tools/manage_state.py --write` (update video status after each publish)
3. `tools/send_email.py` (completion notification)
4. `tools/manage_state.py --set-phase completed`

## Steps (executed by `agents/publisher_agent.py`)

1. Load state, get `approved_video_ids` and `videos` map
2. Set phase to `publishing_in_progress`
3. For each video in state.videos where `youtube_video_id` is in `approved_video_ids`:
   - Call `publish_youtube_video.py --video-id <id>`
   - Tool waits for YouTube processing to complete (up to 10 min, polls status every 30s)
   - Then calls `videos.update` to set `privacyStatus = "public"`
   - Update state: `videos[key].published = true`, `.public_url = ...`, `.published_at = ...`
4. Compose and send completion email listing all public URLs
5. Set phase to `completed`

## Expected Outputs

- YouTube videos are now publicly visible
- `state.phase = "completed"`
- Completion email sent with public video links

## Triggered By

`agents/approval_poller.py` when `phase = awaiting_video_approval` and a valid video approval reply is found.

## Edge Cases & Notes

**YouTube still processing the video:**
- `publish_youtube_video.py` polls `processingDetails.processingStatus` every 30 seconds
- Waits up to 10 minutes total
- If still not ready after 10 min: attempts publish anyway with `--skip-processing-check`
- YouTube typically processes within 2-5 minutes for short videos

**YouTube API quota for publish:**
- `videos.update` costs ~50 API units
- 3 publishes = ~150 units — well within daily limit

**Video deleted from YouTube before approval:**
- API will return 404 for the video ID
- `publish_youtube_video.py` exits with error, publisher_agent logs it and continues
- Completion email notes the failure

**Partial publish (some approved, some rejected):**
- publisher_agent only publishes videos whose IDs are in `approved_video_ids`
- Rejected video IDs remain as unlisted on YouTube (not deleted)
- User can delete them manually from YouTube Studio if desired

**Re-running publisher_agent:**
- If run again after already publishing some videos:
  - `publish_youtube_video.py` will call `videos.update` again (no-op if already public)
  - Safe to run multiple times

**Wrong YouTube channel:**
- The channel is determined by which Google account's OAuth is in `token.json`
- If uploading to the wrong channel: re-run `setup.sh` → sign in with the correct Google account

**Post-publish next steps:**
- Check YouTube Studio in 24-48h for: impressions, CTR, watch time
- Respond to comments in the first few hours (helps algorithm)
- Share to social media for initial traffic boost

**Weekly cadence:**
- After `phase = completed`, system idles until next Sunday
- State remains in `.tmp/state.json` for reference
- Next Sunday's `idea_agent.py` run does `--reset` which clears state for new week
- Tip: Don't delete `state.json` — it's used by approval_poller to avoid re-processing
