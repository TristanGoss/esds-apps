import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from cryptography.fernet import Fernet, InvalidToken
from rapidfuzz import fuzz

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
NAME_HEADERS = re.compile(
    r'\b(name|first[_\s-]?name|last[_\s-]?name|surname|forename|full[_\s-]?name)\b',
    re.IGNORECASE,
)
_MEMBER_EXACT = re.compile(r'^member$', re.IGNORECASE)
EMAIL_HEADERS = re.compile(r'\be[_\s-]?mail(\s*(address|addr\.?))?\s*$', re.IGNORECASE)

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
# Dancer ID management
# ---------------------------------------------------------------------------


def _value_hash(value: str, mac_key: bytes) -> str:
    return hmac.new(mac_key, value.lower().strip().encode(), 'sha256').hexdigest()


def _encrypt(fernet: Fernet, fields: dict) -> str:
    return fernet.encrypt(json.dumps(fields).encode()).decode()


def _with_alt(fernet: Fernet, blob: str | None, alt_key: str, value: str) -> str | None:
    """Return blob re-encrypted with alt_key set to value, or None if an alt is already present."""
    existing = _decrypt_fields(fernet, blob) or {}
    if existing.get(alt_key):
        return None
    return _encrypt(fernet, {**existing, alt_key: value})


# (enc column, hash column, primary field, alt field) for each encrypted blob type.
_NAME_SPEC = ('enc_name', 'name_hash', 'first_name', 'alt_first_name')
_EMAIL_SPEC = ('enc_email', 'email_hash', 'email', 'alt_email')


def _build_field_updates(  # noqa: PLR0913
    fernet: Fernet,
    mac_key: bytes,
    existing_blob: str | None,
    fields: dict[str, str] | None,
    hash_key: str | None,
    spec: tuple[str, str, str, str],
) -> dict:
    """Compute the SQL column updates for one encrypted blob (name or email).

    On first encounter (no existing blob) encrypts the fields and stores the hash.
    On a later encounter with a differing primary value, stores it as the alt field
    (written once — never overwritten). Returns {} when there is nothing to change.
    """
    enc_col, hash_col, primary, alt = spec
    if existing_blob is None:
        if fields and hash_key:
            return {enc_col: _encrypt(fernet, fields), hash_col: _value_hash(hash_key, mac_key)}
        return {}
    if not fields:
        return {}
    existing = _decrypt_fields(fernet, existing_blob) or {}
    new_val = fields.get(primary, '').lower().strip()
    if new_val and new_val != existing.get(primary, '').lower().strip():
        updated = _with_alt(fernet, existing_blob, alt, fields[primary])
        if updated is not None:
            return {enc_col: updated}
    return {}


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
                f'SELECT dancer_id FROM pseudonyms WHERE {field}=?',
                (_value_hash(value, ctx.mac_key),),
            ).fetchone()
            if row:
                found_id = row[0]
                break

    if found_id:
        existing_enc_name, existing_enc_email = ctx.conn.execute(
            'SELECT enc_name, enc_email FROM pseudonyms WHERE dancer_id=?', (found_id,)
        ).fetchone()
        updates = _build_field_updates(ctx.fernet, ctx.mac_key, existing_enc_name, name_fields, name_key, _NAME_SPEC)
        updates.update(
            _build_field_updates(ctx.fernet, ctx.mac_key, existing_enc_email, email_fields, email_key, _EMAIL_SPEC)
        )
        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            ctx.conn.execute(f'UPDATE pseudonyms SET {set_clause} WHERE dancer_id=?', (*updates.values(), found_id))
            ctx.conn.commit()
        return found_id

    primary_key = email_key or name_key
    dancer_id = _derive_dancer_id(ctx.id_key, primary_key)
    if ctx.conn.execute('SELECT 1 FROM pseudonyms WHERE dancer_id=?', (dancer_id,)).fetchone():
        raise ValueError(
            f'Dancer ID collision for {dancer_id!r} — two distinct inputs produced the same 8-character prefix. '
            'This is extremely unlikely; verify that the inputs are genuinely different people.'
        )

    ctx.conn.execute(
        'INSERT INTO pseudonyms (dancer_id, enc_name, enc_email, name_hash, email_hash) VALUES (?, ?, ?, ?, ?)',
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


def _decrypt_fields(fernet: Fernet, blob: str | None) -> dict | None:
    return json.loads(fernet.decrypt(blob.encode()).decode()) if blob else None


def decrypt_all(ctx: DbContext) -> list[dict]:
    return [
        {
            'dancer_id': d,
            'name': _decrypt_fields(ctx.fernet, n),
            'email': _decrypt_fields(ctx.fernet, e),
        }
        for d, n, e in ctx.conn.execute('SELECT dancer_id, enc_name, enc_email FROM pseudonyms')
    ]


def decrypt_dancer(ctx: DbContext, dancer_id: str) -> dict | None:
    """Decrypt and return the identity for a single dancer_id, or None if not found."""
    row = ctx.conn.execute(
        'SELECT enc_name, enc_email FROM pseudonyms WHERE dancer_id=?',
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
        elif '/' not in col and (NAME_HEADERS.search(col) or _MEMBER_EXACT.fullmatch(col.strip())):
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


def _pseudonymise_sheet(
    ws,
    ws_out,
    ctx: DbContext,
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

    before = ctx.conn.execute('SELECT COUNT(*) FROM pseudonyms').fetchone()[0]
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

        dancer_id = get_or_create_dancer_id(ctx, name_fields, email_fields)

        row[pii_cols[0]] = dancer_id
        for col in pii_cols[1:]:
            row[col] = 'redacted'

    new_count = ctx.conn.execute('SELECT COUNT(*) FROM pseudonyms').fetchone()[0] - before
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
    ctx = open_db(db_path, passphrase)
    wb = openpyxl.load_workbook(spreadsheet_path)
    wb_out = openpyxl.Workbook()
    wb_out.remove(wb_out.active)

    result = {}
    total_new = 0
    for ws in wb.worksheets:
        print(f'\nSheet: {ws.title}')
        ws_out = wb_out.create_sheet(ws.title)
        rows, new_count = _pseudonymise_sheet(ws, ws_out, ctx, name_cols, email_cols)
        result[ws.title] = rows
        total_new += new_count

    ctx.conn.close()

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
    ctx = open_db(db_path, passphrase)
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
            _, new_count = _pseudonymise_sheet(ws, ws_out, ctx, name_cols, email_cols)
            total_new += new_count

        wb_out.save(dst)
        print(f'  → {dst}')

    ctx.conn.close()
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


# (enc index, hash index, enc column, hash column, alt field) into the SELECTed row tuple.
_MERGE_SPECS = (
    (0, 2, 'enc_name', 'name_hash', 'alt_first_name'),
    (1, 3, 'enc_email', 'email_hash', 'alt_email'),
)


def _merge_updates(
    fernet: Fernet,
    old_row: tuple,
    new_row: tuple,
    conflict_first_name: str | None,
    conflict_email: str | None,
) -> dict:
    """Compute the column updates to apply to new_id when merging old_id into it.

    For each blob: move old's value across if new lacks it; otherwise store the
    supplied conflict value as the alt field (written once, never overwritten).
    Both rows are (enc_name, enc_email, name_hash, email_hash) tuples.
    """
    conflicts = (conflict_first_name, conflict_email)
    updates = {}
    for (enc_i, hash_i, enc_col, hash_col, alt_key), conflict in zip(_MERGE_SPECS, conflicts):
        if new_row[enc_i] is None and old_row[enc_i] is not None:
            updates[enc_col], updates[hash_col] = old_row[enc_i], old_row[hash_i]
        elif conflict is not None and new_row[enc_i] is not None:
            enc = _with_alt(fernet, new_row[enc_i], alt_key, conflict)
            if enc is not None:
                updates[enc_col] = enc
    return updates


def substitute_dancer_id(  # noqa: PLR0913
    ctx: DbContext,
    old_id: str,
    new_id: str,
    output_dir: Path | None = None,
    conflict_first_name: str | None = None,
    conflict_email: str | None = None,
) -> None:
    """Replace old_id with new_id in the database and all xlsx files in output_dir.

    Merges old_id's name/email into new_id if new_id is missing those fields, then
    deletes old_id. Delete happens before update to avoid UNIQUE constraint conflicts
    when moving a name_hash or email_hash from one row to another.

    When both records have differing first names, pass conflict_first_name to store
    the discarded first name as alt_first_name on new_id. When both records have
    differing emails, pass conflict_email to store it as alt_email. Leave either None
    to discard silently.
    """
    old_row = ctx.conn.execute(
        'SELECT enc_name, enc_email, name_hash, email_hash FROM pseudonyms WHERE dancer_id=?',
        (old_id,),
    ).fetchone()
    new_row = ctx.conn.execute(
        'SELECT enc_name, enc_email, name_hash, email_hash FROM pseudonyms WHERE dancer_id=?',
        (new_id,),
    ).fetchone()

    if old_row is None:
        raise ValueError(f'{old_id!r} not found in database')
    if new_row is None:
        raise ValueError(f'{new_id!r} not found in database')

    # Delete first so the UNIQUE constraints on name_hash/email_hash are freed.
    ctx.conn.execute('DELETE FROM pseudonyms WHERE dancer_id=?', (old_id,))

    updates = _merge_updates(ctx.fernet, old_row, new_row, conflict_first_name, conflict_email)
    if updates:
        set_clause = ', '.join(f'{k}=?' for k in updates)
        ctx.conn.execute(f'UPDATE pseudonyms SET {set_clause} WHERE dancer_id=?', (*updates.values(), new_id))

    ctx.conn.commit()

    if output_dir is not None:
        _replace_id_in_files(Path(output_dir), old_id, new_id)


# ---------------------------------------------------------------------------
# Fuzzy duplicate detection
# ---------------------------------------------------------------------------


def _all_emails(dancer: dict) -> list[str]:
    e = dancer.get('email') or {}
    return [e[k].lower() for k in ('email', 'alt_email') if e.get(k)]


def _email_local(email: str) -> str:
    return re.sub(r'[._\-]', ' ', email.split('@')[0])


def _full_name(dancer: dict) -> str:
    n = dancer.get('name') or {}
    return f'{n.get("first_name", "")} {n.get("last_name", "")}'.strip().lower()


def _pair_score(a: tuple, b: tuple) -> float:
    """Best fuzzy match between two prepared (dancer, full_name, emails, email_locals) tuples."""
    _, a_full, a_emails, a_locals = a
    _, b_full, b_emails, b_locals = b
    best = 0.0
    if a_full and b_full:
        best = fuzz.ratio(a_full, b_full) / 100
    for ae in a_emails:
        for be in b_emails:
            best = max(best, fuzz.ratio(ae, be) / 100)
    for full, locals_ in ((a_full, b_locals), (b_full, a_locals)):
        if full:
            for loc in locals_:
                best = max(best, fuzz.ratio(full, loc) / 100)
    return best


def find_duplicate_candidates(
    ctx: DbContext,
    threshold: float = 0.8,
) -> list[tuple[dict, dict, float]]:
    """Scan the database for pairs of dancers with similar names or emails.

    Returns list of (dancer_a, dancer_b, score) sorted by score descending,
    where score is the best match across name and email.
    """
    # Precompute each dancer's comparison fields once, rather than rebuilding them
    # for every pair (the comparison is O(n²)).
    prepared = [
        (d, _full_name(d), emails, [_email_local(e) for e in emails])
        for d in decrypt_all(ctx)
        for emails in [_all_emails(d)]
    ]

    candidates = []
    for i, a in enumerate(prepared):
        for b in prepared[i + 1 :]:
            score = _pair_score(a, b)
            if score >= threshold:
                candidates.append((a[0], b[0], score))

    return sorted(candidates, key=lambda x: x[2], reverse=True)


def search_dancer(
    ctx: DbContext,
    query: str,
    threshold: float = 0.6,
    max_results: int = 10,
) -> list[tuple[dict, float]]:
    """Fuzzy-search the database for dancers matching a name or email query.

    Compares query against full name, email, and email local part. Returns
    list of (dancer, score) sorted by score descending, capped at max_results.
    """
    q = query.strip().lower()
    q_local = _email_local(q) if '@' in q else q

    results = []
    for dancer in decrypt_all(ctx):
        full_name = _full_name(dancer)

        best = 0.0
        if full_name:
            best = max(best, fuzz.partial_ratio(q, full_name) / 100)
        for email in _all_emails(dancer):
            best = max(best, fuzz.partial_ratio(q, email) / 100)
            best = max(best, fuzz.partial_ratio(q_local, _email_local(email)) / 100)

        if best >= threshold:
            results.append((dancer, best))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:max_results]
