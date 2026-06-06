from datetime import date, datetime

import pytest

from esds_apps.attendance.attendance_db import (
    ActivityType,
    EventType,
    TicketType,
    _to_iso_date,
    open_db,
)


@pytest.fixture
def db(tmp_path):
    d = open_db(tmp_path / 'attendance.sqlite')
    yield d
    d.close()


# ---- schema / open ----


def test_open_creates_tables_and_view(db):
    names = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    assert {'event', 'activity', 'attendance', 'attendance_count', 'dancer', 'event_teacher', 'ingest_log'} <= names
    assert 'activity_attendance' in names


def test_open_is_idempotent(tmp_path):
    p = tmp_path / 'a.sqlite'
    open_db(p).close()
    open_db(p).close()  # must not raise


# ---- _to_iso_date ----


def test_to_iso_date_accepts_date_datetime_and_string():
    assert _to_iso_date(date(2025, 5, 22)) == '2025-05-22'
    assert _to_iso_date(datetime(2025, 5, 22, 19, 30)) == '2025-05-22'
    assert _to_iso_date('2025-05-22 00:00:00') == '2025-05-22'


# ---- events ----


def test_upsert_event_returns_stable_id_and_event_type_is_sticky(db):
    a = db.upsert_event('May-Jun 2025: Level 1', EventType.COURSE)
    b = db.upsert_event('May-Jun 2025: Level 1', EventType.SOCIAL, venue='The Counting House')
    assert a == b
    row = db.conn.execute('SELECT event_type, venue FROM event WHERE event_id=?', (a,)).fetchone()
    assert row == ('course', 'The Counting House')  # event_type sticky; venue filled in


def test_upsert_event_does_not_clobber_venue_with_none(db):
    eid = db.upsert_event('E', EventType.COURSE, venue='Hall')
    db.upsert_event('E', EventType.COURSE)  # venue omitted
    assert db.conn.execute('SELECT venue FROM event WHERE event_id=?', (eid,)).fetchone()[0] == 'Hall'


def test_set_event_teachers_replaces_and_registers_dancers(db):
    eid = db.upsert_event('E', EventType.COURSE)
    db.set_event_teachers(eid, ['DNC-AAAA1111', 'DNC-BBBB2222'])
    db.set_event_teachers(eid, ['DNC-AAAA1111'])  # replace
    teachers = [r[0] for r in db.conn.execute('SELECT dancer_id FROM event_teacher WHERE event_id=?', (eid,))]
    assert teachers == ['DNC-AAAA1111']
    assert db.conn.execute('SELECT COUNT(*) FROM dancer').fetchone()[0] == 2  # both registered


# ---- activities ----


def test_upsert_activity_stable_id_and_iso_date(db):
    eid = db.upsert_event('E', EventType.COURSE)
    a = db.upsert_activity(eid, 'Week 1', datetime(2025, 5, 22), ActivityType.LESSON)
    b = db.upsert_activity(eid, 'Week 1', '2025-05-22 00:00:00')
    assert a == b
    assert db.conn.execute('SELECT date FROM activity WHERE activity_id=?', (a,)).fetchone()[0] == '2025-05-22'


def test_same_name_different_date_are_distinct_activities(db):
    eid = db.upsert_event('E', EventType.COURSE)
    a = db.upsert_activity(eid, 'Social', date(2025, 5, 1))
    b = db.upsert_activity(eid, 'Social', date(2025, 6, 1))
    assert a != b


def test_upsert_activity_stores_and_keeps_difficulty(db):
    eid = db.upsert_event('E', EventType.COURSE)
    a = db.upsert_activity(eid, 'Week 1', date(2025, 5, 22), ActivityType.LESSON, difficulty='Level 1')
    assert db.conn.execute('SELECT difficulty FROM activity WHERE activity_id=?', (a,)).fetchone()[0] == 'Level 1'
    db.upsert_activity(eid, 'Week 1', date(2025, 5, 22))  # re-ingest with no difficulty must not wipe it
    assert db.conn.execute('SELECT difficulty FROM activity WHERE activity_id=?', (a,)).fetchone()[0] == 'Level 1'


# ---- attendance ----


def test_record_attendance_one_row_per_pair(db):
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_attendance(act, 'DNC-1', attended=False)
    db.record_attendance(act, 'DNC-1', attended=True, ticket_type=TicketType.MEMBER)  # upsert
    rows = db.conn.execute('SELECT attended, ticket_type FROM attendance WHERE activity_id=?', (act,)).fetchall()
    assert rows == [(1, 'member')]


def test_record_attendance_registers_dancer(db):
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_attendance(act, 'DNC-Z', attended=True)
    assert db.conn.execute('SELECT 1 FROM dancer WHERE dancer_id=?', ('DNC-Z',)).fetchone() == (1,)


# ---- counts ----


def test_record_count_replaces_not_accumulates(db):
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_count(act, TicketType.MEMBER, 19)
    db.record_count(act, TicketType.MEMBER, 21)  # re-ingest replaces
    assert db.conn.execute('SELECT head_count FROM attendance_count WHERE activity_id=?', (act,)).fetchone() == (21,)


def test_record_count_null_ticket_type_upserts(db):
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_count(act, None, 2)
    db.record_count(act, None, 3)  # must update the same NULL row, not add a second
    rows = db.conn.execute(
        'SELECT ticket_type, head_count FROM attendance_count WHERE activity_id=?', (act,)
    ).fetchall()
    assert rows == [(None, 3)]


def test_record_count_distinct_ticket_types_coexist(db):
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_count(act, TicketType.MEMBER, 19)
    db.record_count(act, TicketType.CONCESSION, 2)
    db.record_count(act, None, 1)
    assert db.conn.execute('SELECT COUNT(*) FROM attendance_count WHERE activity_id=?', (act,)).fetchone() == (3,)


# ---- the view ----


def test_view_combines_named_and_aggregate(db):
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_attendance(act, 'DNC-1', attended=True)
    db.record_attendance(act, 'DNC-2', attended=True)
    db.record_attendance(act, 'DNC-3', attended=False)  # not counted
    db.record_count(act, TicketType.MEMBER, 5)
    row = db.conn.execute(
        'SELECT named_total, aggregate_total, total FROM activity_attendance WHERE activity_id=?', (act,)
    ).fetchone()
    assert row == (2, 5, 7)
