import sqlite3

import pytest

from esds_apps import qr_code_db
from esds_apps.qr_code_db import QRCodeDB


@pytest.fixture
def db(tmp_path):
    return QRCodeDB(db_path=str(tmp_path / 'test.db'))


def test_add_and_get_qr_code(db):
    db.add_qr_code('testid1', 'https://example.com', 'desc')
    qr = db.get_qr_code('testid1')
    assert qr['code_id'] == 'testid1'
    assert qr['target_url'] == 'https://example.com'
    assert qr['description'] == 'desc'
    assert qr['scan_count'] == 0


def test_increment_scan(db):
    db.add_qr_code('testid2', 'https://example.com', 'desc')
    db.increment_scan('testid2')
    assert db.get_qr_code('testid2')['scan_count'] == 1


def test_delete_qr_code(db):
    db.add_qr_code('testid3', 'https://example.com', 'desc')
    db.delete_qr_code('testid3')
    assert db.get_qr_code('testid3') is None


def test_description_length_limit(db):
    db.add_qr_code('testid4', 'https://example.com', 'a' * 1024)
    assert db.get_qr_code('testid4')['description'] == 'a' * 1024
    with pytest.raises(Exception):
        db.add_qr_code('testid5', 'https://example.com', 'b' * 1025)


def test_list_qr_codes(db):
    db.add_qr_code('id1', 'https://a.com', 'desc1')
    db.add_qr_code('id2', 'https://b.com', 'desc2')
    assert {c['code_id'] for c in db.list_qr_codes()} == {'id1', 'id2'}


def test_get_scan_datetimes(db):
    db.add_qr_code('scanid', 'https://example.com', 'desc')
    db.increment_scan('scanid')
    db.increment_scan('scanid')
    assert len(db.get_scan_datetimes('scanid')) == 2


def test_get_nonexistent_qr_code(db):
    assert db.get_qr_code('no-such-id') is None


@pytest.fixture
def opened(monkeypatch):
    """Record every connection QRCodeDB opens so tests can check what state they were left in."""
    connections = []
    real_connect = sqlite3.connect

    def recording_connect(*args, **kwargs):
        connections.append(real_connect(*args, **kwargs))
        return connections[-1]

    monkeypatch.setattr(qr_code_db.sqlite3, 'connect', recording_connect)
    return connections


def _is_closed(conn):
    try:
        conn.execute('SELECT 1')
    except sqlite3.ProgrammingError:
        return True
    return False


def test_every_operation_closes_its_connection(tmp_path, opened):
    """`with sqlite3.connect(...)` only ends the transaction, so a missing close leaks one connection per call."""
    db = QRCodeDB(db_path=str(tmp_path / 'leak.db'))
    db.add_qr_code('leak1', 'https://example.com', 'desc')
    db.increment_scan('leak1')
    db.get_scan_datetimes('leak1')
    db.get_qr_code('leak1')
    db.list_qr_codes()
    db.delete_qr_code('leak1')

    assert len(opened) >= 7
    assert all(_is_closed(conn) for conn in opened)


def test_connection_is_rolled_back_and_closed_when_the_operation_fails(tmp_path, opened):
    db = QRCodeDB(db_path=str(tmp_path / 'fail.db'))
    db.add_qr_code('dup', 'https://example.com', 'first')

    with pytest.raises(sqlite3.IntegrityError):
        db.add_qr_code('dup', 'https://example.com', 'second')  # primary key clash

    assert all(_is_closed(conn) for conn in opened)
    assert db.get_qr_code('dup')['description'] == 'first'
