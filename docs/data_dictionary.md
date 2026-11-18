# Data dictionary

## Inputs (synthetic, from `pipeline/generate_raw_events.py`)

### `webinars.csv`: one row per webinar

| Column | Type | Meaning |
|---|---|---|
| `webinar_id` | text | `W<yyyymmdd><slot>` |
| `title` | text | Session title |
| `webinar_type` | text | Regular (75 min, weekdays) or Masterclass (110 min, Saturdays) |
| `scheduled_start_utc` | timestamp (UTC) | Scheduled start. **This is minute 0** |
| `scheduled_minutes` | integer | Planned length |

### `participants.csv`: one row per join/leave **session** (not per person)

| Column | Type | Meaning |
|---|---|---|
| `webinar_id` | text | Webinar |
| `attendee_id` | text | Person (hashed). Can appear several times: rejoins, second devices |
| `segment` | text | Qualified / Other, set at registration |
| `join_time_utc`, `leave_time_utc` | timestamp (UTC) | One continuous session in the room |

## Output: `site/data.js`

| Field | Meaning |
|---|---|
| `webinars[]` | Per webinar: uniques by segment, average watch minutes, peak and peak minute, retention at +15/+30/+45 (overall and by segment), and the per-minute curve by segment from minute −30 |
| `checks` | Raw rows, attendees after merging, rows collapsed (rejoins and devices), webinar count |
| `summary` | Executive summary: headline, points, recommendation |

## Metric definitions

| Metric | Definition |
|---|---|
| **In the room at minute m** | Distinct people with any session overlapping [m, m+1), after merging each person's sessions |
| **Peak** | Highest in-room count from minute 0 on (lobby arrivals before the start can't set it) |
| **Retention at +N** | In-room at minute N ÷ peak |
| **Unique attendees** | Distinct people with at least one session |
| **Average watch** | Mean minutes in the room between minute 0 and the end of the curve |
| **Biggest drop** | The 15-minute window where the average curve (as % of each webinar's own peak) falls most |

## Statistical checks

| Claim | Method |
|---|---|
| Qualified stay longer | Paired difference (Qualified − Other retention at +45) per webinar. Mean, 95% normal interval, two-sided p |
| Where the room drops | Each webinar normalised to its own peak before averaging, so large sessions don't dominate |
