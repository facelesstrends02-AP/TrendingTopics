# Workflow 10: Retention Script Building

## Objective
Generate a retention-optimized video script for any topic. Uses a specialized scriptwriting
prompt with pattern interrupts, curiosity loops, engagement moments, and subscriber-driving CTAs.

The `generate_retention_script.py` tool is also used automatically by the production pipeline
(Step A in `production_agent.py`) — the sole script generator for all productions.
Run this agent standalone when you want to review or refine a script before production.

## When to Run
- To write a script for any topic without going through the full pipeline
- To review a script before approving production
- To write scripts for experiments, repurposing, or manual uploads

## Agent
`agents/video_script_agent.py`

## Tools Used
1. `tools/generate_retention_script.py` — Claude Sonnet retention-optimized scriptwriter

## Required Inputs
- `ANTHROPIC_API_KEY` in `.env`
- `CHANNEL_NAME` in `.env` (for brand voice)
- `NICHE` in `.env`

## Usage Examples
```bash
# Write a script for a topic
python3 agents/video_script_agent.py --topic "5 habits that changed my life"

# Write and email the script
python3 agents/video_script_agent.py --topic "stoic discipline" --email

# From an existing idea
python3 agents/video_script_agent.py --idea-id 3 --ideas-file .tmp/ideas.json

# Custom output path
python3 agents/video_script_agent.py --topic "morning routine" --output .tmp/scripts/custom_script.json

# Dry run
python3 agents/video_script_agent.py --dry-run --topic "focus and discipline"
```

## Output
- Script JSON at `.tmp/scripts/standalone_{topic}_script.json` (or `--output` path)
- Summary printed to stdout (title, duration, word count, segment flow)
- Optional email with full script

## Retention Script Structure
The script uses these segment types in order:

| Segment | Duration | Purpose |
|---|---|---|
| `hook` | 15-20s | Pattern interrupt — bold statement that breaks the scroll |
| `bridge` | 20-30s | Reinforces the promise, teases what's coming |
| `context` | 30-45s | Sets up the problem/premise |
| `point_1` | 75-90s | Problem → insight → example → takeaway |
| `pattern_interrupt_1` | 10-15s | Rhetorical question or surprising stat — re-hooks viewer |
| `point_2` | 75-90s | Deeper insight → example → takeaway |
| `pattern_interrupt_2` | 10-15s | Re-hooks viewer |
| `point_3` | 75-90s | Insight → example → takeaway |
| `pattern_interrupt_3` | 10-15s | Re-hooks viewer |
| `point_4` | 75-90s | Payoff — resolves the curiosity loop from the hook |
| `engagement` | 20-30s | Specific comment prompt question |
| `cta` | 30-45s | Value-specific subscription CTA |

**Total: ~480-600 seconds (~1040-1300 words)**

## Output Schema
```json
{
  "idea_id": 0,
  "title": "string",
  "thumbnail_text": "SHORT ALL CAPS",
  "description": "SEO description 150-300 words",
  "tags": ["tag1", "tag2"],
  "category_id": "26",
  "total_duration_estimate": 540,
  "segments": [
    {
      "segment_id": 1,
      "type": "hook|bridge|context|point_1|pattern_interrupt_1|point_2|pattern_interrupt_2|point_3|pattern_interrupt_3|point_4|engagement|cta",
      "text": "exact spoken words",
      "visual_cue": "footage description",
      "overlay_text": "on-screen text or null",
      "duration_estimate": 15,
      "pexels_search_queries": ["query1", "query2", "query3"]
    }
  ]
}
```

## Using the Script in Production
To produce a video from a standalone script:
```bash
# 1. Move script to the video slot
cp .tmp/scripts/standalone_topic_script.json .tmp/scripts/video_1_script.json

# 2. Manually set state to include the idea (or use production_agent directly)
# production_agent.py reads from .tmp/ideas.json + state.json
```

## Edge Cases
- **No ideas file**: Use `--topic` to skip the ideas lookup entirely
- **Script too short**: Target is 1040-1300 words (~8-10 min). If Claude produces significantly fewer words, the prompt may need nudging toward more detailed examples per point.
- **Wrong tone**: The prompt targets warm, authoritative, second-person voice — if off, check the `NICHE` env var
