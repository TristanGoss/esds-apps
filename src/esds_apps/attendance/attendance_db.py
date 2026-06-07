"""Attendance database — core schema and write API.

A standalone SQLite file holding only ``dancer_id`` pseudonyms and headcounts —
never names or emails — so it can safely go online while ``pseudonyms.sqlite``
stays offline. This module owns the schema and a narrow write API; it never reads
a spreadsheet cell. Parsers (see ``ingest.py``) do that and call in here.

See ``working/attendance_db_design.md`` for the design rationale.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path


class EventType(StrEnum):
    COURSE = 'course'
    SOCIAL = 'social'
    WEEKENDER = 'weekender'
    WORKSHOP = 'workshop'


class ActivityType(StrEnum):
    LESSON = 'lesson'
    SOCIAL = 'social'


class AttendanceStatus(StrEnum):
    # We have a positive record that the dancer was there.
    ATTENDED = 'attended'
    # We have a positive record that the dancer was *not* there: an enrolled/booked dancer
    # marked absent, a no-show, or a refunded ticket. They were expected but didn't come.
    ABSENT = 'absent'
    # We have a record of the dancer for this activity (they bought a ticket or appear on the
    # register) but whether they actually attended was never captured. Distinct from ATTENDED
    # and ABSENT: the row still records genuine interest, it just can't be counted as a head.
    # The row's existence is the evidence of interest; the status says the turnout is unknown.
    UNKNOWN = 'unknown'


class TicketType(StrEnum):
    MEMBER = 'member'
    CONCESSION = 'concession'
    # The full, undiscounted rate — what the spreadsheets call a 'non-member'. Named 'ordinary'
    # in the database because it reads more naturally alongside the discounted tiers and isn't
    # phrased as the absence of something.
    ORDINARY = 'ordinary'
    # Many registers only record ordinary vs not: a single 'Concession / Member?' flag says
    # someone is entitled to the member/concession rate (i.e. not ordinary) without saying
    # which. Use this when member and concession genuinely can't be told apart.
    MEMBER_OR_CONCESSION = 'member_or_concession'
    # The source recorded *something* about the rate that we can't classify (an unreadable or
    # contradictory value). Distinct from NULL, which means no ticket information at all.
    UNKNOWN = 'unknown'


# Tables, the unifying view, and the one index that isn't already implied by a
# PRIMARY KEY or UNIQUE constraint. Everything is IF NOT EXISTS so open_db is idempotent.
_SCHEMA = """
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
"""


def _to_iso_date(value: date | datetime | str) -> str:
    """Normalise a date/datetime/ISO-ish string to a 'YYYY-MM-DD' string."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return datetime.fromisoformat(str(value).strip()).date().isoformat()


@dataclass
class AttendanceDb:
    """Thin write API over the attendance SQLite file. All methods commit."""

    conn: sqlite3.Connection

    # ---- ingest provenance ----

    def start_ingest(
        self, source_file: str, sheet: str | None = None, file_sha256: str | None = None, note: str | None = None
    ) -> int:
        """Record a new ingest and return its id, to stamp on the rows it produces."""
        cur = self.conn.execute(
            'INSERT INTO ingest_log (source_file, sheet, file_sha256, ingested_at, note) VALUES (?, ?, ?, ?, ?)',
            (source_file, sheet, file_sha256, datetime.now(timezone.utc).isoformat(), note),
        )
        self.conn.commit()
        return cur.lastrowid

    # ---- dancers ----

    def ensure_dancer(self, dancer_id: str) -> None:
        """Register a pseudonymous dancer id if not already present."""
        self.conn.execute('INSERT OR IGNORE INTO dancer (dancer_id) VALUES (?)', (dancer_id,))

    # ---- events ----

    def upsert_event(self, name: str, event_type: EventType, venue: str | None = None) -> int:
        """Insert or update an event by its unique name; return its id.

        ``event_type`` is set on first insert and sticky thereafter: re-ingest never
        overwrites it, so the classifier's first guess (or a later manual correction)
        survives. ``venue`` is COALESCE'd, so it can be filled in later but not wiped.
        """
        row = self.conn.execute('SELECT event_id FROM event WHERE name = ?', (name,)).fetchone()
        if row:
            event_id = row[0]
            self.conn.execute(
                'UPDATE event SET venue = COALESCE(?, venue) WHERE event_id = ?',
                (venue, event_id),
            )
        else:
            cur = self.conn.execute(
                'INSERT INTO event (name, event_type, venue) VALUES (?, ?, ?)', (name, str(event_type), venue)
            )
            event_id = cur.lastrowid
        self.conn.commit()
        return event_id

    def set_event_teachers(self, event_id: int, dancer_ids: list[str]) -> None:
        """Replace the teacher set for an event (idempotent)."""
        for dancer_id in dancer_ids:
            self.ensure_dancer(dancer_id)
        self.conn.execute('DELETE FROM event_teacher WHERE event_id = ?', (event_id,))
        self.conn.executemany(
            'INSERT OR IGNORE INTO event_teacher (event_id, dancer_id) VALUES (?, ?)',
            [(event_id, d) for d in dancer_ids],
        )
        self.conn.commit()

    # ---- activities ----

    def upsert_activity(
        self,
        event_id: int,
        name: str,
        date: date | datetime | str,
        activity_type: ActivityType | None = None,
        difficulty: str | None = None,
    ) -> int:
        """Insert or update an activity by (event, name, date); return its id.

        ``difficulty`` is a free-text level ('Level 1', 'beginners', ...): it sits on the
        activity, not the event, because weekenders and workshops mix levels within one
        event. On re-ingest, activity_type and difficulty are COALESCE'd, so a None never
        wipes a value set by an earlier pass.
        """
        iso = _to_iso_date(date)
        row = self.conn.execute(
            'SELECT activity_id FROM activity WHERE event_id = ? AND name = ? AND date = ?', (event_id, name, iso)
        ).fetchone()
        if row:
            activity_id = row[0]
            self.conn.execute(
                'UPDATE activity SET activity_type = COALESCE(?, activity_type), '
                'difficulty = COALESCE(?, difficulty) WHERE activity_id = ?',
                (str(activity_type) if activity_type else None, difficulty, activity_id),
            )
        else:
            cur = self.conn.execute(
                'INSERT INTO activity (event_id, name, activity_type, difficulty, date) VALUES (?, ?, ?, ?, ?)',
                (event_id, name, str(activity_type) if activity_type else None, difficulty, iso),
            )
            activity_id = cur.lastrowid
        self.conn.commit()
        return activity_id

    # ---- facts ----

    def record_attendance(
        self,
        activity_id: int,
        dancer_id: str,
        status: AttendanceStatus,
        ticket_type: TicketType | None = None,
        ingest_id: int | None = None,
        source_cell: str | None = None,
    ) -> None:
        """Upsert one identified dancer's attendance at an activity (one row per pair).

        ``status`` is an :class:`AttendanceStatus` rather than a bool so the row says exactly
        what is known — attended, absent, or attendance-unknown — without a reader having to
        infer it from a NULL or a missing row.

        When two sources describe the same (activity, dancer) — typically a weekly roster that
        captured turnout and a booking export that only knows a ticket was bought — the more
        informative reading wins: **attended > absent > unknown**. The merge is a total order
        on rank, so it is independent of ingest order (a later UNKNOWN never demotes a recorded
        attendance, and a later ABSENT never overwrites an ATTENDED). The provenance columns
        (ingest_id, source_cell) follow the kept status; ticket_type is COALESCE'd so either
        source can fill it.
        """
        self.ensure_dancer(dancer_id)
        # Rank the incoming status against the stored one; the higher rank is kept. On a tie
        # (same rank) the incoming row is taken, so a re-ingest still refreshes provenance.
        rank = "(CASE {0} WHEN 'attended' THEN 2 WHEN 'absent' THEN 1 ELSE 0 END)"
        incoming_at_least = f'{rank.format("excluded.status")} >= {rank.format("status")}'
        self.conn.execute(
            'INSERT INTO attendance (activity_id, dancer_id, status, ticket_type, ingest_id, source_cell) '
            'VALUES (?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(activity_id, dancer_id) DO UPDATE SET '
            f'  status = CASE WHEN {incoming_at_least} THEN excluded.status ELSE status END, '
            '  ticket_type = COALESCE(excluded.ticket_type, ticket_type), '
            f'  ingest_id = CASE WHEN {incoming_at_least} THEN excluded.ingest_id ELSE ingest_id END, '
            f'  source_cell = CASE WHEN {incoming_at_least} THEN excluded.source_cell ELSE source_cell END',
            (activity_id, dancer_id, str(status), _tt(ticket_type), ingest_id, source_cell),
        )
        self.conn.commit()

    def record_count(
        self,
        activity_id: int,
        ticket_type: TicketType | None,
        head_count: int,
        ingest_id: int | None = None,
        source_cell: str | None = None,
    ) -> None:
        """Upsert an aggregate headcount (replace semantics, so re-ingest is idempotent).

        Done by hand rather than ON CONFLICT because SQLite treats NULLs as distinct
        in a UNIQUE index, so the anonymous (ticket_type = NULL) row would never conflict.
        """
        tt = _tt(ticket_type)
        row = self.conn.execute(
            'SELECT count_id FROM attendance_count WHERE activity_id = ? AND ticket_type IS ?', (activity_id, tt)
        ).fetchone()
        if row:
            self.conn.execute(
                'UPDATE attendance_count SET head_count = ?, ingest_id = ?, source_cell = ? WHERE count_id = ?',
                (head_count, ingest_id, source_cell, row[0]),
            )
        else:
            self.conn.execute(
                'INSERT INTO attendance_count (activity_id, ticket_type, head_count, ingest_id, source_cell) '
                'VALUES (?, ?, ?, ?, ?)',
                (activity_id, tt, head_count, ingest_id, source_cell),
            )
        self.conn.commit()

    def record_waitlist(
        self,
        event_id: int,
        dancer_id: str | None = None,
        head_count: int = 1,
        ingest_id: int | None = None,
        source_cell: str | None = None,
    ) -> None:
        """Upsert a waitlist entry for an event: a named dancer, or an anonymous count.

        Waitlisters wanted a ticket but the event was full; they are not attendees, so they
        live apart from the attendance tables and never enter attendance or revenue totals.
        Pass a ``dancer_id`` (head_count defaults to 1) for a named waitlister, or leave it
        None and pass ``head_count`` for a bare count. Replace semantics make re-ingest
        idempotent — the anonymous (dancer_id = NULL) row is upserted by hand because SQLite
        treats NULLs as distinct in a UNIQUE index, so it would never conflict.
        """
        if dancer_id is not None:
            self.ensure_dancer(dancer_id)
            self.conn.execute(
                'INSERT INTO waitlist (event_id, dancer_id, head_count, ingest_id, source_cell) '
                'VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(event_id, dancer_id) DO UPDATE SET '
                '  head_count = excluded.head_count, ingest_id = excluded.ingest_id, '
                '  source_cell = excluded.source_cell',
                (event_id, dancer_id, head_count, ingest_id, source_cell),
            )
        else:
            row = self.conn.execute(
                'SELECT waitlist_id FROM waitlist WHERE event_id = ? AND dancer_id IS NULL', (event_id,)
            ).fetchone()
            if row:
                self.conn.execute(
                    'UPDATE waitlist SET head_count = ?, ingest_id = ?, source_cell = ? WHERE waitlist_id = ?',
                    (head_count, ingest_id, source_cell, row[0]),
                )
            else:
                self.conn.execute(
                    'INSERT INTO waitlist (event_id, dancer_id, head_count, ingest_id, source_cell) '
                    'VALUES (?, NULL, ?, ?, ?)',
                    (event_id, head_count, ingest_id, source_cell),
                )
        self.conn.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        self.conn.close()


def _tt(ticket_type: TicketType | None) -> str | None:
    # Preserve None as None (SQL NULL) instead of converting to string 'None'.
    return str(ticket_type) if ticket_type is not None else None


def open_db(db_path: Path | str) -> AttendanceDb:
    """Open (or create) the attendance database. Idempotent; safe to call repeatedly."""
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON')
    if str(db_path) != ':memory:':
        conn.execute('PRAGMA journal_mode = WAL')
    conn.executescript(_SCHEMA)
    conn.commit()
    return AttendanceDb(conn)
