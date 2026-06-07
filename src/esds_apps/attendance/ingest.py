"""Attendance ingestion — layout parsers and a folder dispatcher.

Each parser recognises one spreadsheet *layout* (``matches``) and ingests it into the
attendance database (``parse``). Parsers map to layouts, not years: one workbook may
mix layouts across tabs, and the same layout recurs unpredictably across years. The
dispatcher tries each parser per sheet and reports any sheet no parser claimed (those
would need a new parser).

All input is read from the pseudonymised ``attendance_outputs`` tree. The layouts supported
so far:

* **Roster** — one row per dancer, a boolean per session column (date in the row above
  the header). Level 1 tabs.
* **Level 2 tally** — a checkbox grid summed by COUNTIF into per-(week, ticket-type)
  headcounts, for two activity groups (Level 2 Classes / Social Only).
* **Level 2 count grid** — the older 2024 'Levels 1-2' layout: a week-per-row table with
  the per-ticket-type headcounts typed straight in (no checkboxes, no COUNTIF).
* **L2 & SO attendance** — the 2026 tabs whose session cells hold a ticket *category*
  ('Level 2 & Social' / 'Social-Only' / 'Absent') rather than a yes/no marker.
* **Social register** — a one-off social's named register (Tea Dances, Sunday Socials, parties):
  one dated social, attendees in one or more side-by-side blocks with a Present?/Attended marker
  and an optional Concession flag; the date comes from the tab name or filename.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

from esds_apps.attendance.attendance_db import ActivityType, AttendanceDb, EventType, TicketType

_TICKET_LABELS = {
    'members': TicketType.MEMBER,
    'concessions': TicketType.CONCESSION,
    'non-members': TicketType.ORDINARY,
    'non members': TicketType.ORDINARY,
}
_WEEK_RE = re.compile(r'Week\s*(\d+)\s*\(\s*(\d{1,2})\s+([A-Za-z]+)\)', re.IGNORECASE)
# A bare 'Week N' label (no '(DD Mon)' suffix): older sheets carry only the week number.
_WEEK_NO_RE = re.compile(r'\bweek\s*(\d+)\b', re.IGNORECASE)
_COUNTIF_RE = re.compile(r'COUNTIF\(\s*([A-Za-z]+)(\d+)\s*:\s*([A-Za-z]+)(\d+)', re.IGNORECASE)
# ESDS ran Level 2 and Level 3 together; normalise the various spellings to plain 'Level 2'.
_LEVEL_2_3_RE = re.compile(r'level\s*2\s*[/&-]\s*3\b', re.IGNORECASE)


def _is_group_header(text: str) -> bool:
    """True for a tally activity-group header. Tolerates 'Level 2 Class'/'Level 2 Classes'."""
    t = text.strip().lower()
    return t == 'social only' or t.startswith('level 2 class')


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------


# Markers meaning "attended". Rosters vary by year: booleans (True), 'True'/'TRUE'
# strings, the Excel formula '=TRUE()' (read as text because we load formulas, not cached
# values), ticks ('x', 'yes'), and check emoji (☑️/✅/✔/✓). Crosses (❎/❌), 'False',
# '=FALSE()' and 'Refunded' fall through to False — a refunded ticket is a non-attendance.
_TRUE_WORDS = {'true', 'yes', 'y', 'x', '1', '✓', '✔'}
_TRUE_EMOJI = ('☑', '✅', '✔', '✓')  # ☑ ✅ ✔ ✓
# The negative side of the same vocabulary, plus crosses. Used only to tell an attendance
# roster apart from a richer layout whose cells hold categories ('Level 2 & Social').
_FALSE_WORDS = {'false', 'no', 'n', '0', 'refunded', 'absent'}
_MARKER_EMOJI = (*_TRUE_EMOJI, '❎', '❌')


def _is_true(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        # Normalise the '=TRUE()' / '=FALSE()' formula form down to 'true' / 'false'.
        s = value.strip().lower().removeprefix('=').removesuffix('()')
        return s.startswith('true') or s in _TRUE_WORDS or any(e in value for e in _TRUE_EMOJI)
    return False


def _is_attendance_marker(value) -> bool:
    """True if a cell value is a yes/no attendance marker (not a free-text category)."""
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        s = value.strip().lower().removeprefix('=').removesuffix('()')
        return (
            s.startswith(('true', 'false'))
            or s in _TRUE_WORDS
            or s in _FALSE_WORDS
            or any(e in value for e in _MARKER_EMOJI)
        )
    return False


def _parse_dt(value) -> datetime | None:
    """Parse a cell value into a datetime, or None.

    Handles real datetimes and the ISO-ish strings the pseudonymiser leaves
    behind ('2025-05-22 00:00:00').
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _month_number(name: str) -> int | None:
    for fmt in ('%b', '%B'):
        try:
            return datetime.strptime(name.strip()[:3] if fmt == '%b' else name.strip(), fmt).month
        except ValueError:
            continue
    return None


# A date written into a one-off event's title or tab name: 'Tea Dance 25 Feb 2024',
# '... 28th April 2024', or '26 March 23'. Day may carry an ordinal suffix; month short or
# full; year 2- or 4-digit. Plus a plain ISO 'YYYY-MM-DD' as it appears in filenames.
_TITLE_DATE_RE = re.compile(r'(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{2,4})', re.IGNORECASE)
_ISO_DATE_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
_CENTURY = 100  # 2-digit years (e.g. '23') are read as 2000 + year


def _date_from_title(text: str) -> datetime | None:
    """Parse a 'DD[th] Month YY(YY)' or ISO 'YYYY-MM-DD' date out of a title, or None."""
    m = _TITLE_DATE_RE.search(text)
    if m and (month := _month_number(m.group(2))):
        year = int(m.group(3))
        return datetime(year + 2000 if year < _CENTURY else year, month, int(m.group(1)))
    m = _ISO_DATE_RE.search(text)
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _activity_type_for(label: str, event_type: EventType) -> ActivityType:
    """An activity's type. A social event holds only socials; otherwise the label decides.

    Session-column headers ('Christmas Party', 'Tea Dance', 'Registered') rarely contain
    the word 'social', so without the event-type override they'd wrongly read as lessons.
    """
    if event_type == EventType.SOCIAL:
        return ActivityType.SOCIAL
    return ActivityType.SOCIAL if 'social' in label.lower() else ActivityType.LESSON


def _event_type_for(title: str) -> EventType:
    """Classify an event from keywords in its tab title.

    'level' wins first: the termly "Level 2 & Social Only" tabs contain the word
    "social" but are courses. Workshops next, then socials; anything unmatched
    (Teachers Choice, plain Level N) is a course.
    """
    t = title.lower()
    if 'level' in t:
        return EventType.COURSE
    if 'swingout' in t or 'swing out' in t:
        return EventType.WEEKENDER
    if 'workshop' in t or 'charleston' in t:
        return EventType.WORKSHOP
    if any(k in t for k in ('social', 'party', 'tea dance')):
        return EventType.SOCIAL
    return EventType.COURSE


# Free-text difficulty parsed from a name. Ordered: first pattern to match wins. Lives on
# the activity (not the event) because weekenders/workshops mix levels within one event.
_DIFFICULTY_PATTERNS = [
    (re.compile(r'level\s*1\b|\bl1\b', re.IGNORECASE), 'Level 1'),
    (re.compile(r'level\s*2\b|\bl2\b', re.IGNORECASE), 'Level 2'),
    (re.compile(r'level\s*3\b|\bl3\b', re.IGNORECASE), 'Level 3'),
    (re.compile(r'\bbeg', re.IGNORECASE), 'beginners'),  # beginner(s), 'Begn'
    (re.compile(r'\binterm', re.IGNORECASE), 'intermediate'),
    (re.compile(r'\badv', re.IGNORECASE), 'advanced'),
]


def _difficulty_for(*texts: str | None) -> str | None:
    """Difficulty string ('Level 1', 'beginners', ...) from the first text that matches.

    Texts are tried most-specific first (activity label, then the sheet title), so a
    workshop column headed 'Beginners' beats the tab's overall name. None if unknown.
    """
    for text in texts:
        if not text:
            continue
        for pattern, label in _DIFFICULTY_PATTERNS:
            if pattern.search(text):
                return label
    return None


# ---------------------------------------------------------------------------
# Roster layout
# ---------------------------------------------------------------------------


def _roster_header(matrix: list[tuple]) -> tuple[int, int] | None:
    """Return (header_row, dancer_id_col) as 0-based indices, or None."""
    for r, row in enumerate(matrix):
        for c, value in enumerate(row):
            if isinstance(value, str) and value.strip() == 'dancer_id':
                return r, c
    return None


# Tolerant of the 'Consession' misspelling seen in the wild (Jan-Feb 2026) and a trailing plural.
_CONCESSION_HEADER_RE = re.compile(r'con[cs]essions?', re.IGNORECASE)


def _concession_col(header: tuple) -> int | None:
    """Column index of a roster's boolean-ish 'Concession' flag in its header row, or None."""
    for c, value in enumerate(header):
        if isinstance(value, str) and _CONCESSION_HEADER_RE.fullmatch(value.strip()):
            return c
    return None


def _session_columns(matrix: list[tuple], header_row: int, week_anchor: datetime | None = None) -> list[tuple]:
    """Session columns: a date either in the header row itself or the row directly above it.

    Older Level 1 tabs put the date in the row above a 'Week N' label; the 2026 tabs put
    the date straight in the header row alongside 'dancer_id'. Some older tabs only dated
    Week 1 and filled the rest with '=D2+7' formulas that the pseudonymiser stripped to
    nothing — for those an undated 'Week N' column is placed at ``week_anchor + 7*(N-1)``.
    Returns a list of (col_index, activity_name, datetime); activity type is decided in parse.
    """
    head = matrix[header_row]
    above = matrix[header_row - 1] if header_row > 0 else ()
    out = []
    for c in range(max(len(head), len(above))):
        label = head[c] if c < len(head) and isinstance(head[c], str) else None
        in_header = _parse_dt(head[c]) if c < len(head) else None
        dt = in_header or (_parse_dt(above[c]) if c < len(above) else None)
        if dt is None:
            wk = _WEEK_NO_RE.search(label) if label else None
            if wk is None or week_anchor is None:
                continue
            dt = week_anchor + timedelta(weeks=int(wk.group(1)) - 1)
        # A date in the header cell is its own label; otherwise the header cell names it.
        name = label if (in_header is None and label and label.strip()) else dt.date().isoformat()
        out.append((c, str(name).strip(), dt))
    return out


class RosterParser:
    name = 'roster'

    def matches(self, ws) -> bool:
        """True if the sheet has a dancer_id header, dated session columns, and yes/no markers.

        The session cells must be attendance markers (yes/no/true/false/etc.), not free-text
        categories, to rule out sheets like '2026 L2 & SO Attendance' which have dancer_id
        columns but hold category strings like 'Level 2 & Social' instead of attendance bools.
        Requires that at least 50% of non-empty sampled cells are attendance markers.
        """
        matrix = list(ws.iter_rows(values_only=True))
        header = _roster_header(matrix)
        if header is None:
            return False
        sessions = _session_columns(matrix, header[0])
        if not sessions:
            return False
        # Sample cells and check if they're mostly attendance markers (not categories)
        header_row = header[0]
        markers, non_markers = 0, 0
        for col, _, _ in sessions[:3]:  # sample first few sessions
            for r in range(header_row + 1, min(header_row + 5, len(matrix))):  # sample first few rows
                cell = matrix[r][col] if col < len(matrix[r]) else None
                if cell is not None:
                    if _is_attendance_marker(cell):
                        markers += 1
                    else:
                        non_markers += 1
        # Require 50% or more of non-empty cells to be attendance markers
        total = markers + non_markers
        return total > 0 and markers >= total / 2

    def parse(
        self,
        ws,
        db: AttendanceDb,
        term: str,
        year: int | None,
        ingest_id: int | None,
        week_anchor: datetime | None = None,
    ) -> None:
        """Ingest one roster tab: an event, an activity per session column, attendance rows."""
        matrix = list(ws.iter_rows(values_only=True))
        header_row, dancer_col = _roster_header(matrix)
        sessions = _session_columns(matrix, header_row, week_anchor)
        ticket_by_did = self._ticket_by_dancer(matrix, header_row, dancer_col, _concession_col(matrix[header_row]))

        title = ws.title
        event_type = _event_type_for(title)
        event_id = db.upsert_event(f'{term}: {_strip_attendance(title)}', event_type)

        for col, name, dt in sessions:
            activity_type = _activity_type_for(name, event_type)
            difficulty = 'social' if activity_type == ActivityType.SOCIAL else _difficulty_for(name, title)
            activity_id = db.upsert_activity(event_id, name, dt, activity_type, difficulty)
            attended_by: dict[str, list[bool]] = defaultdict(list)
            for r in range(header_row + 1, len(matrix)):
                row = matrix[r]
                did = row[dancer_col] if dancer_col < len(row) else None
                if isinstance(did, str) and did.startswith('DNC-'):
                    attended_by[did].append(_is_true(row[col]) if col < len(row) else False)

            cell = f'{title}!{get_column_letter(col + 1)}'
            anonymous_extra = 0
            for did, attendeds in attended_by.items():
                used = sum(attendeds)
                db.record_attendance(
                    activity_id,
                    did,
                    attended=used >= 1,
                    ticket_type=ticket_by_did.get(did),
                    ingest_id=ingest_id,
                    source_cell=cell,
                )
                anonymous_extra += max(used - 1, 0)  # un-renamed duplicate tickets → anonymous attendees
            if anonymous_extra:
                db.record_count(activity_id, None, anonymous_extra, ingest_id=ingest_id, source_cell=cell)

    @staticmethod
    def _ticket_by_dancer(matrix: list[tuple], header_row: int, dancer_col: int, conc_col: int | None) -> dict:
        """Map each dancer_id to a ticket type from its (per-dancer, constant) Concession flag.

        Empty if the roster has no Concession column. A dancer repeated across rows (un-renamed
        extra tickets) takes the first non-blank reading, so a stray blank duplicate row can't
        wipe a known value.
        """
        if conc_col is None:
            return {}
        out: dict[str, TicketType | None] = {}
        for r in range(header_row + 1, len(matrix)):
            row = matrix[r]
            did = row[dancer_col] if dancer_col < len(row) else None
            if not (isinstance(did, str) and did.startswith('DNC-')):
                continue
            ticket = _concession_ticket(row[conc_col]) if conc_col < len(row) else None
            out[did] = out.get(did) or ticket
        return out


# ---------------------------------------------------------------------------
# Level 2 tally layout
# ---------------------------------------------------------------------------


@dataclass
class _Tally:
    """Pre-scanned structure of a tally sheet."""

    matrix: list[tuple]
    groups: list[tuple] = field(default_factory=list)  # (col_1based, label)
    weeks: dict = field(default_factory=dict)  # start_row_1based -> (week_no, day, month_name)

    def cell(self, row1: int, col1: int):
        if 1 <= row1 <= len(self.matrix):
            row = self.matrix[row1 - 1]
            if 1 <= col1 <= len(row):
                return row[col1 - 1]
        return None


def _scan_tally(matrix: list[tuple]) -> _Tally:
    t = _Tally(matrix=matrix)
    for r, row in enumerate(matrix, start=1):
        for c, value in enumerate(row, start=1):
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if _is_group_header(stripped):
                t.groups.append((c, stripped))
            m = _WEEK_RE.search(stripped)
            if m:
                t.weeks[r] = (int(m.group(1)), int(m.group(2)), m.group(3))
    t.groups.sort()
    return t


def _group_for_col(t: _Tally, col1: int) -> str | None:
    candidates = [(c, label) for c, label in t.groups if c <= col1]
    return max(candidates)[1] if candidates else None


def _ticket_for_totcell(t: _Tally, row1: int, col1: int) -> TicketType | None:
    for c in range(col1 - 1, 0, -1):
        value = t.cell(row1, c)
        if isinstance(value, str) and value.strip().lower() in _TICKET_LABELS:
            return _TICKET_LABELS[value.strip().lower()]
    return None


def _count_true(t: _Tally, start_col: int, start_row: int, end_col: int, end_row: int) -> int:
    return sum(
        1 for r in range(start_row, end_row + 1) for c in range(start_col, end_col + 1) if _is_true(t.cell(r, c))
    )


class Level2TallyParser:
    name = 'level2_tally'

    def matches(self, ws) -> bool:
        """True if the sheet has COUNTIF-of-TRUE totals, a group header, and week labels."""
        has_countif = has_group = has_week = False
        for row in ws.iter_rows(values_only=True):
            for value in row:
                if not isinstance(value, str):
                    continue
                if 'countif' in value.lower() and 'true' in value.lower():
                    has_countif = True
                if _is_group_header(value):
                    has_group = True
                if _WEEK_RE.search(value):
                    has_week = True
        return has_countif and has_group and has_week

    def parse(
        self,
        ws,
        db: AttendanceDb,
        term: str,
        year: int | None,
        ingest_id: int | None,
        week_anchor: datetime | None = None,
    ) -> None:
        """Ingest one tally tab: a headcount per (week, activity group, ticket type)."""
        matrix = list(ws.iter_rows(values_only=True))
        t = _scan_tally(matrix)
        event_id = db.upsert_event(f'{term}: {_strip_attendance(ws.title)}', _event_type_for(ws.title))

        for r, row in enumerate(matrix, start=1):
            for c, value in enumerate(row, start=1):
                if isinstance(value, str) and (m := _COUNTIF_RE.search(value)):
                    self._ingest_total(db, t, event_id, ws.title, year, ingest_id, r, c, m)

    def _ingest_total(self, db, t, event_id, title, year, ingest_id, row1, col1, m) -> None:  # noqa: PLR0913
        start_col, start_row = column_index_from_string(m.group(1)), int(m.group(2))
        end_col, end_row = column_index_from_string(m.group(3)), int(m.group(4))

        group = _group_for_col(t, col1)
        ticket_type = _ticket_for_totcell(t, row1, col1)
        week = t.weeks.get(start_row)
        if group is None or week is None:
            return  # can't place this total without a group and a date

        week_no, day, month_name = week
        month = _month_number(month_name)
        if month is None or year is None:
            return
        activity_date = datetime(year, month, day)

        head_count = _count_true(t, start_col, start_row, end_col, end_row)
        activity_type = _activity_type_for(group, _event_type_for(title))
        difficulty = 'social' if activity_type == ActivityType.SOCIAL else _difficulty_for(group)
        activity_id = db.upsert_activity(
            event_id, f'{group} (Week {week_no})', activity_date, activity_type, difficulty
        )
        cell = f'{title}!{get_column_letter(col1)}{row1}'
        db.record_count(activity_id, ticket_type, head_count, ingest_id=ingest_id, source_cell=cell)


# ---------------------------------------------------------------------------
# Level 2 count-grid layout (2024 'Levels 1-2' workbooks)
# ---------------------------------------------------------------------------


class Level2CountGridParser:
    """A plain grid: one row per week, with the per-ticket-type headcounts typed straight in.

    Header row carries 'Members' / 'Concessions' / 'Non-Members'; each week row has a
    'Week N (DD Mon)' label in the first column and integer counts under those headers.
    No checkbox tally, no COUNTIF — distinguishes it from the Level 2 tally layout.
    """

    name = 'level2_count_grid'

    def _ticket_columns(self, matrix: list[tuple]) -> tuple[int | None, dict]:
        """Find the header row of ticket labels; return (row_index, {col_index: TicketType})."""
        for r, row in enumerate(matrix):
            cols = {
                c: _TICKET_LABELS[v.strip().lower()]
                for c, v in enumerate(row)
                if isinstance(v, str) and v.strip().lower() in _TICKET_LABELS
            }
            if cols:
                return r, cols
        return None, {}

    def matches(self, ws) -> bool:
        """True if there are ticket-label headers and week labels, but no COUNTIF (that's the tally)."""
        matrix = list(ws.iter_rows(values_only=True))
        flat = [v for row in matrix for v in row if isinstance(v, str)]
        if any('countif' in v.lower() for v in flat):
            return False
        _, cols = self._ticket_columns(matrix)
        return bool(cols) and any(_WEEK_NO_RE.search(v) for v in flat)

    @staticmethod
    def _week_date(label: str, week_no: int, year: int | None, week_anchor: datetime | None) -> datetime | None:
        """Date a week row: from a '(DD Mon)' suffix when present, else week_anchor + 7*(week-1)."""
        m = _WEEK_RE.search(label)
        if m:
            month = _month_number(m.group(3))
            if month is not None and year is not None:
                return datetime(year, month, int(m.group(2)))
        if week_anchor is not None:
            return week_anchor + timedelta(weeks=week_no - 1)
        return None

    def parse(
        self,
        ws,
        db: AttendanceDb,
        term: str,
        year: int | None,
        ingest_id: int | None,
        week_anchor: datetime | None = None,
    ) -> None:
        """Ingest one count-grid tab: a headcount per (week, ticket type)."""
        matrix = list(ws.iter_rows(values_only=True))
        _, cols = self._ticket_columns(matrix)
        event_type = _event_type_for(ws.title)
        event_id = db.upsert_event(f'{term}: {_strip_attendance(ws.title)}', event_type)

        for r, row in enumerate(matrix):
            label = row[0] if row else None
            m = _WEEK_NO_RE.search(label) if isinstance(label, str) else None
            if not m:
                continue
            week_no = int(m.group(1))
            activity_date = self._week_date(label, week_no, year, week_anchor)
            if activity_date is None:
                continue
            name = f'Level 2 Classes (Week {week_no})'
            activity_id = db.upsert_activity(
                event_id, name, activity_date, _activity_type_for(name, event_type), _difficulty_for(name)
            )
            for c, ticket_type in cols.items():  # c is a 0-based column index
                value = row[c] if c < len(row) else None
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue  # skip SUM/AVERAGE formula cells and blanks (bool is an int subclass)
                cell = f'{ws.title}!{get_column_letter(c + 1)}{r + 1}'
                db.record_count(activity_id, ticket_type, int(value), ingest_id=ingest_id, source_cell=cell)


class L2SOAttendanceParser:
    """Parse 2026-style 'L2 & SO Attendance' sheets.

    One row per dancer; dated session columns (the date sits in the header row). Each
    session cell holds a *ticket category*, not a yes/no marker:

    * ``Level 2 & Social`` — at that night's Level 2 lesson (they also stayed for the
      social, but per ESDS we record them as a Level 2 attendee only, not in the social).
    * ``Social-Only`` — at the social only.
    * ``Absent`` — signed up for Level 2 that night but didn't show: a Level 2 roster row
      with ``attended = 0``.
    * blank — no record.

    Each night therefore yields up to two activities, a Level 2 lesson and a social, and a
    dancer lands in exactly one of them per session. Activities are created lazily so a
    night with no record of a kind produces no empty activity. Only ``DNC-`` rows are
    read, so the COUNTIF/COUNTA summary formulas below the table are never mistaken for
    categories.
    """

    name = 'l2_so_attendance'

    # Category cell value (lower-cased) -> (activity it counts towards, did they attend).
    _CATEGORIES = {
        'level 2 & social': (ActivityType.LESSON, True),
        'social-only': (ActivityType.SOCIAL, True),
        'social only': (ActivityType.SOCIAL, True),
        'absent': (ActivityType.LESSON, False),  # signed up for Level 2, didn't show
    }

    def matches(self, ws) -> bool:
        """True if the sheet matches the L2 & SO layout: title contains L2 and SO, dancer_id column, sessions."""
        title_lower = ws.title.lower()
        if 'l2' not in title_lower or 'so' not in title_lower:
            return False
        matrix = list(ws.iter_rows(values_only=True))
        header = _roster_header(matrix)
        if header is None:
            return False
        sessions = _session_columns(matrix, header[0])
        return bool(sessions)

    def parse(
        self,
        ws,
        db: AttendanceDb,
        term: str,
        year: int | None,
        ingest_id: int | None,
        week_anchor: datetime | None = None,
    ) -> None:
        """Ingest one L2 & SO tab: a Level 2 lesson and a social per night, by ticket category."""
        matrix = list(ws.iter_rows(values_only=True))
        header_row, dancer_col = _roster_header(matrix)
        sessions = _session_columns(matrix, header_row)

        title = ws.title
        event_id = db.upsert_event(f'{term}: {_strip_attendance(title)}', _event_type_for(title))

        activities: dict[tuple, int] = {}  # (date_iso, ActivityType) -> activity_id

        def activity_for(dt: datetime, kind: ActivityType) -> int:
            key = (dt.date().isoformat(), kind)
            if key not in activities:
                name = f'Level 2 ({key[0]})' if kind == ActivityType.LESSON else f'Social ({key[0]})'
                difficulty = 'Level 2' if kind == ActivityType.LESSON else 'social'
                activities[key] = db.upsert_activity(event_id, name, dt, kind, difficulty)
            return activities[key]

        for r in range(header_row + 1, len(matrix)):
            row = matrix[r]
            did = row[dancer_col] if dancer_col < len(row) else None
            if not (isinstance(did, str) and did.startswith('DNC-')):
                continue
            for col, _, dt in sessions:
                value = row[col] if col < len(row) else None
                entry = self._CATEGORIES.get(value.strip().lower()) if isinstance(value, str) else None
                if entry is None:
                    continue
                kind, attended = entry
                cell = f'{title}!{get_column_letter(col + 1)}{r + 1}'
                db.record_attendance(
                    activity_for(dt, kind), did, attended=attended, ingest_id=ingest_id, source_cell=cell
                )


# ---------------------------------------------------------------------------
# One-off social register layout (Tea Dances etc.)
# ---------------------------------------------------------------------------


_CONCESSION_YES = {'yes', 'y', 'true', '1'}
_CONCESSION_NO = {'no', 'n', 'false', '0'}


def _concession_ticket(value) -> TicketType | None:
    """Map a boolean-ish 'Concession' cell to a ticket type. Blank means no information (None).

    The column records eligibility for the member/concession rate, not membership as such:
    'Yes' means the attendee is a member or concession (not ordinary) without saying which —
    hence MEMBER_OR_CONCESSION — and 'No' means they paid the ordinary rate. A non-empty value
    that is neither yes nor no is UNKNOWN: the source said *something* about the rate that we
    can't read, which is distinct from a blank cell (no information at all, left as None/NULL).
    """
    if isinstance(value, bool):
        return TicketType.MEMBER_OR_CONCESSION if value else TicketType.ORDINARY
    if not isinstance(value, str) or not (v := value.strip().lower()):
        return None
    if v in _CONCESSION_YES:
        return TicketType.MEMBER_OR_CONCESSION
    if v in _CONCESSION_NO:
        return TicketType.ORDINARY
    return TicketType.UNKNOWN


# Header of the per-attendee attendance marker on a one-off register. Different years use
# different words ('Present?' on the Tea Dances, 'Attended' on the Sunday Socials). Matched
# exactly so 'Weeks attended'/'Times attended' summary columns are never mistaken for it.
_ATTENDANCE_HEADERS = {'present', 'present?', 'attended', 'attended?'}


def _is_attendance_header(label) -> bool:
    """True if a header cell labels the per-attendee attendance marker on a social register."""
    return isinstance(label, str) and label.strip().lower() in _ATTENDANCE_HEADERS


def _register_blocks(header: tuple) -> list[tuple]:
    """Find each (dancer_id, concession_col_or_None, marker_col) block across a register header.

    These registers print the attendee list as one or more halves side by side, each a repeat
    of e.g. '# | dancer_id | redacted | Concession | Present?'. Each dancer_id column owns the
    Concession and attendance-marker columns to its right, up to the next dancer_id column.
    """
    did_cols = [j for j, c in enumerate(header) if isinstance(c, str) and c.strip() == 'dancer_id']
    blocks = []
    for k, dc in enumerate(did_cols):
        end = did_cols[k + 1] if k + 1 < len(did_cols) else len(header)
        marker = concession = None
        for j in range(dc + 1, end):
            label = header[j].strip().lower() if isinstance(header[j], str) else ''
            if marker is None and _is_attendance_header(header[j]):
                marker = j
            if concession is None and label == 'concession':
                concession = j
        if marker is not None:
            blocks.append((dc, concession, marker))
    return blocks


class SocialRegisterParser:
    """Parse a one-off social's named register (Tea Dances, Sunday Socials, parties).

    One dated event, one social activity. Each row holds one attendee per side-by-side block:
    a ``dancer_id``, optionally a ``Concession`` Yes/No (ticket type), and an attendance marker
    headed ``Present?`` or ``Attended`` (☑️/x attended, ❎/blank not). Distinguished from the
    weekly roster by having that marker column and *no* dated session columns. The event date
    comes from the sheet tab name or the filename, since the cells carry none. A dancer_id
    repeated across rows (un-renamed extra tickets) becomes one named attendee plus anonymous
    extras, as in the roster layout.
    """

    name = 'social_register'

    def matches(self, ws) -> bool:
        """True for a dancer_id register whose marker column is predominantly attendance marks.

        A 'Present?'/'Attended' header is not enough: a booking list can carry such a column
        that is mostly empty with the odd free-text note ('no show', 'paid £10 cash on door'),
        which must not be read as an attendance register. So the non-empty marker cells (against
        DNC- rows) must be mostly real yes/no marks. Dated session columns disqualify it — that
        is the weekly roster's job.
        """
        matrix = list(ws.iter_rows(values_only=True))
        header = _roster_header(matrix)
        if header is None:
            return False
        header_row = header[0]
        if _session_columns(matrix, header_row):
            return False
        filled, recognised = self._marker_fill(matrix, header_row, _register_blocks(matrix[header_row]))
        return recognised > 0 and recognised >= filled / 2

    @staticmethod
    def _marker_fill(matrix: list[tuple], header_row: int, blocks: list[tuple]) -> tuple[int, int]:
        """Count (non-empty, recognised-as-marker) marker cells against DNC- rows across blocks."""
        filled = recognised = 0
        for row in matrix[header_row + 1 :]:
            for did_col, _, marker_col in blocks:
                did = row[did_col] if did_col < len(row) else None
                if not (isinstance(did, str) and did.startswith('DNC-')):
                    continue
                cell = row[marker_col] if marker_col < len(row) else None
                if cell is None or (isinstance(cell, str) and not cell.strip()):
                    continue
                filled += 1
                recognised += _is_attendance_marker(cell)
        return filled, recognised

    def parse(
        self,
        ws,
        db: AttendanceDb,
        term: str,
        year: int | None,
        ingest_id: int | None,
        week_anchor: datetime | None = None,
    ) -> None:
        """Ingest one social register: a single dated social activity with named attendance."""
        matrix = list(ws.iter_rows(values_only=True))
        header_row, _ = _roster_header(matrix)
        blocks = _register_blocks(matrix[header_row])
        # The event date is in the tab name (e.g. '26 March 23') or the filename, not the cells.
        event_date = _date_from_title(ws.title) or _date_from_title(term)
        if event_date is None or not blocks:
            return  # without an event date or any attendee block there is nothing to anchor

        event_id = db.upsert_event(_strip_attendance(term), EventType.SOCIAL)
        activity_id = db.upsert_activity(event_id, 'Social', event_date, ActivityType.SOCIAL, 'social')

        attendees = self._collect(matrix, header_row, blocks, ws.title)
        anonymous_extra = 0
        for did, (present_count, ticket_type, cell) in attendees.items():
            db.record_attendance(
                activity_id,
                did,
                attended=present_count >= 1,
                ticket_type=ticket_type,
                ingest_id=ingest_id,
                source_cell=cell,
            )
            anonymous_extra += max(present_count - 1, 0)  # un-renamed duplicate tickets → anonymous attendees
        if anonymous_extra:
            db.record_count(activity_id, None, anonymous_extra, ingest_id=ingest_id, source_cell=ws.title)

    @staticmethod
    def _collect(matrix: list[tuple], header_row: int, blocks: list[tuple], title: str) -> dict:
        """Aggregate per dancer_id across every block and row: (present_count, ticket_type, cell)."""
        out: dict[str, list] = {}
        for r in range(header_row + 1, len(matrix)):
            row = matrix[r]
            for did_col, conc_col, pres_col in blocks:
                did = row[did_col] if did_col < len(row) else None
                if not (isinstance(did, str) and did.startswith('DNC-')):
                    continue
                attended = _is_true(row[pres_col]) if pres_col < len(row) else False
                ticket = _concession_ticket(row[conc_col]) if conc_col is not None and conc_col < len(row) else None
                acc = out.setdefault(did, [0, None, f'{title}!{get_column_letter(did_col + 1)}{r + 1}'])
                acc[0] += int(attended)
                acc[1] = acc[1] or ticket
        return {did: tuple(acc) for did, acc in out.items()}


PARSERS = [
    RosterParser(),
    Level2TallyParser(),
    Level2CountGridParser(),
    L2SOAttendanceParser(),
    SocialRegisterParser(),
]


# ---------------------------------------------------------------------------
# Folder dispatcher
# ---------------------------------------------------------------------------


@dataclass
class IngestReport:
    handled: list[tuple] = field(default_factory=list)  # (file, sheet, parser_name)
    unhandled: list[tuple] = field(default_factory=list)  # (file, sheet)

    def summary(self) -> str:
        """Human-readable report of what was ingested and what was skipped."""
        lines = [f'{len(self.handled)} sheet(s) ingested, {len(self.unhandled)} skipped.']
        for f, s, p in self.handled:
            lines.append(f'  [{p}] {f} :: {s}')
        if self.unhandled:
            lines.append('Skipped (no matching parser — would need a new one):')
            for f, s in self.unhandled:
                lines.append(f'  - {f} :: {s}')
        return '\n'.join(lines)


def _strip_attendance(s: str) -> str:
    """Tidy a tab title for use as an event name.

    Drops 'Attendance' (and its 31-char-truncated 'Attendanc') and 'Attendees', which belong
    on a spreadsheet tab but are noise in an event name, and normalises 'Level 2/3' (and '2-3',
    '2 & 3') down to plain 'Level 2' since ESDS ran the two levels as one session.
    """
    s = _LEVEL_2_3_RE.sub('Level 2', s)
    return re.sub(r'\s+', ' ', re.sub(r'\bAttend(?:anc|ee)\w*', '', s, flags=re.IGNORECASE)).strip()


def _term_from(path) -> str:
    return _strip_attendance(path.stem.removesuffix('_pseudonymised'))


def _resolve_year(wb) -> int | None:
    years: Counter = Counter()
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for value in row:
                dt = _parse_dt(value)
                if dt is not None:
                    years[dt.year] += 1
    return years.most_common(1)[0][0] if years else None


def _week_anchor(wb) -> datetime | None:
    """The earliest date anywhere in the workbook — the Week-1 date for weekly-block tabs.

    Older 'Week N' sheets lost their per-column date formulas in pseudonymisation, and the
    count-grid tabs (e.g. 'Level 2-3') never carried dates at all. A workbook like these is
    one weekly term, so an undated 'Week N' is placed at this anchor + 7*(N-1). Tabs that
    carry their own session dates never consult it.
    """
    earliest: datetime | None = None
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for value in row:
                dt = _parse_dt(value)
                if dt is not None and (earliest is None or dt < earliest):
                    earliest = dt
    return earliest


# Sheet titles (lower-cased) that never hold attendance: substrings to skip outright.
# 'member'/'mail' are membership and mailing-list dumps; 'exceptions'/'loyalty'/'tickets'
# are bookkeeping tabs that carry no per-session attendance.
_NON_ATTENDANCE_SHEETS = ('readme', 'member', 'mail', 'exceptions', 'loyalty', 'tickets')


def ingest_folder(output_root, db: AttendanceDb, parsers: list | None = None) -> IngestReport:
    """Walk the attendance_outputs tree, dispatch each sheet to a matching parser.

    2022 is skipped wholesale: its spreadsheets predate any layout the parsers target
    and the data quality is too poor to ingest reliably.
    """
    parsers = parsers if parsers is not None else PARSERS
    report = IngestReport()
    for path in sorted(output_root.rglob('*.xlsx')):
        if path.name.startswith(('~$', '.~lock')):
            continue
        rel = path.relative_to(output_root).as_posix()
        if rel.startswith('2022'):
            continue  # 2022 predates the supported layouts; skip wholesale
        wb = openpyxl.load_workbook(path, data_only=False)
        term, year, anchor = _term_from(path), _resolve_year(wb), _week_anchor(wb)
        for ws in wb.worksheets:
            ws_lower = ws.title.strip().lower()
            if any(pattern in ws_lower for pattern in _NON_ATTENDANCE_SHEETS):
                continue  # skip non-attendance sheets
            parser = next((p for p in parsers if p.matches(ws)), None)
            if parser is None:
                report.unhandled.append((rel, ws.title))
                continue
            ingest_id = db.start_ingest(rel, ws.title)
            parser.parse(ws, db, term=term, year=year, ingest_id=ingest_id, week_anchor=anchor)
            report.handled.append((rel, ws.title, parser.name))
    return report
