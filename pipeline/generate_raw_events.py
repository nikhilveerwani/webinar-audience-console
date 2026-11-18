"""Generate SYNTHETIC raw webinar data: one row per join/leave session, the way a
video-conferencing export looks before anyone cleans it.

Nothing here comes from a real company. Every name, count and timestamp is made up,
but the messy parts are realistic on purpose:
  * people join up to 25 minutes before the scheduled start,
  * ~12% of attendees drop and rejoin (several rows for one person),
  * a few join twice at once from two devices (overlapping rows),
  * timestamps are UTC while the business runs on IST.

Each webinar is seeded from its own date, so a past day's data never changes when the
rolling window moves forward.

Usage: python pipeline/generate_raw_events.py --out data/raw --days 45
"""
import argparse
import csv
import datetime as dt
import hashlib
import os
import random

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

REGULAR_TOPICS = [
    "Cracking the Data Analyst Interview", "SQL for Analytics Careers",
    "System Design Fundamentals", "Breaking into Product Management",
    "From Engineer to Engineering Manager", "Machine Learning Career Paths",
    "Backend Interview Playbook", "Analytics Case Interviews Decoded",
]
MASTERCLASS_TOPICS = [
    "Masterclass: Building an AI Portfolio", "Masterclass: Senior Interview Strategy",
    "Masterclass: Data Science in Practice",
]


def schedule(day: dt.date):
    """Which webinars run on a given day: (slot, type, IST start, scheduled minutes)."""
    rng = random.Random(f"schedule-{day.isoformat()}")
    wd = day.weekday()  # Mon=0 .. Sun=6
    out = []
    if wd == 6:
        return out
    if wd == 5:
        if rng.random() < 0.7:
            out.append(("A", "Masterclass", dt.time(11, 0), 110))
        return out
    out.append(("A", "Regular", dt.time(19, 0), 75))
    if rng.random() < 0.35:
        out.append(("B", "Regular", dt.time(13, 0), 75))
    return out


def attendee_sessions(rng, start_utc, minutes, qualified):
    """Return the join/leave sessions for one attendee, in UTC."""
    r = rng.random()
    if r < 0.35:
        join = rng.uniform(-25, 0)
    elif r < 0.85:
        join = rng.uniform(0, 6)
    else:
        join = min(6 + rng.expovariate(1 / 8), minutes - 5)

    # stay length: qualified attendees are stickier; there is a drop at the pitch (~60%)
    stay_full = 0.30 if qualified else 0.12
    if rng.random() < stay_full:
        leave = minutes + rng.uniform(0, 15)
    elif rng.random() < 0.18:
        leave = minutes * 0.6 + rng.uniform(-3, 4)
    else:
        leave = join + rng.expovariate(1 / (40 if qualified else 22))
    leave = max(leave, join + 1.5)
    leave = min(leave, minutes + 18)

    sessions = [(join, leave)]
    if rng.random() < 0.12 and leave - join > 10:          # connection drop and rejoin
        cut = rng.uniform(join + 3, leave - 3)
        gap = rng.uniform(0.3, 4)
        sessions = [(join, cut), (cut + gap, leave)]
    if rng.random() < 0.02:                                 # second device, overlapping
        a = rng.uniform(join, leave)
        sessions.append((a, min(leave, a + rng.uniform(3, 20))))

    to_ts = lambda m: start_utc + dt.timedelta(minutes=m)
    return [(to_ts(a), to_ts(b)) for a, b in sessions]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--today", help="IST date the window ends on (default: today)")
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(IST).date()
    os.makedirs(args.out, exist_ok=True)
    web_rows, part_rows = [], []

    for back in range(args.days, 0, -1):
        day = today - dt.timedelta(days=back)
        for slot, wtype, t, minutes in schedule(day):
            wid = f"W{day.strftime('%Y%m%d')}{slot}"
            rng = random.Random(wid)
            topics = MASTERCLASS_TOPICS if wtype == "Masterclass" else REGULAR_TOPICS
            title = topics[rng.randrange(len(topics))]
            start_ist = dt.datetime.combine(day, t, IST)
            start_utc = start_ist.astimezone(dt.timezone.utc)

            base = 620 if wtype == "Masterclass" else 240
            base *= 1.15 if day.weekday() == 0 else 1.0       # Monday bump
            base *= 0.8 if slot == "B" else 1.0               # afternoon slot is smaller
            n = max(60, int(rng.gauss(base, base * 0.18)))
            q_share = rng.uniform(0.30, 0.38) if wtype == "Masterclass" else rng.uniform(0.38, 0.52)

            web_rows.append([wid, title, wtype, start_utc.isoformat().replace("+00:00", "Z"), minutes])
            for i in range(n):
                qualified = rng.random() < q_share
                aid = hashlib.sha1(f"{wid}-{i}".encode()).hexdigest()[:10]
                for a, b in attendee_sessions(rng, start_utc, minutes, qualified):
                    part_rows.append([wid, aid, "Qualified" if qualified else "Other",
                                      a.isoformat(timespec="seconds").replace("+00:00", "Z"),
                                      b.isoformat(timespec="seconds").replace("+00:00", "Z")])

    with open(os.path.join(args.out, "webinars.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["webinar_id", "title", "webinar_type", "scheduled_start_utc", "scheduled_minutes"])
        w.writerows(web_rows)
    with open(os.path.join(args.out, "participants.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["webinar_id", "attendee_id", "segment", "join_time_utc", "leave_time_utc"])
        w.writerows(part_rows)
    print(f"synthetic: {len(web_rows)} webinars, {len(part_rows)} participant rows -> {args.out}")


if __name__ == "__main__":
    main()
