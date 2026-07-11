from datetime import date

import pytest

from esds_apps.attendance import dedup_recovery
from esds_apps.attendance.attendance_db import ActivityType, AttendanceStatus, EventType, open_db


def _set_salt(db, value):
    db.conn.execute('INSERT OR REPLACE INTO meta (key, value) VALUES ("salt", ?)', (value,))
    db.conn.commit()


def _add_dancer(db, dancer_id, name_hash=None, email_hash=None):
    db.conn.execute(
        'INSERT INTO dancer (dancer_id, enc_name, enc_email, name_hash, email_hash) VALUES (?, ?, ?, ?, ?)',
        (dancer_id, f'enc-{dancer_id}', None, name_hash, email_hash),
    )
    db.conn.commit()


def _orphan_attendance(db, dancer_id):
    """Attendance for a dancer that is then deleted, leaving the row orphaned (FK off)."""
    eid = db.upsert_event('E', EventType.COURSE)
    act = db.upsert_activity(eid, 'W1', date(2026, 1, 6), ActivityType.LESSON, 'Level 1')
    _add_dancer(db, dancer_id)
    db.record_attendance(act, dancer_id, status=AttendanceStatus.ATTENDED)
    db.conn.execute('DELETE FROM dancer WHERE dancer_id=?', (dancer_id,))
    db.conn.commit()
    return act


@pytest.fixture
def live(tmp_path):
    db = open_db(tmp_path / 'live.sqlite', enforce_foreign_keys=False)
    _set_salt(db, 'SALT-A')
    yield db
    db.close()


@pytest.fixture
def backup(tmp_path):
    db = open_db(tmp_path / 'backup.sqlite', enforce_foreign_keys=False)
    _set_salt(db, 'SALT-A')
    yield db
    db.close()


def test_orphaned_ids_detected(live):
    _orphan_attendance(live, 'DNC-GONE')
    assert dedup_recovery.orphaned_dancer_ids(live.conn) == {'DNC-GONE'}


def test_restore_repairs_orphan(live, backup, tmp_path):
    _orphan_attendance(live, 'DNC-GONE')
    _add_dancer(backup, 'DNC-GONE', name_hash='nh-gone')  # backup still holds the deleted dancer
    report = dedup_recovery.restore_orphaned_dancers(live.conn, tmp_path / 'backup.sqlite', dry_run=False)
    assert report['restored'] == ['DNC-GONE']
    assert dedup_recovery.orphaned_dancer_ids(live.conn) == set()  # fully repaired
    assert (
        live.conn.execute('SELECT enc_name FROM dancer WHERE dancer_id=?', ('DNC-GONE',)).fetchone()[0]
        == 'enc-DNC-GONE'
    )


def test_dry_run_writes_nothing(live, backup, tmp_path):
    _orphan_attendance(live, 'DNC-GONE')
    _add_dancer(backup, 'DNC-GONE')
    report = dedup_recovery.restore_orphaned_dancers(live.conn, tmp_path / 'backup.sqlite', dry_run=True)
    assert report['restored'] == ['DNC-GONE']  # planned
    assert dedup_recovery.orphaned_dancer_ids(live.conn) == {'DNC-GONE'}  # but not applied


def test_salt_mismatch_refused(live, backup, tmp_path):
    _set_salt(backup, 'SALT-B')  # different lineage: restored ciphertext would not decrypt
    _orphan_attendance(live, 'DNC-GONE')
    _add_dancer(backup, 'DNC-GONE')
    with pytest.raises(ValueError, match='salt'):
        dedup_recovery.restore_orphaned_dancers(live.conn, tmp_path / 'backup.sqlite', dry_run=True)


def test_missing_from_backup_reported(live, backup, tmp_path):
    _orphan_attendance(live, 'DNC-GONE')  # backup does NOT have it
    report = dedup_recovery.restore_orphaned_dancers(live.conn, tmp_path / 'backup.sqlite', dry_run=False)
    assert report['missing_from_backup'] == ['DNC-GONE']
    assert report['restored'] == []


def test_hash_clash_reported_not_restored(live, backup, tmp_path):
    _orphan_attendance(live, 'DNC-GONE')
    _add_dancer(live, 'DNC-SURVIVOR', name_hash='shared-nh')  # survivor already holds the hash
    _add_dancer(backup, 'DNC-GONE', name_hash='shared-nh')
    report = dedup_recovery.restore_orphaned_dancers(live.conn, tmp_path / 'backup.sqlite', dry_run=False)
    assert report['hash_clash'] == ['DNC-GONE']
    assert report['restored'] == []
    assert dedup_recovery.orphaned_dancer_ids(live.conn) == {'DNC-GONE'}  # left for manual attention
