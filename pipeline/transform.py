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
    summary = executive_summary(out)
    return {
        "summary": summary,
        "generated_at": dt.datetime.now(IST).strftime("%d %b %Y, %H:%M IST"),
        "synthetic": True,
        "window": {"from": out[0]["date"] if out else None, "to": out[-1]["date"] if out else None},
        "webinars": out,
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "checks": {"raw_rows": raw_rows, "attendees": attendees,
                   "rows_collapsed": raw_rows - attendees,
                   "overlaps_merged": merged_away, "webinars": len(out)},
    }


def mean_ci(xs, z=1.96):
    """Mean and normal-approximation 95% interval (fine for n >= 30 webinars)."""
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else None, None, None)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    h = z * sd / math.sqrt(n)
    return (m, m - h, m + h)


def paired_p(diffs):
    """Two-sided p-value that the mean paired difference is zero (normal approximation)."""
    m, lo, hi = mean_ci(diffs)
    if lo is None:
        return None
    se = (hi - m) / 1.96
    return math.erfc(abs(m / se) / math.sqrt(2)) if se else 0.0


def biggest_drop(curves, step=15):
    """Average curve as % of each webinar's own peak; return the 15-minute window that loses the most."""
    pts = []
    for c in curves:
        peak = max(c[PRE_MINUTES:]) or 1
        pts.append([100 * x / peak for x in c[PRE_MINUTES:PRE_MINUTES + 76]])
    n = min(len(p) for p in pts)
    avg = [sum(p[i] for p in pts) / len(pts) for i in range(n)]
    best = max(range(5, n - step), key=lambda i: avg[i] - avg[i + step])
    return best, best + step, avg[best] - avg[best + step]


def executive_summary(webinars):
    reg = [w for w in webinars if w["type"] == "Regular"]
    if len(reg) < 5:
        return None
    diffs = [w["Qualified"]["retention"]["45"] - w["Other"]["retention"]["45"] for w in reg
             if w["Qualified"]["retention"]["45"] is not None and w["Other"]["retention"]["45"] is not None]
    d, dlo, dhi = mean_ci(diffs)
    p = paired_p(diffs)
    q45 = mean_ci([w["Qualified"]["retention"]["45"] for w in reg])[0]
    o45 = mean_ci([w["Other"]["retention"]["45"] for w in reg])[0]
    a, b, lost = biggest_drop([[q + o for q, o in zip(w["curve"]["Qualified"], w["curve"]["Other"])] for w in reg])
    qa, qb, qlost = biggest_drop([w["curve"]["Qualified"] for w in reg])
    same = abs(qa - a) <= 5
    peak_min = mean_ci([w["all"]["peak_minute"] for w in reg if w["all"]["peak_minute"] is not None])[0]
    pstr = "p < 0.001" if p is not None and p < 0.001 else f"p = {p:.3f}"
    return {
        "headline": f"The room loses the most people between minute {a} and {b}: {lost:.0f} points of the peak, more than any other 15 minutes.",
        "points": [
            f"The room peaks around minute {peak_min:.0f}; by +45, {mean_ci([w['all']['retention']['45'] for w in reg])[0]:.0f}% of the peak is still there (Regular webinars, n = {len(reg)}).",
            f"Qualified attendees stay longer: {q45:.0f}% vs {o45:.0f}% of peak at +45, a gap of {d:.1f} points "
            f"(95% CI {dlo:.1f} to {dhi:.1f}, paired across {len(diffs)} webinars, {pstr}).",
            (f"Qualified attendees' own steepest drop is minute {qa}–{qb} ({qlost:.0f} points): the same window. "
             "So it isn't only the wrong audience leaving; something in the session loses everyone there." if same else
             f"Qualified attendees' steepest drop is at a different point (minute {qa}–{qb}, {qlost:.0f} points), "
             "so the main drop is mostly the less-qualified audience leaving."),
        ],
        "recommendation": (f"Test moving the offer and the main Q&A before minute {a}, A/B across alternate sessions for 4 weeks, "
                           f"judged on qualified retention at +45 and on sales from attendees. Follow up qualified people "
                           f"who leave before minute {b} within the hour."),
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
