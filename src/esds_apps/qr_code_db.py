# Simple SQLite helper for QR code tracking
import os
import sqlite3
from contextlib import contextmanager
from typing import Dict, List, Optional

from esds_apps import config

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'qr_codes_schema.sql')


class QRCodeDB:
    def __init__(self, db_path: Optional[str] = config.QR_DB_PATH):
        self.db_path = db_path
        self._ensure_schema()

    @contextmanager
    def _connect(self):
        """Open a connection that commits on success, rolls back on error, and is always closed.

        ``with sqlite3.connect(...)`` alone only manages the transaction; it leaves the connection open.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_schema(self):
        with self._connect() as conn:
            with open(SCHEMA_PATH, 'r') as f:
                conn.executescript(f.read())

    def add_qr_code(self, code_id: str, target_url: str, description: str):
        """Add a new QR code entry to the database."""
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO qr_codes (code_id, target_url, description) VALUES (?, ?, ?)',
                (code_id, target_url, description),
            )
            conn.commit()

    def increment_scan(self, code_id: str):
        """Increment the scan count and log the scan datetime for a QR code."""
        with self._connect() as conn:
            conn.execute('UPDATE qr_codes SET scan_count = scan_count + 1 WHERE code_id = ?', (code_id,))
            conn.execute('INSERT INTO qr_code_scans (code_id, scanned_at) VALUES (?, CURRENT_TIMESTAMP)', (code_id,))
            conn.commit()

    def get_scan_datetimes(self, code_id: str) -> List[str]:
        """Return a list of scan datetimes (UTC ISO format) for a QR code."""
        with self._connect() as conn:
            cur = conn.execute('SELECT scanned_at FROM qr_code_scans WHERE code_id = ? ORDER BY scanned_at', (code_id,))
            return [row[0] for row in cur.fetchall()]

    def get_qr_code(self, code_id: str) -> Optional[Dict]:
        """Retrieve a QR code entry by its code_id."""
        with self._connect() as conn:
            cur = conn.execute('SELECT * FROM qr_codes WHERE code_id = ?', (code_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_dict(cur, row)
            return None

    def list_qr_codes(self) -> List[Dict]:
        """List all QR codes, most recent first."""
        with self._connect() as conn:
            cur = conn.execute('SELECT * FROM qr_codes ORDER BY created_at DESC')
            return [self._row_to_dict(cur, row) for row in cur.fetchall()]

    def delete_qr_code(self, code_id: str):
        """Delete a QR code entry by its code_id."""
        with self._connect() as conn:
            conn.execute('DELETE FROM qr_codes WHERE code_id = ?', (code_id,))
            conn.execute('DELETE FROM qr_code_scans WHERE code_id = ?', (code_id,))
            conn.commit()

    def _row_to_dict(self, cur, row):
        return {desc[0]: row[idx] for idx, desc in enumerate(cur.description)}
