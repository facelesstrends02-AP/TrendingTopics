# Workflow 09: Title & Thumbnail Variant Generator

## Objective
Generate multiple title + thumbnail concept variants for a video topic to maximize CTR.
Each variant uses a different psychological trigger and thumbnail concept so you can pick
the strongest combination before committing to production.

## When to Run
- Before production, to choose the best title framing for an approved idea
- When testing different click-through strategies for a topic
- When a produced video has low CTR and you want to A/B test a new title

## Agent
`agents/title_thumbnail_agent.py`

## Tools Used (in order)
1. `tools/generate_titles_thumbnails.py` — Claude Sonnet generates title variants + thumbnail concepts
2. `tools/generate_thumbnail.py` — renders a preview image for each variant (Pexels + Pillow)
3. `tools/send_email.py` — sends variants email for review

## Required Inputs
- `NICHE` in `.env` (or pass `--niche`)
- `ANTHROPIC_API_KEY` in `.env`
- `PEXELS_API_KEY` in `.env` (for thumbnail previews)
- `APPROVAL_EMAIL` in `.env`
- OAuth `token.json` (for email)

## Usage Examples
```bash
# Generate variants for a topic
python3 agents/title_thumbnail_agent.py --topic "how to wake up at 5am"

# Generate variants from an existing idea
python3 agents/title_thumbnail_agent.py --idea-id 3 --ideas-file .tmp/ideas.json

# Only 3 variants
python3 agents/title_thumbnail_agent.py --topic "stoic morning habits" --count 3

# Dry run (no email sent)
python3 agents/title_thumbnail_agent.py --dry-run --topic "morning routine"
```

## Output
- `.tmp/title_variants/{topic}_variants.json` — all variants with concepts and recommendations
- `.tmp/title_variants/variant_N_thumbnail.jpg` — rendered thumbnail preview per variant
- Email sent to `APPROVAL_EMAIL` with full variant breakdown

## Output Schema
```json
{
  "topic": "how to wake up at 5am",
  "niche": "Self Development",
  "generated_at": "2026-03-12T10:00:00Z",
  "variants": [
    {
      "variant_id": 1,
      "title": "Stop Waking Up Early (Do This Instead)",
      "psychological_trigger": "tension",
      "why_it_works": "Challenges the viewer's assumption, creates cognitive dissonance",
      "thumbnail": {
        "main_visual": "person at desk in dark room, looking exhausted at alarm showing 5:00am",
        "text_overlay": "STOP WAKING UP EARLY",
        "emotion": "exhausted frustration transitioning to relief",
        "color_strategy": "dark background, bright red text, high contrast",
        "pexels_search_query": "person alarm clock dark bedroom morning exhausted"
      }
    }
  ],
  "recommended_variant_id": 2
}
```

## Psychological Triggers Used
| Trigger | Example | Best for |
|---|---|---|
| `curiosity_gap` | "The Sleep Trick Doctors Won't Tell You" | Topics with hidden/unknown information |
| `specificity` | "I Tried 5am Wake-Ups for 90 Days" | Personal experiments, data-driven content |
| `tension` | "Stop Waking Up Early (Do This Instead)" | Challenging conventional wisdom |
| `novelty` | "The Japanese Secret to Never Feeling Tired" | Cultural angles, new frameworks |
| `fomo` | "This Morning Habit Is Why You're Always Tired" | Audience pain points |

## Edge Cases
- **Pexels API fails for a variant**: Thumbnail preview is skipped (non-fatal), concept still included in email
- **Topic too vague**: Variants may overlap — try adding a specific angle to `--topic`
- **Too many variants requested**: Keep to 3-5 for meaningful differentiation; more creates overlap

## After Choosing a Variant
Copy the chosen title to inform your script prompt:
```bash
python3 agents/video_script_agent.py --topic "chosen title or topic"
# Or let production_agent.py use the idea's title as-is
```

Note: The production pipeline does not automatically use these variants.
To use a specific title, you would need to update the ideas.json or pass it as a topic to the script agent.
