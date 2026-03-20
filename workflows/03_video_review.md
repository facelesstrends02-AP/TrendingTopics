# Workflow: Email-Based Approval Polling

## Objective

Poll the Gmail inbox every 30 minutes for approval replies to system-sent emails. Route to the correct next step (idea production or video publishing) based on current pipeline phase.

## Required Inputs

- `state.phase` — determines what to poll for
- `state.ideas_email_message_id` — Gmail message ID to check replies for
- `state.review_email_message_id` — Gmail message ID for video review
- `credentials.json` + `token.json` — Gmail OAuth

## Tools Used

1. `tools/manage_state.py --read` — check current phase
2. `tools/poll_email_replies.py --original-message-id ... --since-timestamp ...`
3. `tools/parse_approval_email.py --mode ideas|videos --email-body "..."`
4. `tools/send_email.py` — for clarification emails
5. `tools/manage_state.py --write` + `--set-phase`
6. Launch `agents/production_agent.py` or `agents/publisher_agent.py` as subprocess

## Steps (executed by `agents/approval_poller.py`, every 30 min)

1. Read current state
2. If `phase = awaiting_idea_approval`:
   - Poll Gmail thread for reply to ideas email
   - If no reply: exit(0) — try again in 30 min
   - If reply found: parse with `parse_approval_email.py --mode ideas`
   - If ambiguous parse: send clarification email, wait for next poll cycle
   - If valid parse: update state with `approved_idea_ids`, set phase to `production_queued`
   - Launch `production_agent.py` as background process

3. If `phase = awaiting_video_approval`:
   - Poll Gmail thread for reply to video review email
   - Same pattern: poll → parse → clarify if needed → trigger publisher

4. If any other phase: exit immediately (nothing to poll)

## Approval Email Formats

### For idea approval

**System sends:**
```
10 video ideas are ready for Self Development channel.

  1. "5 Habits That Transformed Your Morning Routine" [HIGH]
  2. "Why Most People Fail at Goal Setting" [HIGH]
  ...

Reply with:
  APPROVE: 1, 3, 7
  APPROVE ALL
  APPROVE: 1-5
  APPROVE ALL EXCEPT: 4, 9
```

**User replies:**
```
APPROVE: 1, 3, 7
```
or `APPROVE ALL` or `APPROVE ALL EXCEPT: 4`

### For video approval

**System sends:**
```
3 videos are ready (unlisted YouTube links):

Video 1: "5 Habits..."   https://youtube.com/watch?v=abc123
Video 2: "..."           https://youtube.com/watch?v=def456

Reply with:
  APPROVE ALL
  APPROVE: abc123, def456
  REJECT: abc123
```

**User replies:**
```
APPROVE ALL
```
or `APPROVE: abc123`

## Email Parsing Logic (`tools/parse_approval_email.py`)

**Structured parsing (regex):**
1. Try `APPROVE ALL EXCEPT: X, Y`
2. Try `APPROVE ALL`
3. Try `APPROVE: N-M` (range)
4. Try `APPROVE: N, M, K` (list)
5. Try `REJECT: N, M` (implicit approve rest)

**NLP fallback:**
- If none match → call Claude Haiku to extract intent from free text
- Returns: `{"approved": [...], "rejected": [...], "ambiguous": false}`

**Ambiguous result:**
- Exit code 2 → approval_poller sends clarification email with exact format examples
- Does NOT advance pipeline phase — waits for clearer reply

## Expected Outputs

- `state.approved_idea_ids` populated → triggers production pipeline
- `state.approved_video_ids` populated → triggers publishing pipeline
- Or: clarification email sent if reply is ambiguous

## Triggered By

Cron: `*/30 * * * *` (every 30 minutes, all day every day)

## Edge Cases & Notes

**Poller runs when nothing to do:**
- Phases `idle`, `completed`, `production_in_progress`, `publishing_in_progress` → exit immediately
- Cron overhead is negligible (script exits in <1 second)

**Multiple replies in inbox:**
- `poll_email_replies.py` finds the FIRST unread reply in the correct thread after the timestamp
- Marks it as read so it won't be processed again
- Subsequent replies are ignored until pipeline resets

**No reply after 72 hours:**
- Current implementation: no automatic reminder (poller just keeps checking)
- To add: track `ideas_email_sent_at` in state and send reminder if >72h with no approval
- Manual override: run `python3 agents/production_agent.py` after manually editing state.json

**User approves from mobile email app:**
- `parse_approval_email.py` decodes HTML email bodies using BeautifulSoup-like stripping
- Works with most Gmail mobile app reply formats

**Reply includes quoted original email:**
- Parser looks at full body text — original quoted text might confuse the regex
- Claude Haiku fallback handles this gracefully (looks for intent, not format)

**`cron` doesn't fire if Mac is in deep sleep:**
- macOS may defer cron jobs if system is asleep
- Jobs fire when system wakes up
- Low impact: worst case, approval is processed a few hours late

**Changing the approval email address:**
- Update `APPROVAL_EMAIL` in `.env`
- Change also requires re-OAuth if using a different Google account
