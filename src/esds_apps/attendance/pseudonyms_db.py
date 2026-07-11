"""The encrypted dancer-ID store.

Holds the mapping from a person (name / email) to a stable pseudonymous ``DNC-XXXXXXXX``
dancer ID, with the identifying fields encrypted at rest (Fernet) and looked up by keyed
hash (HMAC). This is the security-sensitive core: the spreadsheet pipeline (``pseudonymise``)
writes through ``get_or_create_dancer_id`` and the analysis tools (``dancer_matching``) read
back through ``decrypt_all``.

Key derivation: one PBKDF2-SHA256 call off the passphrase + per-database salt yields the
Fernet and HMAC keys, while the *dancer-ID* key is derived from the passphrase alone (no salt)
so the same passphrase reproduces the same IDs across freshly built databases.
"""

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from cryptography.fernet import Fernet, InvalidToken

_SENTINEL = b'esds-pseudonymise-v1'

# Canonical join order for the name hash key, so the same person matches across sheets
# regardless of how a given sheet ordered its first/last name columns.
_CANONICAL_NAME_ORDER = ['first_name', 'last_name']


# ---------------------------------------------------------------------------
# Key derivation and database
# ---------------------------------------------------------------------------


def _derive_keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    """One PBKDF2 call → 64 bytes → split into Fernet key and HMAC key."""
    raw = hashlib.pbkdf2_hmac('sha256', passphrase.encode(), salt, 480_000, dklen=64)
    return base64.urlsafe_b64encode(raw[:32]), raw[32:]


def derive_id_key(passphrase: str) -> bytes:
    """Derive a stable key for dancer ID generation from the passphrase alone (no DB salt).

    Because this key does not depend on the per-database random salt, dancer IDs derived
    from it are reproducible: the same passphrase and input always produce the same ID,
    even across freshly created databases. This makes the database reconstructable from the
    original input files given only the passphrase.
    """
    return hashlib.pbkdf2_hmac('sha256', passphrase.encode(), b'esds-dancer-id-v1', 100_000, dklen=32)


def _derive_dancer_id(id_key: bytes, primary_key: str) -> str:
    digest = hmac.new(id_key, primary_key.lower().strip().encode(), 'sha256').hexdigest()
    return f'DNC-{digest[:8].upper()}'


@dataclass
class DbContext:
    """Holds all state needed to read and write the pseudonymisation database."""

    conn: sqlite3.Connection
    fernet: Fernet
    mac_key: bytes
    id_key: bytes


def _setup_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    # Enforce foreign keys on this connection (SQLite defaults them OFF, per-connection). This is
    # the connection the dedup merge deletes dancers on: with the facts repointed first (see
    # attendance_db.reassign_dancer) the delete is clean, and any future path that tries to remove a
    # still-referenced dancer now fails loudly instead of silently orphaning attendance rows.
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute(
        'CREATE TABLE IF NOT EXISTS dancer ('
        '  dancer_id   TEXT PRIMARY KEY,'
        '  enc_name    TEXT,'
        '  enc_email   TEXT,'
        '  name_hash   TEXT UNIQUE,'
        '  email_hash  TEXT UNIQUE)'
    )
    conn.commit()
    return conn


def open_db(db_path: Path, passphrase: str) -> DbContext:
    """Open (or create) the database. Returns a DbContext.

    Raises ValueError with a descriptive message if the passphrase is wrong.
    """
    conn = _setup_db(db_path)
    row = conn.execute('SELECT value FROM meta WHERE key="salt"').fetchone()
    if row:
        salt = bytes.fromhex(row[0])
    else:
        salt = secrets.token_bytes(32)
        conn.execute('INSERT INTO meta VALUES ("salt", ?)', (salt.hex(),))
        conn.commit()

    fernet_key, mac_key = _derive_keys(passphrase, salt)
    fernet = Fernet(fernet_key)

    # On first open: store a sentinel so subsequent opens can validate the passphrase.
    # On later opens: validate immediately rather than letting InvalidToken surface elsewhere.
    sentinel_row = conn.execute('SELECT value FROM meta WHERE key="sentinel"').fetchone()
    if sentinel_row:
        try:
            fernet.decrypt(sentinel_row[0].encode())
        except InvalidToken:
            conn.close()
            raise ValueError(
                f'Wrong passphrase for database {db_path}. Open failed before any data was read or written.'
            )
    else:
        conn.execute(
            'INSERT INTO meta VALUES ("sentinel", ?)',
            (fernet.encrypt(_SENTINEL).decode(),),
        )
        conn.commit()

    return DbContext(conn=conn, fernet=fernet, mac_key=mac_key, id_key=derive_id_key(passphrase))


# ---------------------------------------------------------------------------
# Encryption / hashing primitives
# ---------------------------------------------------------------------------


def _value_hash(value: str, mac_key: bytes) -> str:
    return hmac.new(mac_key, value.lower().strip().encode(), 'sha256').hexdigest()


def _encrypt(fernet: Fernet, fields: dict) -> str:
    return fernet.encrypt(json.dumps(fields).encode()).decode()


def _decrypt_fields(fernet: Fernet, blob: str | None) -> dict | None:
    return json.loads(fernet.decrypt(blob.encode()).decode()) if blob else None


def _with_alts(fernet: Fernet, blob: str | None, additions: dict[str, str | None]) -> str | None:
    """Return blob re-encrypted with the given alt fields added, or None if nothing changed.

    Only fields with a truthy value that are not already present are added — alt fields
    are written once and never overwritten.
    """
    existing = _decrypt_fields(fernet, blob) or {}
    to_add = {k: v for k, v in additions.items() if v and not existing.get(k)}
    if not to_add:
        return None
    return _encrypt(fernet, {**existing, **to_add})


# (primary field, alt field) pairs within each encrypted blob. A name carries both a
# first- and last-name alt (e.g. a surname change on marriage); an email carries one.
_NAME_PAIRS = (('first_name', 'alt_first_name'), ('last_name', 'alt_last_name'))
_EMAIL_PAIRS = (('email', 'alt_email'),)

# (enc column, hash column, primary/alt pairs) for each encrypted blob type.
_NAME_SPEC = ('enc_name', 'name_hash', _NAME_PAIRS)
_EMAIL_SPEC = ('enc_email', 'email_hash', _EMAIL_PAIRS)


# ---------------------------------------------------------------------------
# Dancer ID management
# ---------------------------------------------------------------------------


def _build_field_updates(
    fernet: Fernet,
    mac_key: bytes,
    existing_blob: str | None,
    fields: dict[str, str] | None,
    hash_key: str | None,
    spec: tuple[str, str, tuple[tuple[str, str], ...]],
) -> dict:
    """Compute the SQL column updates for one encrypted blob (name or email).

    On first encounter (no existing blob) encrypts the fields and stores the hash.
    On a later encounter, any primary field whose value differs from the stored one is
    saved as its alt field (written once — never overwritten). Returns {} when there is
    nothing to change.
    """
    enc_col, hash_col, pairs = spec
    if existing_blob is None:
        if fields and hash_key:
            return {enc_col: _encrypt(fernet, fields), hash_col: _value_hash(hash_key, mac_key)}
        return {}
    if not fields:
        return {}
    existing = _decrypt_fields(fernet, existing_blob) or {}
    additions = {}
    for primary, alt in pairs:
        new_val = fields.get(primary, '').lower().strip()
        if new_val and new_val != existing.get(primary, '').lower().strip():
            additions[alt] = fields[primary]
    updated = _with_alts(fernet, existing_blob, additions)
    return {enc_col: updated} if updated is not None else {}


def get_or_create_dancer_id(
    ctx: DbContext,
    name_fields: dict[str, str] | None,
    email_fields: dict[str, str] | None,
) -> str:
    # Canonical join order for the name hash key so the same person matches across sheets.
    name_key = ' '.join(name_fields[k] for k in _CANONICAL_NAME_ORDER if k in name_fields) if name_fields else None
    name_key = name_key or None  # empty string → None
    email_key = next(iter(email_fields.values())) if email_fields else None

    found_id = None
    for field, value in [('email_hash', email_key), ('name_hash', name_key)]:
        if value:
            row = ctx.conn.execute(
                f'SELECT dancer_id FROM dancer WHERE {field}=?',
                (_value_hash(value, ctx.mac_key),),
            ).fetchone()
            if row:
                found_id = row[0]
                break

    if found_id:
        existing_enc_name, existing_enc_email = ctx.conn.execute(
            'SELECT enc_name, enc_email FROM dancer WHERE dancer_id=?', (found_id,)
        ).fetchone()
        updates = _build_field_updates(ctx.fernet, ctx.mac_key, existing_enc_name, name_fields, name_key, _NAME_SPEC)
        updates.update(
            _build_field_updates(ctx.fernet, ctx.mac_key, existing_enc_email, email_fields, email_key, _EMAIL_SPEC)
        )
        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            ctx.conn.execute(f'UPDATE dancer SET {set_clause} WHERE dancer_id=?', (*updates.values(), found_id))
            ctx.conn.commit()
        return found_id

    primary_key = email_key or name_key
    dancer_id = _derive_dancer_id(ctx.id_key, primary_key)
    if ctx.conn.execute('SELECT 1 FROM dancer WHERE dancer_id=?', (dancer_id,)).fetchone():
        raise ValueError(
            f'Dancer ID collision for {dancer_id!r} — two distinct inputs produced the same 8-character prefix. '
            'This is extremely unlikely; verify that the inputs are genuinely different people.'
        )

    ctx.conn.execute(
        'INSERT INTO dancer (dancer_id, enc_name, enc_email, name_hash, email_hash) VALUES (?, ?, ?, ?, ?)',
        (
            dancer_id,
            _encrypt(ctx.fernet, name_fields) if name_fields else None,
            _encrypt(ctx.fernet, email_fields) if email_fields else None,
            _value_hash(name_key, ctx.mac_key) if name_key else None,
            _value_hash(email_key, ctx.mac_key) if email_key else None,
        ),
    )
    ctx.conn.commit()
    return dancer_id


def decrypt_all(ctx: DbContext) -> list[dict]:
    return [
        {
            'dancer_id': d,
            'name': _decrypt_fields(ctx.fernet, n),
            'email': _decrypt_fields(ctx.fernet, e),
        }
        for d, n, e in ctx.conn.execute('SELECT dancer_id, enc_name, enc_email FROM dancer')
    ]


def decrypt_dancer(ctx: DbContext, dancer_id: str) -> dict | None:
    """Decrypt and return the identity for a single dancer_id, or None if not found."""
    row = ctx.conn.execute(
        'SELECT enc_name, enc_email FROM dancer WHERE dancer_id=?',
        (dancer_id,),
    ).fetchone()
    if row is None:
        return None
    n, e = row
    return {
        'dancer_id': dancer_id,
        'name': _decrypt_fields(ctx.fernet, n),
        'email': _decrypt_fields(ctx.fernet, e),
    }


# ---------------------------------------------------------------------------
# Dancer ID substitution (manual de-duplication)
# ---------------------------------------------------------------------------


def _replace_id_in_files(output_dir: Path, old_id: str, new_id: str) -> None:
    for path in sorted(Path(output_dir).rglob('*.xlsx')):
        wb = openpyxl.load_workbook(path)
        changed = False
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value == old_id:
                        cell.value = new_id
                        changed = True
        if changed:
            wb.save(path)


# (enc index, hash index, enc column, hash column, primary/alt pairs) into the SELECTed row tuple.
_MERGE_SPECS = (
    (0, 2, 'enc_name', 'name_hash', _NAME_PAIRS),
    (1, 3, 'enc_email', 'email_hash', _EMAIL_PAIRS),
)


def _merge_updates(fernet: Fernet, old_row: tuple, new_row: tuple, conflicts: dict[str, str | None]) -> dict:
    """Compute the column updates to apply to new_id when merging old_id into it.

    For each blob: move old's value across if new lacks it; otherwise store any supplied
    conflict value as its alt field (written once, never overwritten). conflicts is keyed
    by primary field name ('first_name', 'last_name', 'email'). Both rows are
    (enc_name, enc_email, name_hash, email_hash) tuples.
    """
    updates = {}
    for enc_i, hash_i, enc_col, hash_col, pairs in _MERGE_SPECS:
        if new_row[enc_i] is None and old_row[enc_i] is not None:
            updates[enc_col], updates[hash_col] = old_row[enc_i], old_row[hash_i]
        elif new_row[enc_i] is not None:
            additions = {alt: conflicts.get(primary) for primary, alt in pairs}
            enc = _with_alts(fernet, new_row[enc_i], additions)
            if enc is not None:
                updates[enc_col] = enc
    return updates


_NAME_FIELDS = ('first_name', 'last_name', 'alt_first_name', 'alt_last_name')
_EMAIL_FIELDS = ('email', 'alt_email')


def _clean_fields(fields: dict[str, str] | None, allowed: tuple[str, ...]) -> dict[str, str] | None:
    """Keep only recognised keys with non-blank values, stripped; None if nothing survives."""
    if not fields:
        return None
    out = {k: fields[k].strip() for k in allowed if fields.get(k) and fields[k].strip()}
    return out or None


def update_dancer(
    ctx: DbContext,
    dancer_id: str,
    name_fields: dict[str, str] | None,
    email_fields: dict[str, str] | None,
) -> dict:
    """Overwrite one dancer's stored name/email fields in place and recompute the keyed hashes.

    Raises ValueError if the id is unknown, if the edit would leave the dancer with neither a
    name nor an email, or if the corrected name/email already belongs to a *different* dancer
    (that is a merge, not an edit — use ``substitute_dancer_id`` via the de-duplication flow).
    """
    if ctx.conn.execute('SELECT 1 FROM dancer WHERE dancer_id=?', (dancer_id,)).fetchone() is None:
        raise ValueError(f'{dancer_id!r} not found in database')

    name_fields = _clean_fields(name_fields, _NAME_FIELDS)
    email_fields = _clean_fields(email_fields, _EMAIL_FIELDS)

    name_key = (
        ' '.join(name_fields[k] for k in _CANONICAL_NAME_ORDER if k in name_fields) if name_fields else ''
    ) or None
    email_key = email_fields.get('email') if email_fields else None
    if not name_key and not email_key:
        raise ValueError('A dancer must keep at least a name or an email; this edit would clear both.')

    name_hash = _value_hash(name_key, ctx.mac_key) if name_key else None
    email_hash = _value_hash(email_key, ctx.mac_key) if email_key else None
    for label, col, value in (('name', 'name_hash', name_hash), ('email', 'email_hash', email_hash)):
        if value is None:
            continue
        clash = ctx.conn.execute(
            f'SELECT dancer_id FROM dancer WHERE {col}=? AND dancer_id!=?', (value, dancer_id)
        ).fetchone()
        if clash:
            raise ValueError(
                f'That {label} already belongs to {clash[0]}; that is a merge, not an edit — '
                'combine them from the de-duplication review instead.'
            )

    ctx.conn.execute(
        'UPDATE dancer SET enc_name=?, enc_email=?, name_hash=?, email_hash=? WHERE dancer_id=?',
        (
            _encrypt(ctx.fernet, name_fields) if name_fields else None,
            _encrypt(ctx.fernet, email_fields) if email_fields else None,
            name_hash,
            email_hash,
            dancer_id,
        ),
    )
    ctx.conn.commit()
    return decrypt_dancer(ctx, dancer_id)


def substitute_dancer_id(  # noqa: PLR0913
    ctx: DbContext,
    old_id: str,
    new_id: str,
    output_dir: Path | None = None,
    conflict_first_name: str | None = None,
    conflict_last_name: str | None = None,
    conflict_email: str | None = None,
) -> None:
    """Replace old_id with new_id in the database and all xlsx files in output_dir.

    Merges old_id's name/email into new_id if new_id is missing those fields, then
    deletes old_id. Delete happens before update to avoid UNIQUE constraint conflicts
    when moving a name_hash or email_hash from one row to another.

    When both records have a differing first name, last name, or email, pass
    conflict_first_name / conflict_last_name / conflict_email to store the discarded
    value as alt_first_name / alt_last_name / alt_email on new_id (each written once,
    never overwritten). Leave any of them None to discard that field silently.
    """
    old_row = ctx.conn.execute(
        'SELECT enc_name, enc_email, name_hash, email_hash FROM dancer WHERE dancer_id=?',
        (old_id,),
    ).fetchone()
    new_row = ctx.conn.execute(
        'SELECT enc_name, enc_email, name_hash, email_hash FROM dancer WHERE dancer_id=?',
        (new_id,),
    ).fetchone()

    if old_row is None:
        raise ValueError(f'{old_id!r} not found in database')
    if new_row is None:
        raise ValueError(f'{new_id!r} not found in database')

    # Delete first so the UNIQUE constraints on name_hash/email_hash are freed.
    ctx.conn.execute('DELETE FROM dancer WHERE dancer_id=?', (old_id,))

    conflicts = {
        'first_name': conflict_first_name,
        'last_name': conflict_last_name,
        'email': conflict_email,
    }
    updates = _merge_updates(ctx.fernet, old_row, new_row, conflicts)
    if updates:
        set_clause = ', '.join(f'{k}=?' for k in updates)
        ctx.conn.execute(f'UPDATE dancer SET {set_clause} WHERE dancer_id=?', (*updates.values(), new_id))

    ctx.conn.commit()

    if output_dir is not None:
        _replace_id_in_files(Path(output_dir), old_id, new_id)
