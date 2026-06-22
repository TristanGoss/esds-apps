import hashlib

import pytest

from esds_apps.attendance import pseudonyms_db
from esds_apps.attendance.attendance_db import open_db

PASSPHRASE = 'test-passphrase-esds'


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """Reduce PBKDF2 from 480,000 iterations to 1 for test speed."""
    _orig = hashlib.pbkdf2_hmac
    monkeypatch.setattr(
        hashlib,
        'pbkdf2_hmac',
        lambda name, pw, salt, iters, **kw: _orig(name, pw, salt, 1, **kw),
    )


@pytest.fixture
def db(tmp_path):
    """A fresh attendance database, shared by the parser and dispatcher tests.

    Foreign-key enforcement is off: these tests build attendance records directly without seeding
    the pseudonyms store, so the dancer -> pseudonyms link doesn't have to be satisfied here. The
    link's enforcement is covered explicitly in test_attendance_db.
    """
    d = open_db(tmp_path / 'attendance.sqlite', enforce_foreign_keys=False)
    yield d
    d.close()


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / 'test.sqlite'


@pytest.fixture
def ctx(tmp_db):
    """Open a fresh pseudonyms DB and yield a DbContext, closing on teardown.

    Shared by the store tests (test_pseudonyms_db) and the matching tests (test_dancer_matching).
    """
    c = pseudonyms_db.open_db(tmp_db, PASSPHRASE)
    yield c
    c.conn.close()
