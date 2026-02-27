# Simple SQLite helper for QR code tracking
import os
import sqlite3
from typing import Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), '../../working/qr_codes.db')
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), '../../working/qr_codes_schema.sql')


class QRCodeDB:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_schema()

    def _ensure_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            with open(SCHEMA_PATH, 'r') as f:
                conn.executescript(f.read())

    def add_qr_code(self, code_id: str, target_url: str, description: str):
        """Add a new QR code entry to the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT INTO qr_codes (code_id, target_url, description) VALUES (?, ?, ?)',
                (code_id, target_url, description),
            )
            conn.commit()

    def increment_scan(self, code_id: str):
        """Increment the scan count for a QR code."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE qr_codes SET scan_count = scan_count + 1 WHERE code_id = ?', (code_id,))
            conn.commit()

    def get_qr_code(self, code_id: str) -> Optional[Dict]:
        """Retrieve a QR code entry by its code_id."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT * FROM qr_codes WHERE code_id = ?', (code_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_dict(cur, row)
            return None

    def list_qr_codes(self) -> List[Dict]:
        """List all QR codes, most recent first."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('SELECT * FROM qr_codes ORDER BY created_at DESC')
            return [self._row_to_dict(cur, row) for row in cur.fetchall()]

    def delete_qr_code(self, code_id: str):
        """Delete a QR code entry by its code_id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM qr_codes WHERE code_id = ?', (code_id,))
            conn.commit()

    def _row_to_dict(self, cur, row):
        return {desc[0]: row[idx] for idx, desc in enumerate(cur.description)}
