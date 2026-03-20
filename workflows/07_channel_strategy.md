# Workflow 07: Channel Strategy

## Objective

Run manually (once at channel launch, then once per quarter) to generate a comprehensive
90-day growth strategy for the YouTube channel. Uses Claude Sonnet to produce:

- Channel positioning and differentiation
- 4 core content pillars with 3 example titles each
- Upload schedule optimized for the niche audience
- 30-video roadmap (10 weeks × 3 videos)
- Fastest path to 1000 subscribers and 4000 watch hours
- Content formats proven in the niche
- 6 SEO tactics and thumbnail strategy

Writes to a "Strategy" tab in the existing Google Sheet and emails the strategy summary.

## When to Run

- At channel launch (before first video)
- Every quarter to refresh strategy based on what's working
- When pivoting niche or target audience

## Required Inputs

- `NICHE` — from `.env` (e.g., "Self Development")
- `CHANNEL_NAME` — from `.env`
- `ANTHROPIC_API_KEY` — for Claude Sonnet (`claude-sonnet-4-6`)
- `APPROVAL_EMAIL` — where to send the strategy email
- `GOOGLE_SHEET_ID` — target workbook (auto-created if missing)
- `credentials.json` + `token.json` — Google OAuth for Sheets + Gmail

## Optional CLI Arguments

| Argument | Description | Example |
|---|---|---|
| `--niche` | Override `NICHE` from `.env` | `"Finance"` |
| `--target-audience` | Who you're making content for | `"25-35 year old professionals"` |
| `--competitors` | Comma-separated channel names to differentiate from | `"Ali Abdaal,Thomas Frank"` |
| `--goals` | Growth goals | `"1000 subs in 90 days"` |
| `--dry-run` | Print email body without sending | — |

**Tip:** Always provide `--competitors` for sharper positioning. Without it, Claude positions
based on the niche alone and the differentiation will be more generic.

## Tools Used (in order)

1. `tools/generate_channel_strategy.py` — Claude Sonnet strategy generation → `.tmp/channel_strategy.json`
2. `tools/write_strategy_to_sheet.py` — Writes "Strategy" tab to `GOOGLE_SHEET_ID` workbook
3. `tools/send_email.py` — Sends formatted strategy email to `APPROVAL_EMAIL`

## Steps (executed by `agents/channel_strategy_agent.py`)

1. **Parse CLI args** — fall back to `.env` values (`NICHE`, `CHANNEL_NAME`, `APPROVAL_EMAIL`)

2. **Generate strategy** (hard failure — agent exits if this fails)
   - Calls `generate_channel_strategy.py` with niche, audience, competitors, goals
   - Uses `claude-sonnet-4-6` at `max_tokens=4000`
   - System prompt: senior YouTube strategist persona
   - Retries once on malformed JSON parse failure
   - Output: `.tmp/channel_strategy.json`

3. **Write to Google Sheet** (soft failure — falls back to inline email)
   - Calls `write_strategy_to_sheet.py`
   - Finds or creates "Strategy" tab in `GOOGLE_SHEET_ID` workbook
   - OVERWRITES (not appends) the tab each run — clearing content + formatting
   - Preserves tab's internal `sheetId` so `#gid=` bookmark URLs remain stable
   - Sheet layout: 7 sections (~65 rows, 6 columns A–F)

4. **Send email**
   - If sheet write succeeded: email summary + roadmap weeks 1–3 inline + sheet link
   - If sheet write failed: full strategy including complete roadmap inline
   - Subject: `[YT Automation] Channel Strategy Ready — {niche} ({date})`

## Expected Outputs

- `.tmp/channel_strategy.json` — full strategy JSON (persists for future reference)
- Google Sheet "Strategy" tab — formatted 7-section layout
- Email sent to `APPROVAL_EMAIL` with strategy summary and sheet link

## NOT Triggered By Cron

This agent is designed for manual one-off execution. Do NOT add to crontab.

```bash
# Standard run (uses NICHE from .env)
python3 agents/channel_strategy_agent.py

# Full run with all args
python3 agents/channel_strategy_agent.py \
  --niche "Self Development" \
  --target-audience "25-35 year old professionals" \
  --competitors "Ali Abdaal,Thomas Frank,James Clear" \
  --goals "1000 subscribers and 4000 watch hours in 90 days"

# Dry run (no email sent)
python3 agents/channel_strategy_agent.py --dry-run
```

## Impact on Existing Pipeline

Zero impact. This agent:
- Does **not** read or write `state.json`
- Does **not** interfere with cron-scheduled agents (analytics, idea, approval, production, publisher)
- Writes to a new **"Strategy" tab** — does not touch "Ideas" or "Analytics" tabs
- Outputs `.tmp/channel_strategy.json` which is available for future use

## Edge Cases & Notes

**Claude Sonnet timeout / malformed JSON:**
- Tool retries once before failing
- On hard failure, agent exits with code 1 and no email is sent
- Fix: run again — the tool is idempotent (no side effects until `.tmp/channel_strategy.json` is written)

**Google Sheets write failure:**
- Agent does NOT exit — falls back to sending full strategy inline in email
- User still receives the complete strategy; fix Sheets auth separately
- Common cause: `token.json` expired → re-authenticate with `bash setup.sh`

**`GOOGLE_SHEET_ID` not set in `.env`:**
- `write_strategy_to_sheet.py` creates a new workbook `{NICHE} - {YEAR}`
- New sheet ID is auto-saved to `.env`
- Subsequent agents (analytics, ideas) will pick up the same workbook automatically

**`--competitors` not provided:**
- Tool substitutes `"none specified — position uniquely within the niche"` in the prompt
- Claude still produces differentiation guidance based on the niche alone
- Recommendation: provide at least 2–3 competitor names for the sharpest positioning output

**Roadmap week count warning:**
- Claude is prompted for exactly 10 weeks but occasionally returns 9 or 11
- Tool logs a warning but does **not** fail — the strategy is still usable
- Sheet and email show whatever weeks were returned

## Cost

- `generate_channel_strategy.py`: ~$0.05–0.10/run (Sonnet, 4000 max tokens)
- `write_strategy_to_sheet.py`: free (Google Sheets API)
- `send_email.py`: free (Gmail API)
- **Total: ~$0.05–0.10 per quarterly run**
