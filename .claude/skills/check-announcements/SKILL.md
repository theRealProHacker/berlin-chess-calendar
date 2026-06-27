---
name: check-announcements
description: Monthly reconciliation of predicted tournaments. Walks every status:"expected" record in data/tournaments.json and checks whether the real edition has now been officially announced, so predictions get promoted to confirmed with real dates (or marked stale, or corrected when the predicted year was wrong). Use when the user says "check announcements", "reconcile predictions", "are the expected tournaments announced yet", or runs the monthly maintenance pass. Run manually (no scheduler).
---

# Check announcements (monthly forecast reconciliation)

The maintenance heartbeat for the predictive calendar. Every `expected` record is a promise:
this checks each one against reality so the forecast self-corrects instead of rotting into a
wrong date nobody trusts. **Run manually, ~monthly** — there is no scheduler (a laptop cron
fails as silently as a GitHub cron; for a handful of records, a manual pass is honest).

**Never push.** Output is a summary table + a working-tree diff the human reviews and commits.

## Step 1 — Read the predictions
```bash
python3 -c "from bcc.build import load; \
[print(r['id'], r['start_date'], r.get('source_url','')) for r in load() if r['status']=='expected']"
```

## Step 2 — For each `expected` record, research (via `/browse`)
Check whether the real edition is now announced: fetch the `source_url` / official site / a
**direct** targeted lookup for the target year's dates (go direct — search-result pages
bot-wall the headless browser). Decide which case applies:

### Case A — Announced, year matches the prediction
Promote it (keep the same `id`/UID so subscribers' calendars update in place):
```bash
python -m bcc.add set <id> status=confirmed start_date=YYYY-MM-DD end_date=YYYY-MM-DD
# add what the announcement exposes:
# python -m bcc.add set <id> registration=https://... registration_deadline=YYYY-MM-DD
# python -m bcc.add set <id> prize_pool='{"amount":5150,"currency":"EUR"}'
```

### Case B — Announced, but the real year differs from the predicted year
The `id` bakes in the **guessed** year and **is the iCal UID** — you cannot just `set` new
dates onto a wrong-year id (the id would lie, and editing the id churns every subscriber's
calendar). Instead **detect and propose a delete-and-reinsert**:
1. Flag the mismatch in the summary table (predicted `2027`, real `2028`).
2. Remove the stale prediction record from `data/tournaments.json` (a deliberate hand-edit —
   `bcc.add` has no `delete`; removals are rare and human).
3. Insert a fresh correct-year record:
   ```bash
   python -m bcc.add skeleton "<name>" <real-year>   # fill from the announcement, status=confirmed
   python -m bcc.add insert /tmp/draft.json
   ```
   The stale prediction self-heals for anyone already subscribed: predictions are `TENTATIVE`
   in the `.ics` and drop 30 days after their (now-passed) predicted date.

### Case C — Not announced, predicted start is >30 days past
Likely discontinued or slipped. Propose marking it stale (drops from the `.ics`):
```bash
python -m bcc.add set <id> status=stale
```
(Or, if you find a new estimated window, re-estimate the dates instead.)

### Case D — Not announced, still future
No change needed beyond recording that you checked — bump the verification date:
```bash
python -m bcc.add set <id> last_verified=$(date +%F)   # `set` always bumps last_verified anyway
```

## Step 3 — Gate any applied change
```bash
python -m bcc.build
python -m unittest discover -s tests -t .
```

## Step 4 — Report, don't push
Print a summary table — `id | announced? | proposed action` — and show the `git diff`. Offer
to commit; **never auto-commit or push**.
