import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl
from cryptography.fernet import Fernet, InvalidToken

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
NAME_HEADERS = re.compile(
    r'\b(name|first[_\s-]?name|last[_\s-]?name|surname|forename|full[_\s-]?name|member)\b',
    re.IGNORECASE,
)
EMAIL_HEADERS = re.compile(r'\be[_\s-]?mail\b', re.IGNORECASE)

# Fullmatch variants for header-row detection — prevents matching header words
# that appear as substrings in note cells like 'NB: order A_Z by First Name'.
_NAME_CELL_EXACT = re.compile(
    r'first[_\s-]?name|last[_\s-]?name|surname|forename|full[_\s-]?name|name|member', re.IGNORECASE
)
_EMAIL_CELL_EXACT = re.compile(r'e[_\s-]?mail', re.IGNORECASE)

_SENTINEL = b'esds-pseudonymise-v1'

# Accepts letters (including accented), apostrophes, hyphens, spaces, and initials with periods.
# Rejects anything with digits or most punctuation — i.e. footer notes, not names.
_VALID_NAME_RE = re.compile(r"^[A-Za-zÀ-ž'\-\s\.]+$")
_EMAIL_MATCH_THRESHOLD = 0.5

_CANONICAL_NAME_ORDER = ['first_name', 'last_name']


def _canonical_name_key(col: str) -> str:
    col_l = col.lower()
    if re.search(r'first|fore', col_l):
        return 'first_name'
    if re.search(r'last|sur', col_l):
        return 'last_name'
    if re.search(r'name|member', col_l):
        return 'first_name'
    raise ValueError(f'Cannot map column {col!r} to a canonical name field (expected first/forename or last/surname)')


# ---------------------------------------------------------------------------
# Key derivation and database
# ---------------------------------------------------------------------------


def _derive_keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    """One PBKDF2 call → 64 bytes → split into Fernet key and HMAC key."""
    raw = hashlib.pbkdf2_hmac('sha256', passphrase.encode(), salt, 480_000, dklen=64)
    return base64.urlsafe_b64encode(raw[:32]), raw[32:]


def _setup_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute(
        'CREATE TABLE IF NOT EXISTS pseudonyms ('
        '  dancer_id   TEXT PRIMARY KEY,'
        '  enc_name    TEXT,'
        '  enc_email   TEXT,'
        '  name_hash   TEXT UNIQUE,'
        '  email_hash  TEXT UNIQUE)'
    )
    conn.commit()
    return conn


def open_db(db_path: Path, passphrase: str) -> tuple[sqlite3.Connection, Fernet, bytes]:
    """Open (or create) the database. Returns (conn, fernet, mac_key).

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

    return conn, fernet, mac_key


# ---------------------------------------------------------------------------
# Dancer ID management
# ---------------------------------------------------------------------------


def _value_hash(value: str, mac_key: bytes) -> str:
    return hmac.new(mac_key, value.lower().strip().encode(), 'sha256').hexdigest()


def _build_name_updates(  # noqa: PLR0913
    fernet: Fernet,
    mac_key: bytes,
    existing_enc_name: str | None,
    name_fields: dict[str, str] | None,
    name_key: str | None,
) -> dict:
    if existing_enc_name is None and name_fields and name_key:
        return {
            'enc_name': fernet.encrypt(json.dumps(name_fields).encode()).decode(),
            'name_hash': _value_hash(name_key, mac_key),
        }
    if existing_enc_name is not None and name_fields:
        existing_name = _decrypt_fields(fernet, existing_enc_name) or {}
        stored_first = existing_name.get('first_name', '').lower().strip()
        new_first = name_fields.get('first_name', '').lower().strip()
        if new_first and new_first != stored_first and not existing_name.get('alt_first_name'):
            updated_name = {**existing_name, 'alt_first_name': name_fields['first_name']}
            return {'enc_name': fernet.encrypt(json.dumps(updated_name).encode()).decode()}
    return {}


def get_or_create_dancer_id(
    conn: sqlite3.Connection,
    fernet: Fernet,
    mac_key: bytes,
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
            row = conn.execute(
                f'SELECT dancer_id FROM pseudonyms WHERE {field}=?',
                (_value_hash(value, mac_key),),
            ).fetchone()
            if row:
                found_id = row[0]
                break

    if found_id:
        existing_enc_name, existing_enc_email = conn.execute(
            'SELECT enc_name, enc_email FROM pseudonyms WHERE dancer_id=?', (found_id,)
        ).fetchone()
        updates = _build_name_updates(fernet, mac_key, existing_enc_name, name_fields, name_key)
        if existing_enc_email is None and email_fields and email_key:
            updates['enc_email'] = fernet.encrypt(json.dumps(email_fields).encode()).decode()
            updates['email_hash'] = _value_hash(email_key, mac_key)
        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE pseudonyms SET {set_clause} WHERE dancer_id=?', (*updates.values(), found_id))
            conn.commit()
        return found_id

    while True:
        dancer_id = f'DNC-{secrets.token_hex(4).upper()}'
        if not conn.execute('SELECT 1 FROM pseudonyms WHERE dancer_id=?', (dancer_id,)).fetchone():
            break

    conn.execute(
        'INSERT INTO pseudonyms (dancer_id, enc_name, enc_email, name_hash, email_hash) VALUES (?, ?, ?, ?, ?)',
        (
            dancer_id,
            fernet.encrypt(json.dumps(name_fields).encode()).decode() if name_fields else None,
            fernet.encrypt(json.dumps(email_fields).encode()).decode() if email_fields else None,
            _value_hash(name_key, mac_key) if name_key else None,
            _value_hash(email_key, mac_key) if email_key else None,
        ),
    )
    conn.commit()
    return dancer_id


def _decrypt_fields(fernet: Fernet, blob: str | None) -> dict | None:
    return json.loads(fernet.decrypt(blob.encode()).decode()) if blob else None


def decrypt_all(conn: sqlite3.Connection, fernet: Fernet) -> list[dict]:
    return [
        {
            'dancer_id': d,
            'name': _decrypt_fields(fernet, n),
            'email': _decrypt_fields(fernet, e),
        }
        for d, n, e in conn.execute('SELECT dancer_id, enc_name, enc_email FROM pseudonyms')
    ]


def decrypt_dancer(conn: sqlite3.Connection, fernet: Fernet, dancer_id: str) -> dict | None:
    """Decrypt and return the identity for a single dancer_id, or None if not found."""
    row = conn.execute(
        'SELECT enc_name, enc_email FROM pseudonyms WHERE dancer_id=?',
        (dancer_id,),
    ).fetchone()
    if row is None:
        return None
    n, e = row
    return {
        'dancer_id': dancer_id,
        'name': _decrypt_fields(fernet, n),
        'email': _decrypt_fields(fernet, e),
    }


# ---------------------------------------------------------------------------
# XLSX I/O
# ---------------------------------------------------------------------------


def _read_sheet(ws) -> tuple[list[str], list[dict], list[list]]:
    """Read an openpyxl worksheet. Returns (fieldnames, rows, prefix_rows).

    Scans for the first row where any cell exactly matches a known name/email
    header. Rows above that are returned as prefix_rows and written back verbatim.
    """
    all_rows = [[str(c) if c is not None else '' for c in row] for row in ws.iter_rows(values_only=True)]

    header_idx = 0
    for i, row in enumerate(all_rows):
        if any(_NAME_CELL_EXACT.fullmatch(cell.strip()) or _EMAIL_CELL_EXACT.fullmatch(cell.strip()) for cell in row):
            header_idx = i
            break

    fieldnames = all_rows[header_idx] if all_rows else []
    rows = []
    for raw in all_rows[header_idx + 1 :] if all_rows else []:
        if not any(cell.strip() for cell in raw):
            continue
        padded = (raw + [''] * len(fieldnames))[: len(fieldnames)]
        rows.append(dict(zip(fieldnames, padded)))

    return fieldnames, rows, all_rows[:header_idx]


def _write_sheet(
    ws_out,
    out_fieldnames: list[str],
    rows: list[dict],
    original_fieldnames: list[str],
    prefix_rows: list[list],
) -> None:
    for row in prefix_rows:
        ws_out.append(row)
    ws_out.append(out_fieldnames)
    for row in rows:
        ws_out.append([row[c] for c in original_fieldnames])


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------


def detect_columns(fieldnames: list[str], rows: list[dict]) -> dict[str, list]:
    name_cols, email_cols = [], []
    for col in fieldnames:
        if EMAIL_HEADERS.search(col):
            email_cols.append(col)
        elif NAME_HEADERS.search(col):
            name_cols.append(col)

    # Content sniff over at most 5 rows — short columns are common.
    for col in fieldnames:
        if col in email_cols:
            continue
        sample = [r[col] for r in rows[:5] if r.get(col, '').strip()]
        if sample and sum(bool(EMAIL_RE.match(v.strip())) for v in sample) / len(sample) > _EMAIL_MATCH_THRESHOLD:
            email_cols.append(col)

    return {'name_cols': name_cols, 'email_cols': email_cols}


# ---------------------------------------------------------------------------
# Sheet-level pseudonymisation (internal)
# ---------------------------------------------------------------------------


def _pseudonymise_sheet(  # noqa: PLR0913
    ws,
    ws_out,
    conn: sqlite3.Connection,
    fernet: Fernet,
    mac_key: bytes,
    name_cols: list | None = None,
    email_cols: list | None = None,
) -> tuple[list[dict], int]:
    """Pseudonymise one worksheet and write to ws_out. Returns (rows, new_count)."""
    fieldnames, rows, prefix_rows = _read_sheet(ws)

    detected = detect_columns(fieldnames, rows)
    _name_cols = name_cols if name_cols is not None else detected['name_cols']
    _email_cols = email_cols if email_cols is not None else detected['email_cols']
    print(f'  Name columns:  {_name_cols}')
    print(f'  Email columns: {_email_cols}')

    pii_cols = sorted(_name_cols + _email_cols, key=lambda c: fieldnames.index(c))
    col_rename = {col: ('dancer_id' if i == 0 else 'redacted') for i, col in enumerate(pii_cols)}
    out_fieldnames = [col_rename.get(c, c) for c in fieldnames]

    new_count = 0
    for row in rows:
        raw_name = {}
        for c in _name_cols:
            val = row.get(c, '').strip()
            if val and _VALID_NAME_RE.match(val):
                raw_name[_canonical_name_key(c)] = val
        name_fields = {k: raw_name[k] for k in _CANONICAL_NAME_ORDER if k in raw_name} or None

        email_val = next((row[c].strip() for c in _email_cols if EMAIL_RE.match(row.get(c, '').strip())), None)
        email_fields = {'email': email_val} if email_val else None

        if not name_fields and not email_fields:
            continue

        before = conn.execute('SELECT COUNT(*) FROM pseudonyms').fetchone()[0]
        dancer_id = get_or_create_dancer_id(conn, fernet, mac_key, name_fields, email_fields)
        new_count += conn.execute('SELECT COUNT(*) FROM pseudonyms').fetchone()[0] - before

        row[pii_cols[0]] = dancer_id
        for col in pii_cols[1:]:
            row[col] = 'redacted'

    _write_sheet(ws_out, out_fieldnames, rows, fieldnames, prefix_rows)
    return rows, new_count


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def pseudonymise(  # noqa: PLR0913
    spreadsheet_path: Path,
    db_path: Path,
    passphrase: str,
    output_path: Path | None = None,
    name_cols: list | None = None,
    email_cols: list | None = None,
    suffix: str = '_pseudonymised',
) -> dict[str, list[dict]]:
    """Read an xlsx workbook, replace name/email cells with stable dancer IDs, write the redacted copy.

    Processes all sheets. Returns {sheet_name: rows} for each sheet.
    """
    conn, fernet, mac_key = open_db(db_path, passphrase)
    wb = openpyxl.load_workbook(spreadsheet_path)
    wb_out = openpyxl.Workbook()
    wb_out.remove(wb_out.active)

    result = {}
    total_new = 0
    for ws in wb.worksheets:
        print(f'\nSheet: {ws.title}')
        ws_out = wb_out.create_sheet(ws.title)
        rows, new_count = _pseudonymise_sheet(ws, ws_out, conn, fernet, mac_key, name_cols, email_cols)
        result[ws.title] = rows
        total_new += new_count

    conn.close()

    if output_path is None:
        p = Path(spreadsheet_path)
        output_path = p.parent / f'{p.stem}{suffix}{p.suffix}'

    wb_out.save(output_path)
    print(f'\nNew dancer IDs created: {total_new}')
    print(f'Output written to:      {output_path}')
    return result


def pseudonymise_folder(  # noqa: PLR0913
    input_dir: Path,
    output_dir: Path,
    db_path: Path,
    passphrase: str,
    suffix: str = '_pseudonymised',
    name_cols: list | None = None,
    email_cols: list | None = None,
) -> None:
    """Pseudonymise all xlsx files in input_dir (recursively), writing results to output_dir.

    Preserves subdirectory structure. Output filenames get suffix appended before the extension.
    All files share a single database so dancer IDs are consistent across the whole dataset.
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    conn, fernet, mac_key = open_db(db_path, passphrase)
    total_new = 0

    for src in sorted(input_dir.rglob('*.xlsx')):
        rel = src.relative_to(input_dir)
        dst = output_dir / rel.parent / f'{src.stem}{suffix}{src.suffix}'
        dst.parent.mkdir(parents=True, exist_ok=True)

        print(f'\n{rel}')
        wb = openpyxl.load_workbook(src)
        wb_out = openpyxl.Workbook()
        wb_out.remove(wb_out.active)

        for ws in wb.worksheets:
            print(f'  Sheet: {ws.title}')
            ws_out = wb_out.create_sheet(ws.title)
            _, new_count = _pseudonymise_sheet(ws, ws_out, conn, fernet, mac_key, name_cols, email_cols)
            total_new += new_count

        wb_out.save(dst)
        print(f'  → {dst}')

    conn.close()
    print(f'\nTotal new dancer IDs: {total_new}')


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


def substitute_dancer_id(  # noqa: PLR0913
    conn: sqlite3.Connection,
    fernet: Fernet,
    mac_key: bytes,
    old_id: str,
    new_id: str,
    output_dir: Path | None = None,
) -> None:
    """Replace old_id with new_id in the database and all xlsx files in output_dir.

    Merges old_id's name/email into new_id if new_id is missing those fields, then
    deletes old_id. Delete happens before update to avoid UNIQUE constraint conflicts
    when moving a name_hash or email_hash from one row to another.
    """
    old_row = conn.execute(
        'SELECT enc_name, enc_email, name_hash, email_hash FROM pseudonyms WHERE dancer_id=?',
        (old_id,),
    ).fetchone()
    new_row = conn.execute(
        'SELECT enc_name, enc_email, name_hash, email_hash FROM pseudonyms WHERE dancer_id=?',
        (new_id,),
    ).fetchone()

    if old_row is None:
        raise ValueError(f'{old_id!r} not found in database')
    if new_row is None:
        raise ValueError(f'{new_id!r} not found in database')

    # Capture what we might want to move before deleting old_id.
    name_to_move = (old_row[0], old_row[2]) if new_row[0] is None and old_row[0] is not None else None
    email_to_move = (old_row[1], old_row[3]) if new_row[1] is None and old_row[1] is not None else None

    # Delete first so the UNIQUE constraints on name_hash/email_hash are freed.
    conn.execute('DELETE FROM pseudonyms WHERE dancer_id=?', (old_id,))

    updates = {}
    if name_to_move:
        updates['enc_name'], updates['name_hash'] = name_to_move
    if email_to_move:
        updates['enc_email'], updates['email_hash'] = email_to_move
    if updates:
        set_clause = ', '.join(f'{k}=?' for k in updates)
        conn.execute(f'UPDATE pseudonyms SET {set_clause} WHERE dancer_id=?', (*updates.values(), new_id))

    conn.commit()

    if output_dir is not None:
        _replace_id_in_files(Path(output_dir), old_id, new_id)


# ---------------------------------------------------------------------------
# Fuzzy duplicate detection
# ---------------------------------------------------------------------------


def find_duplicate_candidates(
    conn: sqlite3.Connection,
    fernet: Fernet,
    threshold: float = 0.8,
) -> list[tuple[dict, dict, float]]:
    """Scan the database for pairs of dancers with similar names or emails.

    Uses difflib.SequenceMatcher (stdlib). Returns list of (dancer_a, dancer_b, score)
    sorted by score descending, where score is the best match across name and email.
    """
    all_dancers = decrypt_all(conn, fernet)
    candidates = []

    for i, a in enumerate(all_dancers):
        for b in all_dancers[i + 1 :]:
            best = 0.0

            an = a.get('name') or {}
            bn = b.get('name') or {}
            a_full = f'{an.get("first_name", "")} {an.get("last_name", "")}'.strip().lower()
            b_full = f'{bn.get("first_name", "")} {bn.get("last_name", "")}'.strip().lower()
            if a_full and b_full:
                best = max(best, SequenceMatcher(None, a_full, b_full).ratio())

            ae = (a.get('email') or {}).get('email', '').lower()
            be = (b.get('email') or {}).get('email', '').lower()
            if ae and be:
                best = max(best, SequenceMatcher(None, ae, be).ratio())

            if best >= threshold:
                candidates.append((a, b, best))

    return sorted(candidates, key=lambda x: x[2], reverse=True)
