import pytest

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
