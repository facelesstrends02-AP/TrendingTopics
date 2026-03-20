# Pipeline Schedule — Trends Daily

> All times in IST. Keep this file updated whenever cron jobs or publish times change.

---

## Weekly Flow

```
SUNDAY
├── 8:15 PM  → Analytics Agent
│              Fetches YouTube stats → writes to Google Sheets → emails summary
│
└── 9:15 PM  → Idea Agent
               Scrapes trending content → generates 10 ideas → emails you the list

                        ▼  YOU REPLY: "APPROVE: 1, 3, 7"

Every 30 min → Approval Poller detects reply → queues approved ideas for production

                        ▼  AUTO: Production Agent (per video)
                           Script (Claude) → Voiceover (OpenAI TTS)
                           → Footage (Pexels/Pixabay) → Assemble (moviepy)
                           → Upload unlisted → Thumbnail → Email preview link

                        ▼  YOU REPLY: "APPROVE ALL"

                        ▼  AUTO: Publisher Agent
                           Schedules videos for Mon/Wed/Fri at 7:00 AM IST

MONDAY / WEDNESDAY / FRIDAY
├──  7:00 AM  → Full video goes public (auto-scheduled by YouTube)
│
└──  9:00 PM  → Shorts Scheduler (runs on publish day only)
               Triggers Shorts Agent for that day's video:
               1. Generate 2 short scripts (Claude)
               2. Voiceover (OpenAI TTS)
               3. Vertical footage (Pexels)
               4. Assemble portrait video (9:16)
               5. Upload private → schedule

NEXT DAY (Tue / Thu / Sat)
├──  7:00 AM  → Short 1 goes public
└──  7:00 PM  → Short 2 goes public
```

---

## Schedule Summary

| Time (IST)            | Agent / Action                          |
|-----------------------|-----------------------------------------|
| Sun 8:15 PM           | Analytics Agent runs                    |
| Sun 9:15 PM           | Idea Agent → 10 ideas emailed to you   |
| Every 30 min          | Approval Poller checks Gmail            |
| Mon/Wed/Fri 7:00 AM   | Full video goes public                  |
| Mon/Wed/Fri 9:00 PM   | Shorts Scheduler → produces 2 shorts   |
| Tue/Thu/Sat 7:00 AM   | Short 1 goes public                     |
| Tue/Thu/Sat 7:00 PM   | Short 2 goes public                     |

---

## Cron Jobs (UTC)

```
45 14 * * 0       analytics_agent.py        # Sun 8:15 PM IST
45 15 * * 0       idea_agent.py             # Sun 9:15 PM IST
*/30 * * * *      approval_poller.py        # Every 30 min
30 15 * * 1,3,5   shorts_scheduler.py       # Mon/Wed/Fri 9:00 PM IST
```

Full video publish time (7:00 AM IST = 01:30 UTC) is set in `agents/publisher_agent.py`.

---

## State Machine

```
idle → ideas_generated → awaiting_idea_approval → production_queued
     → production_in_progress → awaiting_video_approval
     → publishing_queued → publishing_in_progress → completed
```

Tracked in `.tmp/state.json`.
