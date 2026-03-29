# Workflow: Fact-Check Script

## Objective

Verify every specific factual claim in a generated video script before voiceover is recorded.
Auto-revise uncertain or speculative claims with hedged language. Flag actively-wrong claims
(DISPUTED) and pause production so the user can correct them manually.

This exists because the channel's trust is built on accuracy. The "personal implications" framing
(e.g., "your gas bill could rise by $40-60") is what makes the content sticky — but if those
claims are wrong, it destroys credibility faster than anything else.

---

## Trigger

Called automatically by:
- `agents/production_agent.py` — Step A.5, between script generation and voiceover
- `agents/shorts_agent.py` — Step 3.5, after Shorts script generation

Can also be run standalone against any script.

---

## Tool

`tools/fact_check_script.py`

---

## Inputs

| Input | Description |
|-------|-------------|
| `--script-file` | Full video script JSON (`.tmp/scripts/video_N_script.json`) or Shorts plan (`.tmp/shorts/video_N_shorts_plan.json`) |
| `--output-report` | Path to write the fact-check report JSON |
| `--mode` | `full` (default) or `shorts` |
| `ANTHROPIC_API_KEY` | Required. Already in `.env`. |
| `FACT_CHECK_ENABLED` | Set to `0` in `.env` to disable globally (emergency escape hatch only). |

---

## How It Works

**Single Claude Haiku call** reads the full script text and returns every specific verifiable claim
with a rating and (where needed) a suggested revision.

**What counts as a verifiable claim:**
- Specific statistics or numbers ("average household spent $1,600 on electricity")
- Causal assertions ("driven by the natural gas crisis")
- Predictions stated as fact ("prices will continue rising")
- Specific attributions ("according to the EIA")
- Personal implications stated with certainty ("your bill will increase by $40-60")
- Specific event/quantity assertions ("Germany had 700,000 registered by mid-2024")

**What is NOT extracted:** general opinions, narrative framing, rhetorical questions, CTA, branding.

### Claim Ratings

| Rating | Meaning | Action |
|--------|---------|--------|
| VERIFIED | High confidence accurate | Keep as-is |
| LIKELY_ACCURATE | Directionally correct, specific figure unconfirmed | Minor hedge only if number is very precise |
| UNVERIFIABLE | Too recent, too local, or not in training data | Auto-revise with hedged language |
| SPECULATIVE | Prediction or opinion framed as fact | Auto-revise with framing language |
| DISPUTED | Contradicted by training knowledge | **Pause pipeline, email user** |

### Auto-Revision Examples

| Before | After |
|--------|-------|
| "costs $2.40 per gallon" | "costs approximately $2.40 per gallon" |
| "prices will rise by 15%" | "analysts project prices could rise by around 15%" |
| "your heating bill will increase" | "your heating bill may increase" |
| "Germany had over 700,000 registered by mid-2024" | "Germany had more than 500,000 registered by 2024" |

Revisions are applied **in-place** to the script JSON file. The voiceover reads the revised text.
An audit report is saved separately to `.tmp/fact_checks/`.

---

## Exit Codes

| Code | Meaning | pipeline_agent behavior |
|------|---------|------------------------|
| 0 | All claims handled | Continue to voiceover |
| 1 | Tool error (API failure, file not found) | Log warning, continue (graceful degradation) |
| 2 | DISPUTED claims found | Pause this video, email user with claim details |

---

## DISPUTED Claims Flow

1. Tool writes the fact-check report with the disputed claims listed
2. Tool exits with code 2
3. `production_agent.py` sends an email to `APPROVAL_EMAIL` with:
   - The disputed claim text
   - The rationale (why it's disputed)
   - Path to the script file to edit
4. User manually fixes the claim in `.tmp/scripts/video_N_script.json`
5. User re-runs `production_agent.py` — the resume logic skips all completed steps and
   picks up at the voiceover (Step B). The fact-check re-runs on the fixed script.

**Expected frequency:** <5% of videos. Scripts are generated from real Reuters/AP/BBC RSS feeds,
and Claude already has "be factual" instructions. DISPUTED fires only for actively wrong claims,
not uncertain ones (those become UNVERIFIABLE and auto-revise).

---

## Outputs

- **`.tmp/scripts/video_N_script.json`** — revised in-place (same file, overwritten)
- **`.tmp/fact_checks/video_N_fact_check.json`** — full audit report

### Report Schema

```json
{
  "script_file": ".tmp/scripts/video_1_script.json",
  "video_title": "Title of Video",
  "mode": "full",
  "model": "claude-haiku-4-5-20251001",
  "checked_at": "2026-03-29T14:22:00Z",
  "summary": {
    "total_claims": 12,
    "verified": 4,
    "likely_accurate": 3,
    "unverifiable": 3,
    "speculative": 2,
    "disputed": 0,
    "auto_revised": 5,
    "pipeline_paused": false
  },
  "claims": [...],
  "disputed_claims": [],
  "revision_log": [
    {
      "claim_text": "original text",
      "revised_to": "hedged text",
      "rating": "UNVERIFIABLE",
      "segment_type": "point_1"
    }
  ]
}
```

---

## Standalone Usage

```bash
# Fact-check a full video script
python3 tools/fact_check_script.py \
    --script-file .tmp/scripts/video_1_script.json \
    --output-report .tmp/fact_checks/video_1_fact_check.json

# Fact-check a Shorts plan
python3 tools/fact_check_script.py \
    --script-file .tmp/shorts/video_1_shorts_plan.json \
    --output-report .tmp/fact_checks/video_1_shorts_fact_check.json \
    --mode shorts
```

---

## Cost

| Run type | Cost |
|----------|------|
| Full video fact-check | ~$0.005 |
| Shorts fact-check (2 scripts) | ~$0.002 |
| Monthly total (3 videos + 3 Shorts runs/week) | ~$0.09 |

Uses Claude Haiku. Single API call per run.

---

## Edge Cases

**Claim text not found for revision:** If the verbatim claim text can't be matched in the script
(e.g. Claude slightly paraphrased it in the output), the revision is skipped and logged as
`"revised_in_script": false`. The report still shows the suggested revision for manual reference.

**0 claims extracted:** Normal for CTAs, engagement segments, and very narrative scripts.
Tool exits 0 with an empty report.

**Knowledge cutoff boundary:** Scripts about events from early-mid 2025 may produce many
UNVERIFIABLE ratings. This is conservative and correct behavior — hedged language is safer than
confident wrong language.

**Shorts DISPUTED behavior:** Unlike full videos (which pause), Shorts with disputed claims
log an error and continue. Shorts are 55-70 words, lower claim density. The user is still
alerted via the error log.

**Disable fact-checking:** Set `FACT_CHECK_ENABLED=0` in `.env`. Not recommended for production —
only for testing or emergency situations where the API is unavailable.
