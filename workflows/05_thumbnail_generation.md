# Workflow 05: Thumbnail Generation

## Objective
Auto-generate a clickbaity 1280×720 JPEG thumbnail for each video and upload it to YouTube immediately after the video is uploaded (while still unlisted).

## Placement in Pipeline
This runs as Steps F and G inside `production_agent.py`, immediately after Step E (YouTube upload):

```
Step E: upload_to_youtube.py     → video_id (unlisted)
Step F: generate_thumbnail.py    → .tmp/thumbnails/video_N_thumbnail.jpg
Step G: upload_thumbnail.py      → thumbnails.set API call
```

Both steps are wrapped in try/except — thumbnail failure is **non-fatal**. The video still proceeds to user review without a custom thumbnail (YouTube will show the auto-generated frame instead).

## Required Inputs
- Script JSON file (already created in Step A): provides `thumbnail_text`, `title`, `segments[0].pexels_search_query`, `segments[0].overlay_text`
- `PEXELS_API_KEY` in `.env` (same key as footage, covers both photos and videos endpoints)
- `token.json` (Google OAuth, same as upload — already authorized for `youtube` scope)
- Video ID from Step E

## Tools Used

### `tools/generate_thumbnail.py`
1. Reads `thumbnail_text` from script JSON (main bold headline)
2. Reads `segments[0].overlay_text` as subtext (displayed in warm yellow at the bottom)
3. Reads `segments[0].pexels_search_query` as the photo search term
4. Calls Pexels Photos API (`https://api.pexels.com/v1/search?orientation=landscape`)
5. Downloads best landscape photo
6. Resizes and center-crops to 1280×720
7. Applies dark gradient overlay (0% opacity top, ramps to 75% at bottom)
8. Draws main title in white bold 96pt with 4px black stroke (wraps to max 3 lines)
9. Draws subtext in warm yellow 44pt at the bottom
10. Saves as JPEG quality 92

**Pexels query fallback chain:** specific query → first word of query → "success"

**Font fallback chain:**
- macOS: `/System/Library/Fonts/Supplemental/Arial Bold.ttf`
- Linux: `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`
- Last resort: `ImageFont.load_default()` (basic but functional)

### `tools/upload_thumbnail.py`
1. Validates file size (max 2MB per YouTube requirement)
2. Calls `youtube.thumbnails().set(videoId, media_body)` via YouTube Data API v3
3. Quota cost: 50 units per call (3 videos/week = 150 units, vs. 10,000 daily limit)

## Output
- `.tmp/thumbnails/{video_key}_thumbnail.jpg` — the generated JPEG
- `state.json` video entry gets `thumbnail_path` and `thumbnail_uploaded: true/false`

## Edge Cases & Failure Handling

| Scenario | Behavior |
|----------|----------|
| Pexels returns no photos | Tries 3 fallback queries, then fails gracefully |
| Thumbnail file > 2MB | Error logged, thumbnail skipped |
| Font not found | Falls back to PIL default font (less polished but functional) |
| `thumbnails.set` API error | Warning logged, `thumbnail_uploaded: false` in state, video proceeds |
| `generate_thumbnail.py` crashes | Caught by try/except in `production_agent.py`, non-fatal |

## Manual Override
If the auto-generated thumbnail is poor quality:
1. Go to YouTube Studio → Videos → select the video
2. Click "Thumbnail" → "Upload thumbnail"
3. Upload your custom image (1280×720, under 2MB)

## Quota Notes
- `thumbnails.set`: 50 units per call
- 3 videos/week × 50 = 150 units/week
- Daily quota: 10,000 units — safely within limits

## Design Notes

**Why Pexels Photos (not DALL-E):**
- Free — same API key, no extra cost
- Deterministic — reproducible results
- High-quality real photography that reads well as thumbnails

**Why apply gradient rather than solid overlay:**
- Preserves visual context of the image (more appealing)
- Ensures text is always legible against dark bottom portion
- Matches standard YouTube clickbait thumbnail aesthetic

**Why upload while unlisted (not after publishing):**
- `thumbnails.set` works on unlisted videos — no need to wait for publishing
- User sees the actual thumbnail in the review email previews
- Avoids a second API call sequence in `publisher_agent.py`
