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


# ---- value helpers ----


@pytest.mark.parametrize('value', [True, 'True', 'TRUE', 'true', 'x', 'yes', '☑️', '✅'])
def test_is_true_positives(value):
    assert ingest._is_true(value)


@pytest.mark.parametrize('value', [False, 'False', 'Refunded', '❎', '❌', '', None, 0])
def test_is_true_negatives(value):
    assert not ingest._is_true(value)


def test_parse_dt_handles_datetime_and_isoish_string():
    assert ingest._parse_dt(datetime(2025, 5, 22)).year == 2025
    assert ingest._parse_dt('2025-05-22 00:00:00').month == 5
    assert ingest._parse_dt('not a date') is None


def test_term_from_strips_attendance_and_suffix():
    assert ingest._term_from(Path('May-Jun 2025 Attendance_pseudonymised.xlsx')) == 'May-Jun 2025'
    assert ingest._term_from(Path('Attendance Nov-Dec 2024_pseudonymised.xlsx')) == 'Nov-Dec 2024'


# ---- matchers ----


def test_roster_matches_roster_only():
    assert ingest.RosterParser().matches(_roster_ws())
    assert not ingest.RosterParser().matches(_tally_ws())


def test_tally_matches_tally_only():
    assert ingest.Level2TallyParser().matches(_tally_ws())
    assert not ingest.Level2TallyParser().matches(_roster_ws())


# ---- roster parsing ----


def test_roster_parse_creates_event_and_activities(db):
    ingest.RosterParser().parse(_roster_ws(), db, term='May-Jun 2025', year=2025, ingest_id=None)
    ev = db.conn.execute('SELECT name, event_type FROM event').fetchone()
    assert ev == ('May-Jun 2025: Level 1 Attendance', 'course')
    acts = db.conn.execute('SELECT name, date FROM activity ORDER BY date').fetchall()
    assert acts == [('Week 1', '2025-05-22'), ('Week 2', '2025-05-29')]


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
