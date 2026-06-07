CREATE TABLE IF NOT EXISTS event (
    event_id   INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    event_type TEXT NOT NULL,
    venue      TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS dancer (
    dancer_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS event_teacher (
    event_id  INTEGER NOT NULL REFERENCES event(event_id),
    dancer_id TEXT    NOT NULL REFERENCES dancer(dancer_id),
    PRIMARY KEY (event_id, dancer_id)
);

CREATE TABLE IF NOT EXISTS activity (
    activity_id   INTEGER PRIMARY KEY,
    event_id      INTEGER NOT NULL REFERENCES event(event_id),
    name          TEXT NOT NULL,
    activity_type TEXT,
    difficulty    TEXT,
    date          TEXT NOT NULL,
    UNIQUE(event_id, name, date)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    ingest_id   INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,
    sheet       TEXT,
    file_sha256 TEXT,
    ingested_at TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS attendance (
    attendance_id INTEGER PRIMARY KEY,
    activity_id   INTEGER NOT NULL REFERENCES activity(activity_id),
    dancer_id     TEXT NOT NULL REFERENCES dancer(dancer_id),
    status        TEXT NOT NULL,
    ticket_type   TEXT,
    ingest_id     INTEGER REFERENCES ingest_log(ingest_id),
    source_cell   TEXT,
    UNIQUE(activity_id, dancer_id)
);

CREATE TABLE IF NOT EXISTS attendance_count (
    count_id    INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL REFERENCES activity(activity_id),
    ticket_type TEXT,
    head_count  INTEGER NOT NULL,
    ingest_id   INTEGER REFERENCES ingest_log(ingest_id),
    source_cell TEXT,
    UNIQUE(activity_id, ticket_type)
);

CREATE TABLE IF NOT EXISTS waitlist (
    waitlist_id INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES event(event_id),
    dancer_id   TEXT REFERENCES dancer(dancer_id),
    head_count  INTEGER NOT NULL,
    ingest_id   INTEGER REFERENCES ingest_log(ingest_id),
    source_cell TEXT,
    UNIQUE(event_id, dancer_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_date ON activity(date);

CREATE VIEW IF NOT EXISTS activity_attendance AS
SELECT a.activity_id, a.event_id, e.event_type, a.date, a.activity_type, a.difficulty,
       COALESCE(named.n, 0)                       AS named_total,
       COALESCE(agg.n, 0)                         AS aggregate_total,
       COALESCE(named.n, 0) + COALESCE(agg.n, 0)  AS total,
       COALESCE(unknown.n, 0)                     AS named_unknown,
       COALESCE(reg.n, 0)                         AS named_registered
FROM activity a
JOIN event e USING (event_id)
LEFT JOIN (SELECT activity_id, COUNT(*) AS n FROM attendance
           WHERE status = 'attended' GROUP BY activity_id) named USING (activity_id)
LEFT JOIN (SELECT activity_id, COUNT(*) AS n FROM attendance
           WHERE status = 'unknown' GROUP BY activity_id) unknown USING (activity_id)
LEFT JOIN (SELECT activity_id, COUNT(*) AS n FROM attendance
           GROUP BY activity_id) reg USING (activity_id)
LEFT JOIN (SELECT activity_id, SUM(head_count) AS n FROM attendance_count
           GROUP BY activity_id) agg USING (activity_id);
