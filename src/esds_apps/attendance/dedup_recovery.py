"""Repair attendance rows orphaned by de-duplications that pre-dated fact-repointing merges.

Historically a merge only rewrote the ``dancer`` identity table and the spreadsheets; it did not
touch the ingested ``attendance`` / ``waitlist`` / ``event_teacher`` rows. So a merge run on an
already-ingested database deleted the discarded dancer and left its fact rows dangling on an id no
longer present in the ``dancer`` table.

Restoration copies only ciphertext keyed by ``dancer_id``, so it needs no passphrase. But the
identifying columns (``enc_name`` and the ``*_hash`` lookup keys) are derived with the database's
per-database salt, so the backup **must share the live database's salt** or the restored rows would
neither decrypt under the live key nor match future pseudonymisation. The restore refuses a salt
mismatch, and skips any id whose name/email hash already belongs to a different live dancer (a
UNIQUE clash left by a merge that promoted the discarded value to primary — that one needs a look).
"""

import sqlite3
from pathlib import Path

# Fact tables whose dancer_id can dangle after a pre-repoint merge.
_REF_TABLES = ('attendance', 'waitlist', 'event_teacher')


def _salt(conn: sqlite3.Connection) -> str | None:
    row = conn.execute('SELECT value FROM meta WHERE key="salt"').fetchone()
    return row[0] if row else None


def orphaned_dancer_ids(conn: sqlite3.Connection) -> set[str]:
    """Every dancer_id referenced by a fact row but absent from the dancer table."""
    orphans: set[str] = set()
    for table in _REF_TABLES:
        orphans.update(
            r[0]
            for r in conn.execute(
                f'SELECT DISTINCT dancer_id FROM {table} '
                'WHERE dancer_id IS NOT NULL AND dancer_id NOT IN (SELECT dancer_id FROM dancer)'
            )
        )
    return orphans


def restore_orphaned_dancers(conn: sqlite3.Connection, backup_path: str | Path, dry_run: bool = True) -> dict:
    """Restore the live DB's orphaned dancer rows from ``backup_path``. Dry-run by default.

    Repairs referential integrity so the de-duplication can be repeated cleanly. Refuses if the
    backup's salt differs from the live database's (restored ciphertext would be undecryptable).
    Skips, and reports, any orphan missing from the backup, or whose name/email hash already belongs
    to a different live dancer (a UNIQUE clash needing manual attention).

    Returns ``{'restored': [...], 'missing_from_backup': [...], 'hash_clash': [...]}``. With
    ``dry_run=True`` (default) nothing is written — inspect the plan, then re-run with
    ``dry_run=False`` to apply and commit.
    """
    backup = sqlite3.connect(f'file:{Path(backup_path).as_posix()}?mode=ro', uri=True)
    try:
        if _salt(backup) != _salt(conn):
            raise ValueError(
                'Backup salt does not match the live database — restored rows would not decrypt. '
                'Choose a backup that shares the live salt.'
            )
        restored, missing, clash = [], [], []
        for oid in sorted(orphaned_dancer_ids(conn)):
            row = backup.execute(
                'SELECT dancer_id, enc_name, enc_email, name_hash, email_hash FROM dancer WHERE dancer_id=?',
                (oid,),
            ).fetchone()
            if row is None:
                missing.append(oid)
                continue
            _id, _enc_name, _enc_email, name_hash, email_hash = row
            if any(
                value is not None
                and conn.execute(f'SELECT 1 FROM dancer WHERE {col}=? AND dancer_id!=?', (value, oid)).fetchone()
                for col, value in (('name_hash', name_hash), ('email_hash', email_hash))
            ):
                clash.append(oid)
                continue
            if not dry_run:
                conn.execute(
                    'INSERT INTO dancer (dancer_id, enc_name, enc_email, name_hash, email_hash) VALUES (?, ?, ?, ?, ?)',
                    row,
                )
            restored.append(oid)
        if not dry_run:
            conn.commit()
        return {'restored': restored, 'missing_from_backup': missing, 'hash_clash': clash}
    finally:
        backup.close()
