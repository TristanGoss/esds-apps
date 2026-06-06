"""Attendance ingestion — layout parsers and a folder dispatcher.

Each parser recognises one spreadsheet *layout* (``matches``) and ingests it into the
attendance database (``parse``). Parsers map to layouts, not years: one workbook may
mix layouts across tabs, and the same layout recurs unpredictably across years. The
dispatcher tries each parser per sheet and reports any sheet no parser claimed (those
would need a new parser).

All input is read from the pseudonymised ``attendance_outputs`` tree. Two layouts are
supported so far:

* **Roster** — one row per dancer, a boolean per session column (date in the row above
  the header). Level 1 tabs and one-off social tabs.
* **Level 2 tally** — a checkbox grid summed by COUNTIF into per-(week, ticket-type)
  headcounts, for two activity groups (Level 2 Classes / Social Only).
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

from esds_apps.attendance.attendance_db import ActivityType, AttendanceDb, EventType, TicketType

_TICKET_LABELS = {
    'members': TicketType.MEMBER,
    'concessions': TicketType.CONCESSION,
    'non-members': TicketType.NON_MEMBER,
    'non members': TicketType.NON_MEMBER,
}
_GROUP_HEADERS = {'level 2 classes', 'social only'}
_WEEK_RE = re.compile(r'Week\s*(\d+)\s*\(\s*(\d{1,2})\s+([A-Za-z]+)\)', re.IGNORECASE)
_COUNTIF_RE = re.compile(r'COUNTIF\(\s*([A-Za-z]+)(\d+)\s*:\s*([A-Za-z]+)(\d+)', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------


# Markers meaning "attended". Rosters vary by year: booleans (True), 'True'/'TRUE'
# strings, ticks ('x', 'yes'), and check emoji (☑️/✅/✔/✓). Crosses (❎/❌) and
# 'Refunded' fall through to False — a refunded ticket is a non-attendance.
_TRUE_WORDS = {'true', 'yes', 'y', 'x', '1', '✓', '✔'}
_TRUE_EMOJI = ('☑', '✅', '✔', '✓')  # ☑ ✅ ✔ ✓


def _is_true(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        return s.startswith('true') or s in _TRUE_WORDS or any(e in value for e in _TRUE_EMOJI)
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


def _activity_type_for(label: str) -> ActivityType:
    return ActivityType.SOCIAL if 'social' in label.lower() else ActivityType.LESSON


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


def _session_columns(matrix: list[tuple], header_row: int) -> list[tuple]:
    """Columns whose cell in the row above the header is a date — i.e. session columns.

    Returns a list of (col_index, activity_name, datetime, ActivityType).
    """
    if header_row == 0:
        return []
    date_row, head = matrix[header_row - 1], matrix[header_row]
    out = []
    for c in range(len(date_row)):
        dt = _parse_dt(date_row[c]) if c < len(date_row) else None
        if dt is None:
            continue
        name = head[c] if c < len(head) and isinstance(head[c], str) and head[c].strip() else dt.date().isoformat()
        out.append((c, str(name).strip(), dt, _activity_type_for(str(name))))
    return out


class RosterParser:
    name = 'roster'

    def matches(self, ws) -> bool:
        """True if the sheet has a dancer_id header and at least one dated session column."""
        matrix = list(ws.iter_rows(values_only=True))
        header = _roster_header(matrix)
        return header is not None and bool(_session_columns(matrix, header[0]))

    def parse(self, ws, db: AttendanceDb, term: str, year: int | None, ingest_id: int | None) -> None:
        """Ingest one roster tab: an event, an activity per session column, attendance rows."""
        matrix = list(ws.iter_rows(values_only=True))
        header_row, dancer_col = _roster_header(matrix)
        sessions = _session_columns(matrix, header_row)

        title = ws.title
        is_social = 'social' in title.lower() and 'level' not in title.lower()
        event_id = db.upsert_event(f'{term}: {title}', EventType.SOCIAL if is_social else EventType.COURSE)

        for col, name, dt, atype in sessions:
            activity_id = db.upsert_activity(event_id, name, dt, atype)
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
                db.record_attendance(activity_id, did, attended=used >= 1, ingest_id=ingest_id, source_cell=cell)
                anonymous_extra += max(used - 1, 0)  # un-renamed duplicate tickets → anonymous attendees
            if anonymous_extra:
                db.record_count(activity_id, None, anonymous_extra, ingest_id=ingest_id, source_cell=cell)


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
            if stripped.lower() in _GROUP_HEADERS:
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
                if value.strip().lower() in _GROUP_HEADERS:
                    has_group = True
                if _WEEK_RE.search(value):
                    has_week = True
        return has_countif and has_group and has_week

    def parse(self, ws, db: AttendanceDb, term: str, year: int | None, ingest_id: int | None) -> None:
        """Ingest one tally tab: a headcount per (week, activity group, ticket type)."""
        matrix = list(ws.iter_rows(values_only=True))
        t = _scan_tally(matrix)
        event_id = db.upsert_event(f'{term}: {ws.title}', EventType.COURSE)

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
        activity_id = db.upsert_activity(
            event_id, f'{group} (Week {week_no})', activity_date, _activity_type_for(group)
        )
        cell = f'{title}!{get_column_letter(col1)}{row1}'
        db.record_count(activity_id, ticket_type, head_count, ingest_id=ingest_id, source_cell=cell)


PARSERS = [RosterParser(), Level2TallyParser()]


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


def _term_from(path) -> str:
    stem = path.stem.removesuffix('_pseudonymised')
    return re.sub(r'\s+', ' ', re.sub(r'\bAttendance\b', '', stem, flags=re.IGNORECASE)).strip()


def _resolve_year(wb) -> int | None:
    years: Counter = Counter()
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for value in row:
                dt = _parse_dt(value)
                if dt is not None:
                    years[dt.year] += 1
    return years.most_common(1)[0][0] if years else None


def ingest_folder(output_root, db: AttendanceDb, parsers: list | None = None) -> IngestReport:
    """Walk the attendance_outputs tree, dispatch each sheet to a matching parser."""
    parsers = parsers if parsers is not None else PARSERS
    report = IngestReport()
    for path in sorted(output_root.rglob('*.xlsx')):
        if path.name.startswith(('~$', '.~lock')):
            continue
        wb = openpyxl.load_workbook(path, data_only=False)
        term, year = _term_from(path), _resolve_year(wb)
        rel = path.relative_to(output_root).as_posix()
        for ws in wb.worksheets:
            parser = next((p for p in parsers if p.matches(ws)), None)
            if parser is None:
                report.unhandled.append((rel, ws.title))
                continue
            ingest_id = db.start_ingest(rel, ws.title)
            parser.parse(ws, db, term=term, year=year, ingest_id=ingest_id)
            report.handled.append((rel, ws.title, parser.name))
    return report
