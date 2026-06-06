from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from esds_apps.attendance import ingest
from esds_apps.attendance.attendance_db import open_db


@pytest.fixture
def db(tmp_path):
    d = open_db(tmp_path / 'attendance.sqlite')
    yield d
    d.close()


def _roster_ws(title='Level 1 Attendance'):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['E1'], ws['F1'] = datetime(2025, 5, 22), datetime(2025, 5, 29)  # dates above the header
    ws['B2'], ws['C2'], ws['D2'], ws['E2'], ws['F2'] = '#', 'dancer_id', 'redacted', 'Week 1', 'Week 2'
    # (dancer, week1, week2) — mixed truthy markers and a duplicate ticket for DNC-1
    data = [
        ('DNC-1', True, False),
        ('DNC-2', '☑️', 'False'),
        ('DNC-3', 'x', 'Refunded'),
        ('DNC-1', True, True),  # second ticket DNC-1 bought but didn't rename
    ]
    for i, (did, w1, w2) in enumerate(data, start=3):
        ws[f'C{i}'], ws[f'E{i}'], ws[f'F{i}'] = did, w1, w2
    return ws


def _tally_ws(title='Level 2 & Social Only Attendanc'):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['C1'] = 'Level 2 Classes'
    ws['C2'], ws['F2'] = 'Members', '=COUNTIF(C3:E4, "TRUE")'
    ws['H2'], ws['K2'] = 'Concessions', '=COUNTIF(H3:I4, "TRUE")'
    ws['B3'] = 'Week 1 (22 May)'
    ws['C3'], ws['D3'], ws['E3'] = True, True, False  # members: 3 ticks
    ws['C4'], ws['D4'], ws['E4'] = True, False, False
    ws['H3'], ws['I3'] = True, False  # concessions: 2 ticks
    ws['H4'], ws['I4'] = False, True
    return ws


def _count_grid_ws(title='Level 2'):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['B4'], ws['C4'], ws['D4'], ws['E4'] = 'Members', 'Concessions', 'Non-Members', 'Totals'
    ws['A5'], ws['B5'], ws['C5'], ws['D5'], ws['E5'] = 'Week 1\n(11 Apr)', 16, 3, 8, '=SUM(B5:D5)'
    ws['A6'], ws['B6'], ws['C6'], ws['D6'], ws['E6'] = 'Week 2\n(18 Apr)', 23, 1, 7, '=SUM(B6:D6)'
    ws['A8'], ws['B8'] = 'Mean', '=AVERAGE(B5:B6)'  # must be ignored (no week label)
    return ws


def _count_grid_undated_ws(title='Level 2-3'):
    """An older count grid: bare 'Week N' labels with no per-row dates, ticket-type headers.

    Dates must come from the workbook anchor (Week 1 + 7*(N-1)), and the 'Level 2-3' title
    must normalise to plain 'Level 2'.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['B4'], ws['C4'], ws['D4'], ws['E4'] = 'Members', 'Concessions', 'Non-Members', 'Totals'
    ws['A5'], ws['B5'], ws['C5'], ws['D5'], ws['E5'] = 'Week 1', 21, 0, 3, '=SUM(B5:D5)'
    ws['A6'], ws['B6'], ws['C6'], ws['D6'], ws['E6'] = 'Week 2', 27, 3, 6, '=SUM(B6:D6)'
    return ws


def _roster_weekly_ws(title='Level 1'):
    """A roster where only Week 1 carries a date; Weeks 2-3 are bare 'Week N' labels.

    Mimics the older Level 1 tabs whose '=D1+7' week-date formulas were stripped to nothing
    by pseudonymisation, so all but the first week date must be inferred from the anchor.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['D1'] = datetime(2023, 11, 9)  # only Week 1 dated (E1/F1 held '=D1+7', now gone)
    ws['B2'], ws['C2'], ws['D2'], ws['E2'], ws['F2'] = 'dancer_id', 'redacted', 'Week 1', 'Week 2', 'Week 3'
    ws['B3'], ws['D3'], ws['E3'], ws['F3'] = 'DNC-1', '☑️', '☑️', '❎'
    return ws


def _l2_so_ws(title='2026 L2 & SO Attendance'):
    """A 2026-style sheet with dancer_id + dated columns holding ticket categories, not markers.

    The final row is a COUNTIF summary like the real sheets carry: it has no DNC- id, so it
    must never be read as a category (the bug that spawned phantom '=countif(...)' activities).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['C1'] = datetime(2026, 1, 15)
    ws['D1'] = datetime(2026, 1, 22)
    ws['B2'], ws['C2'], ws['D2'] = 'dancer_id', 'Session 1', 'Session 2'
    ws['B3'], ws['C3'], ws['D3'] = 'DNC-1', 'Level 2 & Social', 'Social-Only'
    ws['B4'], ws['C4'], ws['D4'] = 'DNC-2', 'Absent', 'Level 2 & Social'
    ws['C5'], ws['D5'] = '=countif(C3:C4,"Absent")', '=countif(D3:D4,"Social-Only")'
    return ws


def _l1_attendance_2026_ws(title='2026 L1 Attendance'):
    """A 2026 L1 Attendance sheet: dates in the header row, session names optional."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['C1'] = datetime(2026, 1, 15)
    ws['D1'] = datetime(2026, 1, 22)
    ws['B2'], ws['C2'], ws['D2'] = 'dancer_id', 'Week 1', 'Week 2'
    ws['B3'], ws['C3'], ws['D3'] = 'DNC-1', True, False
    ws['B4'], ws['C4'], ws['D4'] = 'DNC-2', 'Yes', True
    ws['B5'], ws['C5'], ws['D5'] = 'DNC-1', True, 'x'  # duplicate ticket
    return ws


def _social_register_ws(title='Online bookings'):
    """A one-off social register: the attendee list in two side-by-side halves.

    Each half is '# | dancer_id | redacted | Concession | Present?'; the sheet has no dates
    (the event date lives in the filename) and no dated session columns. DNC-1 appears twice
    (an un-renamed extra ticket); DNC-5 was absent (❎).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['A1'] = 'Tea Dance 25th Feb 2024'  # a title note above the header
    ws['B2'], ws['C2'], ws['D2'], ws['E2'], ws['F2'] = '#', 'dancer_id', 'redacted', 'Concession', 'Present?'
    ws['H2'], ws['I2'], ws['J2'], ws['K2'], ws['L2'] = '#', 'dancer_id', 'redacted', 'Concession', 'Present?'
    # left half
    ws['B3'], ws['C3'], ws['E3'], ws['F3'] = 1, 'DNC-1', 'No', '☑️'
    ws['B4'], ws['C4'], ws['E4'], ws['F4'] = 2, 'DNC-2', 'Yes', '☑️'
    ws['B5'], ws['C5'], ws['E5'], ws['F5'] = 3, 'DNC-1', 'No', '☑️'  # same person, second ticket
    # right half (different people)
    ws['H3'], ws['I3'], ws['K3'], ws['L3'] = 39, 'DNC-3', 'Yes', '☑️'
    ws['H4'], ws['I4'], ws['K4'], ws['L4'] = 40, 'DNC-5', 'No', '❎'  # booked, absent
    return ws


def _attended_register_ws(title='26 March 23'):
    """A Sunday-Social-style register: a single block, an 'Attended' x marker, no concession.

    The event date is the tab name, not a cell. DNC-2 appears twice (extra ticket); DNC-3
    has a blank Attended cell (booked but absent).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws['A1'], ws['B1'], ws['C1'], ws['D1'], ws['E1'] = 'dancer_id', 'redacted', 'redacted', 'Order', 'Attended'
    ws['A2'], ws['D2'], ws['E2'] = 'DNC-1', 'OAOX', 'x'
    ws['A3'], ws['D3'], ws['E3'] = 'DNC-2', 'O7LY', 'x'
    ws['A4'], ws['D4'], ws['E4'] = 'DNC-2', 'O7LY', 'x'  # un-renamed extra ticket
    ws['A5'], ws['D5'] = 'DNC-3', 'O00J'  # blank Attended -> booked but absent
    return ws


# ---- value helpers ----


@pytest.mark.parametrize('value', [True, 'True', 'TRUE', 'true', '=TRUE()', 'x', 'yes', '☑️', '✅'])
def test_is_true_positives(value):
    assert ingest._is_true(value)


@pytest.mark.parametrize('value', [False, 'False', '=FALSE()', 'Refunded', '❎', '❌', '', None, 0])
def test_is_true_negatives(value):
    assert not ingest._is_true(value)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('Level 2 Classes', True),  # plural (older sheets)
        ('Level 2 Class', True),  # singular (2025-H2 sheets)
        ('Social Only', True),
        ('Members', False),
        ('Level 1', False),
    ],
)
def test_is_group_header(text, expected):
    assert ingest._is_group_header(text) is expected


def test_parse_dt_handles_datetime_and_isoish_string():
    assert ingest._parse_dt(datetime(2025, 5, 22)).year == 2025
    assert ingest._parse_dt('2025-05-22 00:00:00').month == 5
    assert ingest._parse_dt('not a date') is None


def test_term_from_strips_attendance_and_suffix():
    assert ingest._term_from(Path('May-Jun 2025 Attendance_pseudonymised.xlsx')) == 'May-Jun 2025'
    assert ingest._term_from(Path('Attendance Nov-Dec 2024_pseudonymised.xlsx')) == 'Nov-Dec 2024'


def test_strip_attendance_handles_full_and_truncated_word():
    assert ingest._strip_attendance('Level 1 Attendance') == 'Level 1'
    assert ingest._strip_attendance('Level 2 & Social Only Attendanc') == 'Level 2 & Social Only'  # 31-char cut
    assert (
        ingest._strip_attendance('Sunday Social Attendees 2023-03-25') == 'Sunday Social 2023-03-25'
    )  # 'Attendees' too
    assert ingest._strip_attendance('Tea Dance 13th Oct') == 'Tea Dance 13th Oct'  # untouched


@pytest.mark.parametrize(
    ('title', 'expected'),
    [
        ('Level 1 Attendance', 'course'),
        ('Level 2 & Social Only Attendanc', 'course'),  # 'level' beats 'social'
        ('Teachers Choice 12th Dec', 'course'),
        ('Sat 17th Workshop', 'workshop'),
        ('Begn Charleston 24th Feb', 'workshop'),
        ('Christmas Party 19th Dec', 'social'),
        ('Tea Dance 13th Oct', 'social'),
        ('Stockbridge Swingout', 'weekender'),  # swingout is a weekender, not a social
        ('Sun 27th Social', 'social'),
    ],
)
def test_event_type_for_classifies_by_title(title, expected):
    assert ingest._event_type_for(title) == expected


@pytest.mark.parametrize(
    ('texts', 'expected'),
    [
        (('Level 1 Class',), 'Level 1'),
        (('Level 2 Classes (Week 1)',), 'Level 2'),
        (('L3 taster',), 'Level 3'),
        (('Begn Charleston',), 'beginners'),
        (('Intermediate Lindy',), 'intermediate'),
        (('Advanced aerials',), 'advanced'),
        (('Social',), None),
        (('Week 1', 'Level 1 Attendance'), 'Level 1'),  # falls back to the second text
        (('Beginners drop-in', 'Level 2 term'), 'beginners'),  # first text wins
    ],
)
def test_difficulty_for(texts, expected):
    assert ingest._difficulty_for(*texts) == expected


# ---- matchers ----


def test_roster_matches_roster_only():
    assert ingest.RosterParser().matches(_roster_ws())
    assert not ingest.RosterParser().matches(_tally_ws())
    assert not ingest.RosterParser().matches(_l2_so_ws())  # rejects category strings


def test_tally_matches_tally_only():
    assert ingest.Level2TallyParser().matches(_tally_ws())
    assert not ingest.Level2TallyParser().matches(_roster_ws())


def test_count_grid_matches_grid_only():
    assert ingest.Level2CountGridParser().matches(_count_grid_ws())
    assert ingest.Level2CountGridParser().matches(_count_grid_undated_ws())  # bare 'Week N' still matches
    assert not ingest.Level2CountGridParser().matches(_tally_ws())  # has COUNTIF
    assert not ingest.Level2CountGridParser().matches(_roster_ws())
    assert not ingest.Level2TallyParser().matches(_count_grid_ws())  # no COUNTIF


# ---- roster parsing ----


def test_roster_parse_creates_event_and_activities(db):
    ingest.RosterParser().parse(_roster_ws(), db, term='May-Jun 2025', year=2025, ingest_id=None)
    ev = db.conn.execute('SELECT name, event_type FROM event').fetchone()
    assert ev == ('May-Jun 2025: Level 1', 'course')  # 'Attendance' stripped from the name
    acts = db.conn.execute('SELECT name, date FROM activity ORDER BY date').fetchall()
    assert acts == [('Week 1', '2025-05-22'), ('Week 2', '2025-05-29')]
    # difficulty comes from the sheet title ('Level 1 Attendance') since 'Week N' has none
    assert {r[0] for r in db.conn.execute('SELECT DISTINCT difficulty FROM activity')} == {'Level 1'}


def test_roster_2026_format_with_dates_in_header(db):
    """The 2026 L1 format has dates in the header row, not the row above."""
    ingest.RosterParser().parse(_l1_attendance_2026_ws(), db, term='Jan 2026', year=2026, ingest_id=None)
    ev = db.conn.execute('SELECT name, event_type FROM event').fetchone()
    assert ev == ('Jan 2026: 2026 L1', 'course')
    acts = db.conn.execute('SELECT name, date FROM activity ORDER BY date').fetchall()
    # Session names come from the header row (C2='Week 1', D2='Week 2'); dates from header row itself
    assert acts == [('Week 1', '2026-01-15'), ('Week 2', '2026-01-22')]
    # difficulty parsed from the title '2026 L1 Attendance' → 'Level 1'
    assert {r[0] for r in db.conn.execute('SELECT DISTINCT difficulty FROM activity')} == {'Level 1'}
    # Verify the attendance was recorded correctly
    rows = db.conn.execute(
        'SELECT dancer_id, COUNT(*) as activity_count FROM attendance GROUP BY dancer_id ORDER BY dancer_id'
    ).fetchall()
    assert rows == [('DNC-1', 2), ('DNC-2', 2)]  # DNC-1 and DNC-2 each have 2 attendance records


def test_roster_parse_reads_mixed_truthy_markers(db):
    ingest.RosterParser().parse(_roster_ws(), db, term='T', year=2025, ingest_id=None)
    # Week 1: DNC-1 (True), DNC-2 (☑️), DNC-3 ('x') all attended
    rows = db.conn.execute(
        'SELECT dancer_id, attended FROM attendance JOIN activity USING(activity_id) '
        "WHERE name='Week 1' ORDER BY dancer_id"
    ).fetchall()
    assert rows == [('DNC-1', 1), ('DNC-2', 1), ('DNC-3', 1)]


def test_roster_parse_duplicate_ticket_becomes_anonymous_count(db):
    ingest.RosterParser().parse(_roster_ws(), db, term='T', year=2025, ingest_id=None)
    # DNC-1 used two tickets in Week 1 → one named row + one anonymous head.
    anon = db.conn.execute(
        "SELECT ticket_type, head_count FROM attendance_count JOIN activity USING(activity_id) WHERE name='Week 1'"
    ).fetchall()
    assert anon == [(None, 1)]
    # Week 2: DNC-1's two tickets, only one used → named attended, no anonymous extra.
    assert db.conn.execute(
        "SELECT COUNT(*) FROM attendance_count JOIN activity USING(activity_id) WHERE name='Week 2'"
    ).fetchone() == (0,)


def test_roster_refunded_and_false_are_absent(db):
    ingest.RosterParser().parse(_roster_ws(), db, term='T', year=2025, ingest_id=None)
    w2 = dict(
        db.conn.execute(
            "SELECT dancer_id, attended FROM attendance JOIN activity USING(activity_id) WHERE name='Week 2'"
        ).fetchall()
    )
    assert w2['DNC-2'] == 0  # 'False'
    assert w2['DNC-3'] == 0  # 'Refunded'


def test_roster_infers_weekly_dates_from_anchor(db):
    """Only Week 1 is dated; Weeks 2-3 are placed at anchor + 7*(N-1)."""
    ingest.RosterParser().parse(
        _roster_weekly_ws(), db, term='Nov-Dec 2023', year=2023, ingest_id=None, week_anchor=datetime(2023, 11, 9)
    )
    acts = db.conn.execute('SELECT name, date FROM activity ORDER BY date').fetchall()
    assert acts == [('Week 1', '2023-11-09'), ('Week 2', '2023-11-16'), ('Week 3', '2023-11-23')]


def test_roster_without_anchor_keeps_only_dated_weeks(db):
    """No anchor: undated 'Week N' columns are dropped rather than guessed."""
    ingest.RosterParser().parse(_roster_weekly_ws(), db, term='Nov-Dec 2023', year=2023, ingest_id=None)
    assert db.conn.execute('SELECT name FROM activity').fetchall() == [('Week 1',)]


def test_roster_social_event_forces_social_activities(db):
    # 'Week 1'/'Week 2' headers contain no 'social', but the event classifies as social,
    # so the activities must still be social — not lesson.
    ingest.RosterParser().parse(_roster_ws(title='Christmas Party 19th Dec'), db, term='T', year=2024, ingest_id=None)
    assert db.conn.execute('SELECT event_type FROM event').fetchone() == ('social',)
    assert {r[0] for r in db.conn.execute('SELECT DISTINCT activity_type FROM activity')} == {'social'}
    # social activities get difficulty 'social', not NULL
    assert {r[0] for r in db.conn.execute('SELECT DISTINCT difficulty FROM activity')} == {'social'}


# ---- tally parsing ----


def test_tally_parse_counts_ticks_per_ticket_type(db):
    ingest.Level2TallyParser().parse(_tally_ws(), db, term='May-Jun 2025', year=2025, ingest_id=None)
    rows = db.conn.execute(
        'SELECT a.name, a.date, ac.ticket_type, ac.head_count '
        'FROM attendance_count ac JOIN activity a USING(activity_id) ORDER BY ac.ticket_type'
    ).fetchall()
    assert rows == [
        ('Level 2 Classes (Week 1)', '2025-05-22', 'concession', 2),
        ('Level 2 Classes (Week 1)', '2025-05-22', 'member', 3),
    ]


def test_tally_parse_sets_event_and_activity_type(db):
    ingest.Level2TallyParser().parse(_tally_ws(), db, term='T', year=2025, ingest_id=None)
    assert db.conn.execute('SELECT event_type FROM event').fetchone() == ('course',)
    assert db.conn.execute('SELECT DISTINCT activity_type FROM activity').fetchone() == ('lesson',)
    # difficulty parsed from the group label 'Level 2 Classes'
    assert db.conn.execute('SELECT DISTINCT difficulty FROM activity').fetchone() == ('Level 2',)


# ---- count-grid parsing ----


def test_count_grid_parse_records_counts_and_skips_mean_row(db):
    ingest.Level2CountGridParser().parse(_count_grid_ws(), db, term='Apr-May 2024', year=2024, ingest_id=None)
    # two week rows -> two activities; the 'Mean' row has no week label and is ignored
    acts = db.conn.execute('SELECT name, date, activity_type, difficulty FROM activity ORDER BY date').fetchall()
    assert acts == [
        ('Level 2 Classes (Week 1)', '2024-04-11', 'lesson', 'Level 2'),
        ('Level 2 Classes (Week 2)', '2024-04-18', 'lesson', 'Level 2'),
    ]
    week1 = db.conn.execute(
        'SELECT ac.ticket_type, ac.head_count FROM attendance_count ac JOIN activity a USING(activity_id) '
        "WHERE a.name='Level 2 Classes (Week 1)' ORDER BY ac.ticket_type"
    ).fetchall()
    assert week1 == [('concession', 3), ('member', 16), ('non_member', 8)]


def test_count_grid_undated_uses_anchor_and_normalises_level(db):
    """Bare 'Week N' rows fall back to anchor + 7*(N-1); the 'Level 2-3' title becomes 'Level 2'."""
    ingest.Level2CountGridParser().parse(
        _count_grid_undated_ws(), db, term='Nov-Dec 2023', year=2023, ingest_id=None, week_anchor=datetime(2023, 11, 9)
    )
    assert db.conn.execute('SELECT name FROM event').fetchone() == ('Nov-Dec 2023: Level 2',)
    acts = db.conn.execute('SELECT name, date, difficulty FROM activity ORDER BY date').fetchall()
    assert acts == [
        ('Level 2 Classes (Week 1)', '2023-11-09', 'Level 2'),
        ('Level 2 Classes (Week 2)', '2023-11-16', 'Level 2'),
    ]


# ---- helpers ----


def test_week_anchor_picks_earliest_date():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws['A1'], ws['A2'], ws['A3'] = datetime(2023, 11, 23), datetime(2023, 11, 9), 'not a date'
    assert ingest._week_anchor(wb) == datetime(2023, 11, 9)
    wb2 = openpyxl.Workbook()
    wb2.active['A1'] = 'text only'
    assert ingest._week_anchor(wb2) is None


def test_strip_attendance_normalises_level_2_3():
    assert ingest._strip_attendance('Level 2-3') == 'Level 2'
    assert ingest._strip_attendance('Level 2/3 Attendance') == 'Level 2'
    assert ingest._strip_attendance('Level 2 & 3') == 'Level 2'
    assert ingest._strip_attendance('Levels 1-3 (Nov-Dec 2023)') == 'Levels 1-3 (Nov-Dec 2023)'  # untouched


# ---- folder dispatch ----


def test_ingest_folder_dispatches_and_reports_unhandled(tmp_path, db):
    root = tmp_path / 'outputs'
    (root / 'sub').mkdir(parents=True)
    _roster_ws().parent.save(root / 'sub' / 'May-Jun 2025 Attendance_pseudonymised.xlsx')

    # an unhandled workbook: a flat sheet with neither dancer_id sessions nor a tally
    wb = openpyxl.Workbook()
    wb.active.title = 'Sales'
    wb.active['A1'], wb.active['A2'] = 'total', 5
    wb.save(root / 'misc.xlsx')

    report = ingest.ingest_folder(root, db)
    handled = {(s, p) for _, s, p in report.handled}
    assert ('Level 1 Attendance', 'roster') in handled
    assert any(sheet == 'Sales' for _, sheet in report.unhandled)
    # the roster activities made it into the db via the folder path
    assert db.conn.execute("SELECT COUNT(*) FROM activity WHERE name='Week 1'").fetchone() == (1,)


def test_ingest_folder_skips_readme_silently(tmp_path, db):
    root = tmp_path / 'outputs'
    root.mkdir()
    wb = openpyxl.Workbook()
    wb.active.title = 'README'
    wb.active['A1'] = 'How to use this workbook'
    wb.save(root / 'Jan-Feb 2025 Attendance_pseudonymised.xlsx')

    report = ingest.ingest_folder(root, db)
    assert report.handled == []
    assert all(sheet.lower() != 'readme' for _, sheet in report.unhandled)


def test_ingest_folder_skips_member_and_mail_sheets(tmp_path, db):
    root = tmp_path / 'outputs'
    root.mkdir()
    for title in ['Members', 'Membership', 'Mail List', 'Email']:
        wb = openpyxl.Workbook()
        wb.active.title = title
        wb.active['A1'] = 'data'
        wb.save(root / f'{title}.xlsx')

    report = ingest.ingest_folder(root, db)
    assert report.handled == []
    assert report.unhandled == []  # silently skipped, not in unhandled


def test_ingest_folder_skips_bookkeeping_sheets(tmp_path, db):
    """'Exceptions', 'Loyalty' and 'Tickets' tabs carry no per-session attendance."""
    root = tmp_path / 'outputs'
    root.mkdir()
    for title in ['Exceptions', 'Loyalty', 'Tickets']:
        wb = openpyxl.Workbook()
        wb.active.title = title
        wb.active['A1'] = 'data'
        wb.save(root / f'{title}.xlsx')

    report = ingest.ingest_folder(root, db)
    assert report.handled == []
    assert report.unhandled == []  # silently skipped, not in unhandled


def test_ingest_folder_skips_2022_wholesale(tmp_path, db):
    """Anything under a 2022 folder is skipped before a workbook is even opened."""
    root = tmp_path / 'outputs'
    (root / '2022 Attendance Records').mkdir(parents=True)
    _roster_ws().parent.save(root / '2022 Attendance Records' / 'Level 1 Attendance_pseudonymised.xlsx')

    report = ingest.ingest_folder(root, db)
    assert report.handled == []
    assert report.unhandled == []
    assert db.conn.execute('SELECT COUNT(*) FROM activity').fetchone() == (0,)


# ---- L2 & SO attendance parsing ----


def test_l2_so_matches_l2_so_only():
    assert ingest.L2SOAttendanceParser().matches(_l2_so_ws())
    assert not ingest.L2SOAttendanceParser().matches(_roster_ws())


def test_l2_so_parse_maps_categories_to_lesson_and_social(db):
    """Each night yields a Level 2 lesson and/or a social; activities are created lazily."""
    ingest.L2SOAttendanceParser().parse(_l2_so_ws(), db, term='Jan 2026', year=2026, ingest_id=None)
    ev = db.conn.execute('SELECT name, event_type FROM event').fetchone()
    assert ev == ('Jan 2026: 2026 L2 & SO', 'course')

    # Only the (date, kind) pairs that actually occur are created — no phantom activities,
    # and the '=countif(...)' summary row is never read as a category.
    acts = {(r[0], r[1], r[2]) for r in db.conn.execute('SELECT name, activity_type, difficulty FROM activity')}
    assert acts == {
        ('Level 2 (2026-01-15)', 'lesson', 'Level 2'),  # DNC-1 attended, DNC-2 absent
        ('Social (2026-01-22)', 'social', 'social'),  # DNC-1 social-only
        ('Level 2 (2026-01-22)', 'lesson', 'Level 2'),  # DNC-2 attended
    }


def test_l2_so_parse_records_attendance_by_category(db):
    """Level 2 & Social -> Level 2 attendee; Social-Only -> social; Absent -> Level 2 no-show."""
    ingest.L2SOAttendanceParser().parse(_l2_so_ws(), db, term='Jan 2026', year=2026, ingest_id=None)

    # DNC-1: 'Level 2 & Social' on 01-15 -> Level 2 attended; 'Social-Only' on 01-22 -> social.
    dnc1 = db.conn.execute(
        'SELECT a.name, att.attended FROM attendance att JOIN activity a USING(activity_id) '
        "WHERE att.dancer_id='DNC-1' ORDER BY a.name"
    ).fetchall()
    assert dnc1 == [('Level 2 (2026-01-15)', 1), ('Social (2026-01-22)', 1)]

    # DNC-2: 'Absent' on 01-15 -> Level 2 roster row, did not attend; 'Level 2 & Social' on 01-22.
    dnc2 = db.conn.execute(
        'SELECT a.name, att.attended FROM attendance att JOIN activity a USING(activity_id) '
        "WHERE att.dancer_id='DNC-2' ORDER BY a.name"
    ).fetchall()
    assert dnc2 == [('Level 2 (2026-01-15)', 0), ('Level 2 (2026-01-22)', 1)]


# ---- social register parsing (Tea Dances etc.) ----


def test_social_register_matches_only_undated_present_sheets():
    assert ingest.SocialRegisterParser().matches(_social_register_ws())
    assert not ingest.SocialRegisterParser().matches(_roster_ws())  # has dated session columns
    assert not ingest.SocialRegisterParser().matches(_tally_ws())  # no dancer_id


def test_social_register_parse_one_dated_social_from_title(db):
    """One social event/activity; the date comes from the title since the sheet has none."""
    ingest.SocialRegisterParser().parse(
        _social_register_ws(), db, term='Tea Dance 25 Feb 2024', year=None, ingest_id=None
    )
    assert db.conn.execute('SELECT name, event_type FROM event').fetchone() == ('Tea Dance 25 Feb 2024', 'social')
    assert db.conn.execute('SELECT name, activity_type, difficulty, date FROM activity').fetchone() == (
        'Social',
        'social',
        'social',
        '2024-02-25',
    )


def test_social_register_parse_reads_both_halves_with_ticket_and_absence(db):
    """Both side-by-side halves are read; Concession maps to ticket type; ❎ is a no-show."""
    ingest.SocialRegisterParser().parse(
        _social_register_ws(), db, term='Tea Dance 25 Feb 2024', year=None, ingest_id=None
    )
    rows = dict(db.conn.execute('SELECT dancer_id, attended FROM attendance').fetchall())
    assert rows == {'DNC-1': 1, 'DNC-2': 1, 'DNC-3': 1, 'DNC-5': 0}  # DNC-3 (right half) read; DNC-5 absent
    # Concession Yes -> member_or_concession (not a non-member); No -> non_member.
    tickets = dict(db.conn.execute('SELECT dancer_id, ticket_type FROM attendance').fetchall())
    assert tickets == {
        'DNC-1': 'non_member',
        'DNC-2': 'member_or_concession',
        'DNC-3': 'member_or_concession',
        'DNC-5': 'non_member',
    }


def test_social_register_parse_duplicate_ticket_becomes_anonymous_extra(db):
    """DNC-1's two present tickets collapse to one named attendee + one anonymous head."""
    ingest.SocialRegisterParser().parse(
        _social_register_ws(), db, term='Tea Dance 25 Feb 2024', year=None, ingest_id=None
    )
    assert db.conn.execute("SELECT attended FROM attendance WHERE dancer_id='DNC-1'").fetchall() == [(1,)]
    assert db.conn.execute('SELECT ticket_type, head_count FROM attendance_count').fetchall() == [(None, 1)]
    # view total: 3 named present (DNC-1/2/3) + 1 anonymous extra = 4
    assert db.conn.execute('SELECT named_total, aggregate_total, total FROM activity_attendance').fetchone() == (
        3,
        1,
        4,
    )


def test_social_register_matches_attended_marker_layout():
    """The 'Attended' single-block layout (Sunday Socials) is also a social register."""
    assert ingest.SocialRegisterParser().matches(_attended_register_ws())
    assert not ingest.RosterParser().matches(_attended_register_ws())  # no dated sessions


def test_social_register_parse_attended_layout_date_from_tab_name(db):
    """Date comes from the tab name ('26 March 23'); 'x'/blank are present/absent; no ticket type."""
    ingest.SocialRegisterParser().parse(
        _attended_register_ws(), db, term='Sunday Social Attendees 2023-03-25', year=None, ingest_id=None
    )
    # event name has 'Attendees' stripped; activity dated from the tab name, not the filename's 25th
    assert db.conn.execute('SELECT name, event_type FROM event').fetchone() == ('Sunday Social 2023-03-25', 'social')
    assert db.conn.execute('SELECT date FROM activity').fetchone() == ('2023-03-26',)
    rows = dict(db.conn.execute('SELECT dancer_id, attended FROM attendance').fetchall())
    assert rows == {'DNC-1': 1, 'DNC-2': 1, 'DNC-3': 0}  # DNC-3 booked but absent
    # DNC-2's two 'x' tickets -> one named + one anonymous extra; no concession column -> ticket NULL
    assert db.conn.execute('SELECT ticket_type, head_count FROM attendance_count').fetchall() == [(None, 1)]
    assert {r[0] for r in db.conn.execute('SELECT DISTINCT ticket_type FROM attendance')} == {None}
