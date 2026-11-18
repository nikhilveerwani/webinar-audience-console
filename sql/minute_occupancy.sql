-- Webinar audience per minute, in BigQuery Standard SQL.
-- Same rules as pipeline/transform.py, written for a warehouse.
-- Tables are generic placeholders: `project.webinar.participants`, `project.webinar.webinars`.
--
-- Rules
--   * minute 0 = scheduled start; the curve runs from -30 to scheduled end + 20
--   * a person is in the room in minute m if any of their sessions overlaps [m, m+1)
--   * overlapping sessions per person (second device) are merged first
--   * peak is measured from minute 0; retention at +N = in-room at N / peak

WITH sessions AS (
  SELECT
    p.webinar_id,
    p.attendee_id,
    ANY_VALUE(p.segment) OVER (PARTITION BY p.webinar_id, p.attendee_id) AS segment,
    TIMESTAMP_DIFF(p.join_time_utc,  w.scheduled_start_utc, SECOND) / 60 AS start_min,
    TIMESTAMP_DIFF(p.leave_time_utc, w.scheduled_start_utc, SECOND) / 60 AS end_min,
    w.scheduled_minutes
  FROM `project.webinar.participants` p
  JOIN `project.webinar.webinars` w USING (webinar_id)
  WHERE p.leave_time_utc > p.join_time_utc
),

-- Merge overlapping sessions per person (gaps-and-islands).
flagged AS (
  SELECT *,
    IF(start_min <= MAX(end_min) OVER (
         PARTITION BY webinar_id, attendee_id ORDER BY start_min, end_min
         ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0, 1) AS new_island
  FROM sessions
),
islands AS (
  SELECT *, SUM(new_island) OVER (
      PARTITION BY webinar_id, attendee_id ORDER BY start_min, end_min) AS island_id
  FROM flagged
),
merged AS (
  SELECT webinar_id, attendee_id, segment, scheduled_minutes,
         MIN(start_min) AS start_min, MAX(end_min) AS end_min
  FROM islands
  GROUP BY webinar_id, attendee_id, segment, scheduled_minutes, island_id
),

-- One row per person per minute present (DISTINCT: a sub-minute rejoin counts once).
person_minutes AS (
  SELECT DISTINCT m.webinar_id, m.attendee_id, m.segment, minute
  FROM merged m,
  UNNEST(GENERATE_ARRAY(
    GREATEST(-30, CAST(FLOOR(m.start_min) AS INT64)),
    LEAST(m.scheduled_minutes + 19, CAST(CEIL(m.end_min) AS INT64) - 1))) AS minute
),

occupancy AS (
  SELECT webinar_id, minute,
         COUNT(*) AS in_room,
         COUNTIF(segment = 'Qualified') AS in_room_qualified
  FROM person_minutes
  GROUP BY webinar_id, minute
),

peaks AS (
  SELECT webinar_id,
         MAX(in_room) AS peak,
         ARRAY_AGG(minute ORDER BY in_room DESC, minute LIMIT 1)[OFFSET(0)] AS peak_minute
  FROM occupancy
  WHERE minute >= 0
  GROUP BY webinar_id
)

SELECT
  o.webinar_id,
  p.peak,
  p.peak_minute,
  ROUND(100 * MAX(IF(o.minute = 15, o.in_room, NULL)) / p.peak, 1) AS retention_15,
  ROUND(100 * MAX(IF(o.minute = 30, o.in_room, NULL)) / p.peak, 1) AS retention_30,
  ROUND(100 * MAX(IF(o.minute = 45, o.in_room, NULL)) / p.peak, 1) AS retention_45,
  ARRAY_AGG(STRUCT(o.minute, o.in_room, o.in_room_qualified) ORDER BY o.minute) AS curve
FROM occupancy o
JOIN peaks p USING (webinar_id)
GROUP BY o.webinar_id, p.peak, p.peak_minute
ORDER BY o.webinar_id;
