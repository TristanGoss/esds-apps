import sqlite3
from datetime import date, datetime

import pytest

from esds_apps.attendance.attendance_db import (
    ActivityType,
    AttendanceStatus,
    EventType,
    TicketType,
    _to_iso_date,
    open_db,
)


@pytest.fixture
def db(tmp_path):
    # Enforcement off: these tests register dancers without seeding the pseudonyms store. The
    # dancer -> pseudonyms link is covered explicitly in
    # test_dancer_foreign_key_into_pseudonyms_is_enforced.
    d = open_db(tmp_path / 'attendance.sqlite', enforce_foreign_keys=False)
    yield d
    d.close()


# ---- schema / open ----


def test_open_creates_tables_and_view(db):
    names = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    assert {
        'event',
        'activity',
        'attendance',
        'attendance_count',
        'dancer',
        'event_teacher',
        'ingest_log',
        'waitlist',
        'meta',
    } <= names
    assert 'activity_attendance' in names


def test_open_is_idempotent(tmp_path):
    p = tmp_path / 'a.sqlite'
    open_db(p).close()
    open_db(p).close()  # must not raise


def test_attendance_requires_existing_dancer_when_foreign_keys_enforced(tmp_path):
    """With enforcement on (the production default), attendance needs the dancer to exist first."""
    d = open_db(tmp_path / 'fk.sqlite')  # enforce_foreign_keys=True
    eid = d.upsert_event('E', EventType.SOCIAL)
    act = d.upsert_activity(eid, 'Social', date(2026, 1, 1), ActivityType.SOCIAL)
    with pytest.raises(sqlite3.IntegrityError):
        d.record_attendance(act, 'DNC-NOPE', status=AttendanceStatus.ATTENDED)  # no dancer row -> FK violation
    # Mint the dancer (as pseudonymisation would), then the same attendance records cleanly.
    d.conn.execute("INSERT INTO dancer (dancer_id) VALUES ('DNC-OK')")
    d.record_attendance(act, 'DNC-OK', status=AttendanceStatus.ATTENDED)
    assert d.conn.execute("SELECT COUNT(*) FROM attendance WHERE dancer_id = 'DNC-OK'").fetchone()[0] == 1
    d.close()


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


def test_event_id_by_name_finds_existing_and_none_for_missing(db):
    eid = db.upsert_event('Level 1 Term B (Mar-Apr 2023)', EventType.COURSE)
    assert db.event_id_by_name('Level 1 Term B (Mar-Apr 2023)') == eid
    assert db.event_id_by_name('No Such Event') is None  # read-only: a miss returns None, inserts nothing
    assert db.conn.execute('SELECT COUNT(*) FROM event').fetchone() == (1,)


def test_set_event_teachers_replaces(db):
    eid = db.upsert_event('E', EventType.COURSE)
    db.set_event_teachers(eid, ['DNC-AAAA1111', 'DNC-BBBB2222'])
    db.set_event_teachers(eid, ['DNC-AAAA1111'])  # replace
    teachers = [r[0] for r in db.conn.execute('SELECT dancer_id FROM event_teacher WHERE event_id=?', (eid,))]
    assert teachers == ['DNC-AAAA1111']


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
    db.record_attendance(act, 'DNC-1', status=AttendanceStatus.ABSENT)
    db.record_attendance(act, 'DNC-1', status=AttendanceStatus.ATTENDED, ticket_type=TicketType.MEMBER)  # upsert
    rows = db.conn.execute('SELECT status, ticket_type FROM attendance WHERE activity_id=?', (act,)).fetchall()
    assert rows == [('attended', 'member')]


def test_record_attendance_stores_unknown_status(db):
    # A bought-ticket-but-turnout-unknown row: kept as evidence of interest, never a confirmed head.
    eid = db.upsert_event('E', EventType.SOCIAL)
    act = db.upsert_activity(eid, 'Social', date(2023, 12, 14))
    db.record_attendance(act, 'DNC-7', status=AttendanceStatus.UNKNOWN)
    assert db.conn.execute('SELECT status FROM attendance WHERE activity_id=?', (act,)).fetchone() == ('unknown',)


@pytest.mark.parametrize(
    ('first', 'second', 'expected'),
    [
        # A more informative reading upgrades a less informative one (either order).
        (AttendanceStatus.UNKNOWN, AttendanceStatus.ATTENDED, 'attended'),
        (AttendanceStatus.UNKNOWN, AttendanceStatus.ABSENT, 'absent'),
        (AttendanceStatus.ABSENT, AttendanceStatus.ATTENDED, 'attended'),
        # ...and a less informative one never demotes it: attended > absent > unknown is a total
        # order, so the merge is independent of ingest order.
        (AttendanceStatus.ATTENDED, AttendanceStatus.UNKNOWN, 'attended'),
        (AttendanceStatus.ABSENT, AttendanceStatus.UNKNOWN, 'absent'),
        (AttendanceStatus.ATTENDED, AttendanceStatus.ABSENT, 'attended'),
    ],
)
def test_record_attendance_keeps_most_informative_status(db, first, second, expected):
    # The Term B double-ingest case: a roster knows turnout, a booking export only knows a
    # ticket was bought; whichever order they arrive, the stronger fact must survive.
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2022, 6, 16))
    db.record_attendance(act, 'DNC-1', status=first, source_cell='first')
    db.record_attendance(act, 'DNC-1', status=second, source_cell='second')
    row = db.conn.execute('SELECT status, source_cell FROM attendance WHERE activity_id=?', (act,)).fetchone()
    assert row[0] == expected
    # Provenance follows the kept status: the row that lost an arbitration doesn't claim it.
    assert row[1] == ('second' if str(second) == expected else 'first')


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


# ---- waitlist ----


def test_record_waitlist_named_is_idempotent(db):
    eid = db.upsert_event('E', EventType.COURSE)
    db.record_waitlist(eid, 'DNC-1')
    db.record_waitlist(eid, 'DNC-1', source_cell='Sheet!A9')  # re-ingest updates, not duplicates
    rows = db.conn.execute(
        'SELECT dancer_id, head_count, source_cell FROM waitlist WHERE event_id=?', (eid,)
    ).fetchall()
    assert rows == [('DNC-1', 1, 'Sheet!A9')]


def test_record_waitlist_count_uses_single_null_row(db):
    eid = db.upsert_event('E', EventType.COURSE)
    db.record_waitlist(eid, head_count=12)
    db.record_waitlist(eid, head_count=8)  # re-ingest replaces the same NULL row
    rows = db.conn.execute('SELECT dancer_id, head_count FROM waitlist WHERE event_id=?', (eid,)).fetchall()
    assert rows == [(None, 8)]


def test_record_waitlist_named_and_count_coexist_and_sum(db):
    eid = db.upsert_event('E', EventType.COURSE)
    db.record_waitlist(eid, 'DNC-1')
    db.record_waitlist(eid, 'DNC-2')
    db.record_waitlist(eid, head_count=5)  # plus an anonymous count
    total = db.conn.execute('SELECT SUM(head_count) FROM waitlist WHERE event_id=?', (eid,)).fetchone()[0]
    assert total == 7  # noqa: PLR2004


def test_record_waitlist_stays_out_of_attendance_totals(db):
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_attendance(act, 'DNC-1', status=AttendanceStatus.ATTENDED)
    db.record_waitlist(eid, 'DNC-2')  # waitlisted, not an attendee
    row = db.conn.execute(
        'SELECT named_total, aggregate_total, total FROM activity_attendance WHERE activity_id=?', (act,)
    ).fetchone()
    assert row == (1, 0, 1)  # the waitlister does not appear


# ---- the view ----


def test_view_combines_named_and_aggregate(db):
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_attendance(act, 'DNC-1', status=AttendanceStatus.ATTENDED)
    db.record_attendance(act, 'DNC-2', status=AttendanceStatus.ATTENDED)
    db.record_attendance(act, 'DNC-3', status=AttendanceStatus.ABSENT)  # not counted
    db.record_count(act, TicketType.MEMBER, 5)
    row = db.conn.execute(
        'SELECT named_total, aggregate_total, total FROM activity_attendance WHERE activity_id=?', (act,)
    ).fetchone()
    assert row == (2, 5, 7)


def test_view_counts_unknown_separately_from_attendance(db):
    # UNKNOWN rows show interest but are kept out of the attendance totals and surfaced on their own.
    eid = db.upsert_event('E', EventType.SOCIAL)
    act = db.upsert_activity(eid, 'Social', date(2023, 12, 14))
    db.record_attendance(act, 'DNC-1', status=AttendanceStatus.ATTENDED)
    db.record_attendance(act, 'DNC-2', status=AttendanceStatus.UNKNOWN)
    db.record_attendance(act, 'DNC-3', status=AttendanceStatus.UNKNOWN)
    row = db.conn.execute(
        'SELECT named_total, total, named_unknown FROM activity_attendance WHERE activity_id=?', (act,)
    ).fetchone()
    assert row == (1, 1, 2)  # the two unknowns don't inflate attendance, but are counted as interest


def test_view_named_registered_counts_every_named_row(db):
    # 'Registered' is everyone named on the sheet, whatever their turnout: a weekly roster
    # records the present as attended and the no-shows as absent, so registered >= attended.
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2025, 5, 22))
    db.record_attendance(act, 'DNC-1', status=AttendanceStatus.ATTENDED)
    db.record_attendance(act, 'DNC-2', status=AttendanceStatus.ABSENT)
    db.record_attendance(act, 'DNC-3', status=AttendanceStatus.UNKNOWN)
    row = db.conn.execute(
        'SELECT named_total, named_unknown, named_registered, total FROM activity_attendance WHERE activity_id=?',
        (act,),
    ).fetchone()
    assert row == (1, 1, 3, 1)  # registered = attended+absent+unknown; total stays attended-only


# ---- reassign_dancer (de-duplication merge) ----


def _seed_two_dancers(db):
    db.conn.execute("INSERT INTO dancer (dancer_id) VALUES ('DNC-OLD'), ('DNC-NEW')")


def test_reassign_moves_attendance_when_no_clash(db):
    from esds_apps.attendance.attendance_db import reassign_dancer

    _seed_two_dancers(db)
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2026, 1, 6))
    db.record_attendance(act, 'DNC-OLD', status=AttendanceStatus.ATTENDED)
    moved = reassign_dancer(db.conn, 'DNC-OLD', 'DNC-NEW')
    db.conn.commit()
    assert moved['attendance'] == 1
    holders = [r[0] for r in db.conn.execute('SELECT dancer_id FROM attendance WHERE activity_id=?', (act,))]
    assert holders == ['DNC-NEW']


def test_reassign_collapses_attendance_keeping_more_informative_status(db):
    """Both ids attended the same activity: one row survives, 'attended' beating 'unknown'."""
    from esds_apps.attendance.attendance_db import reassign_dancer

    _seed_two_dancers(db)
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2026, 1, 6))
    db.record_attendance(act, 'DNC-OLD', status=AttendanceStatus.ATTENDED)
    db.record_attendance(act, 'DNC-NEW', status=AttendanceStatus.UNKNOWN)
    reassign_dancer(db.conn, 'DNC-OLD', 'DNC-NEW')
    db.conn.commit()
    rows = db.conn.execute('SELECT dancer_id, status FROM attendance WHERE activity_id=?', (act,)).fetchall()
    assert rows == [('DNC-NEW', str(AttendanceStatus.ATTENDED))]


def test_reassign_does_not_downgrade_survivor_attended(db):
    """A surviving 'attended' is kept even if old_id's row is only 'unknown'."""
    from esds_apps.attendance.attendance_db import reassign_dancer

    _seed_two_dancers(db)
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2026, 1, 6))
    db.record_attendance(act, 'DNC-OLD', status=AttendanceStatus.UNKNOWN)
    db.record_attendance(act, 'DNC-NEW', status=AttendanceStatus.ATTENDED)
    reassign_dancer(db.conn, 'DNC-OLD', 'DNC-NEW')
    db.conn.commit()
    rows = db.conn.execute('SELECT dancer_id, status FROM attendance WHERE activity_id=?', (act,)).fetchall()
    assert rows == [('DNC-NEW', str(AttendanceStatus.ATTENDED))]


def test_reassign_sums_waitlist_head_counts_on_clash(db):
    from esds_apps.attendance.attendance_db import reassign_dancer

    _seed_two_dancers(db)
    eid = db.upsert_event('E', EventType.COURSE)
    db.record_waitlist(eid, 'DNC-OLD', head_count=1)
    db.record_waitlist(eid, 'DNC-NEW', head_count=1)
    reassign_dancer(db.conn, 'DNC-OLD', 'DNC-NEW')
    db.conn.commit()
    rows = db.conn.execute('SELECT dancer_id, head_count FROM waitlist WHERE event_id=?', (eid,)).fetchall()
    assert rows == [('DNC-NEW', 2)]


def test_reassign_collapses_duplicate_event_teacher(db):
    from esds_apps.attendance.attendance_db import reassign_dancer

    _seed_two_dancers(db)
    eid = db.upsert_event('E', EventType.COURSE)
    db.set_event_teachers(eid, ['DNC-OLD', 'DNC-NEW'])  # both teach it; after merge only one remains
    reassign_dancer(db.conn, 'DNC-OLD', 'DNC-NEW')
    db.conn.commit()
    teachers = [r[0] for r in db.conn.execute('SELECT dancer_id FROM event_teacher WHERE event_id=?', (eid,))]
    assert teachers == ['DNC-NEW']


def test_reassign_leaves_anonymous_waitlist_untouched(db):
    from esds_apps.attendance.attendance_db import reassign_dancer

    _seed_two_dancers(db)
    eid = db.upsert_event('E', EventType.COURSE)
    db.record_waitlist(eid, dancer_id=None, head_count=5)  # anonymous count
    moved = reassign_dancer(db.conn, 'DNC-OLD', 'DNC-NEW')
    db.conn.commit()
    assert moved['waitlist'] == 0
    row = db.conn.execute('SELECT dancer_id, head_count FROM waitlist WHERE event_id=?', (eid,)).fetchone()
    assert row == (None, 5)
