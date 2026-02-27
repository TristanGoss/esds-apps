import os
import tempfile

import pytest

from esds_apps.qr_code_db import QRCodeDB


def test_add_and_get_qr_code():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test_qr_codes.db')
        schema_path = os.path.join(os.path.dirname(__file__), '../../working/qr_codes_schema.sql')
        db = QRCodeDB(db_path=db_path)
        db.SCHEMA_PATH = schema_path  # ensure schema path is correct
        code_id = 'testid1'
        db.add_qr_code(code_id, 'https://example.com', 'desc')
        qr = db.get_qr_code(code_id)
        assert qr['code_id'] == code_id
        assert qr['target_url'] == 'https://example.com'
        assert qr['description'] == 'desc'
        assert qr['scan_count'] == 0


def test_increment_scan():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test_qr_codes.db')
        db = QRCodeDB(db_path=db_path)
        code_id = 'testid2'
        db.add_qr_code(code_id, 'https://example.com', 'desc')
        db.increment_scan(code_id)
        qr = db.get_qr_code(code_id)
        assert qr['scan_count'] == 1


def test_delete_qr_code():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test_qr_codes.db')
        db = QRCodeDB(db_path=db_path)
        code_id = 'testid3'
        db.add_qr_code(code_id, 'https://example.com', 'desc')
        db.delete_qr_code(code_id)
        assert db.get_qr_code(code_id) is None


def test_description_length_limit():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test_qr_codes.db')
        db = QRCodeDB(db_path=db_path)
        code_id = 'testid4'
        long_desc = 'a' * 1024
        db.add_qr_code(code_id, 'https://example.com', long_desc)
        qr = db.get_qr_code(code_id)
        assert qr['description'] == long_desc
        # Should fail if too long
        with pytest.raises(Exception):
            db.add_qr_code('testid5', 'https://example.com', 'b' * 1025)


def test_list_qr_codes():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test_qr_codes.db')
        db = QRCodeDB(db_path=db_path)
        # Add multiple codes
        db.add_qr_code('id1', 'https://a.com', 'desc1')
        db.add_qr_code('id2', 'https://b.com', 'desc2')
        codes = db.list_qr_codes()
        # Should return both codes, most recent first
        code_ids = [c['code_id'] for c in codes]
        assert set(code_ids) == {'id1', 'id2'}
        assert codes[0]['code_id'] in {'id1', 'id2'}
