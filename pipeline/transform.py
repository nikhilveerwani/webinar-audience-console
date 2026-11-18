"""Turn raw join/leave rows into the metrics the console shows.

Rules (the same ones documented on the page):
  * Minute 0 is the SCHEDULED start, not the moment the room opened.
  * A person is "in the room" in minute m if any of their sessions overlaps [m, m+1).
    Sessions are merged per person first, so rejoins and second devices count once.
  * Peak = the highest in-room count from minute 0 onwards.
  * Retention at +N = in-room count at minute N as a % of peak.
  * Unique attendees = distinct people with at least one session.

Usage: python pipeline/transform.py --raw data/raw --out site/data.js
"""
import argparse
import csv
import datetime as dt
import json
import math
from collections import defaultdict

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PRE_MINUTES = 30          # curve starts 30 minutes before the scheduled start
POST_MINUTES = 20         # and runs 20 minutes past the scheduled end
CHECKPOINTS = (15, 30, 45)


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def merge_sessions(sessions):
    """Merge overlapping or touching (start, end) minute offsets for one person."""
    out = []
    for a, b in sorted(sessions):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def minute_grid(merged, lo, hi):
    """Set of whole minutes in [lo, hi) the person was present for any part of."""
    minutes = set()
    for a, b in merged:
        first = max(lo, math.floor(a))
        last = min(hi - 1, math.ceil(b) - 1)
        minutes.update(range(first, last + 1))
    return minutes


def build(raw_dir):
    webinars = {}
    with open(f"{raw_dir}/webinars.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["start"] = parse_ts(r["scheduled_start_utc"])
            r["scheduled_minutes"] = int(r["scheduled_minutes"])
            webinars[r["webinar_id"]] = r

    sessions = defaultdict(list)          # (webinar, attendee) -> [(start_min, end_min)]
    segment = {}
    raw_rows = 0
    with open(f"{raw_dir}/participants.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            raw_rows += 1
            w = webinars[r["webinar_id"]]
            a = (parse_ts(r["join_time_utc"]) - w["start"]).total_seconds() / 60
            b = (parse_ts(r["leave_time_utc"]) - w["start"]).total_seconds() / 60
            if b <= a:
                continue
            key = (r["webinar_id"], r["attendee_id"])
            sessions[key].append((a, b))
            segment[key] = r["segment"]

    per_webinar = defaultdict(list)
    for key in sessions:
        per_webinar[key[0]].append(key)

    out, daily = [], defaultdict(lambda: {"Qualified": 0, "Other": 0})
    merged_away = 0
    for wid, w in sorted(webinars.items(), key=lambda kv: kv[1]["start"]):
        lo, hi = -PRE_MINUTES, w["scheduled_minutes"] + POST_MINUTES
        curve = {"Qualified": [0] * (hi - lo), "Other": [0] * (hi - lo)}
        uniques = {"Qualified": 0, "Other": 0}
        watch = []
        for key in per_webinar[wid]:
            merged = merge_sessions(sessions[key])
            merged_away += len(sessions[key]) - len(merged)
            seg = segment[key]
            uniques[seg] += 1
            watch.append(sum(min(b, hi) - max(a, 0) for a, b in merged if b > 0 and a < hi))
            for m in minute_grid(merged, lo, hi):
                curve[seg][m - lo] += 1

        total = [q + o for q, o in zip(curve["Qualified"], curve["Other"])]

        def stats(series):
            from_zero = series[PRE_MINUTES:]
            peak = max(from_zero) if from_zero else 0
            return {
                "peak": peak,
                "peak_minute": from_zero.index(peak) if peak else None,
                "retention": {str(c): round(100 * series[PRE_MINUTES + c] / peak, 1) if peak else None
                              for c in CHECKPOINTS},
            }

        start_ist = w["start"].astimezone(IST)
        date = start_ist.date().isoformat()
        daily[date]["Qualified"] += uniques["Qualified"]
        daily[date]["Other"] += uniques["Other"]
        out.append({
            "id": wid, "title": w["title"], "type": w["webinar_type"],
            "date": date, "time": start_ist.strftime("%H:%M"),
            "scheduled_minutes": w["scheduled_minutes"],
            "uniques": uniques,
            "avg_watch_min": round(sum(watch) / len(watch), 1) if watch else 0,
            "all": stats(total), "Qualified": stats(curve["Qualified"]), "Other": stats(curve["Other"]),
            "curve": {"start": lo, "Qualified": curve["Qualified"], "Other": curve["Other"]},
        })

    attendees = sum(len(v) for v in per_webinar.values())
    return {
        "generated_at": dt.datetime.now(IST).strftime("%d %b %Y, %H:%M IST"),
        "synthetic": True,
        "window": {"from": out[0]["date"] if out else None, "to": out[-1]["date"] if out else None},
        "webinars": out,
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "checks": {"raw_rows": raw_rows, "attendees": attendees,
                   "rows_collapsed": raw_rows - attendees,
                   "overlaps_merged": merged_away, "webinars": len(out)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="site/data.js")
    args = ap.parse_args()
    data = build(args.raw)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("window.CONSOLE_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    c = data["checks"]
    print(f"{c['webinars']} webinars, {c['raw_rows']} raw rows -> {c['attendees']} attendees "
          f"({c['rows_collapsed']} rejoin/second-device rows collapsed) -> {args.out}")


if __name__ == "__main__":
    main()
