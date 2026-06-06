import json

import openpyxl
import pytest

from esds_apps.attendance import pseudonymise

PASSPHRASE = 'test-passphrase-esds'


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / 'test.sqlite'


@pytest.fixture
def ctx(tmp_db):
    """Open a fresh DB and yield a DbContext, closing on teardown."""
    c = pseudonymise.open_db(tmp_db, PASSPHRASE)
    yield c
    c.conn.close()


@pytest.fixture
def simple_xlsx(tmp_path):
    """Single-sheet xlsx with standard headers."""
    p = tmp_path / 'simple.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(['First Name', 'Last Name', 'Email', 'Notes'])
    ws.append(['Alice', 'Smith', 'alice@example.com', 'Regular'])
    ws.append(['Bob', 'Jones', 'bob@example.com', 'VIP'])
    wb.save(p)
    return p


@pytest.fixture
def prefixed_xlsx(tmp_path):
    """Xlsx with two metadata rows above the header."""
    p = tmp_path / 'prefixed.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(['Class Register'])
    ws.append(['Level 1'])
    ws.append(['Forename', 'Surname', 'E-mail', 'Attended'])
    ws.append(['Victoria', 'McLean', 'v@example.com', 'Yes'])
    wb.save(p)
    return p


# ============================================================================
# Key derivation
# ============================================================================


def test_derive_keys_length():
    import secrets

    salt = secrets.token_bytes(32)
    fernet_key, mac_key = pseudonymise._derive_keys(PASSPHRASE, salt)
    assert len(mac_key) == 32
    assert len(fernet_key) > 32  # urlsafe-base64 encoded


def test_derive_keys_deterministic():
    import secrets

    salt = secrets.token_bytes(32)
    k1a, k1b = pseudonymise._derive_keys(PASSPHRASE, salt)
    k2a, k2b = pseudonymise._derive_keys(PASSPHRASE, salt)
    assert k1a == k2a
    assert k1b == k2b


def test_derive_keys_differs_with_different_salt():
    import secrets

    _, mac1 = pseudonymise._derive_keys(PASSPHRASE, secrets.token_bytes(32))
    _, mac2 = pseudonymise._derive_keys(PASSPHRASE, secrets.token_bytes(32))
    assert mac1 != mac2


def test_derive_id_key_deterministic():
    assert pseudonymise.derive_id_key(PASSPHRASE) == pseudonymise.derive_id_key(PASSPHRASE)


def test_derive_id_key_differs_with_different_passphrase():
    assert pseudonymise.derive_id_key(PASSPHRASE) != pseudonymise.derive_id_key('other-passphrase')


# ============================================================================
# Database setup and passphrase validation
# ============================================================================


def test_setup_db_creates_tables(tmp_db):
    conn = pseudonymise._setup_db(tmp_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'meta', 'pseudonyms'} <= tables
    conn.close()


def test_open_db_creates_file(tmp_db):
    assert not tmp_db.exists()
    c = pseudonymise.open_db(tmp_db, PASSPHRASE)
    c.conn.close()
    assert tmp_db.exists()


def test_open_db_returns_dbcontext(tmp_db):
    c = pseudonymise.open_db(tmp_db, PASSPHRASE)
    assert isinstance(c, pseudonymise.DbContext)
    c.conn.close()


def test_open_db_stores_salt(tmp_db):
    c = pseudonymise.open_db(tmp_db, PASSPHRASE)
    salt_row = c.conn.execute('SELECT value FROM meta WHERE key="salt"').fetchone()
    c.conn.close()
    assert salt_row is not None


def test_open_db_reuses_salt_across_opens(tmp_db):
    c1 = pseudonymise.open_db(tmp_db, PASSPHRASE)
    mac1 = c1.mac_key
    c1.conn.close()
    c2 = pseudonymise.open_db(tmp_db, PASSPHRASE)
    mac2 = c2.mac_key
    c2.conn.close()
    assert mac1 == mac2


def test_open_db_wrong_passphrase_raises(tmp_db):
    c = pseudonymise.open_db(tmp_db, PASSPHRASE)
    c.conn.close()
    with pytest.raises(ValueError, match='Wrong passphrase'):
        pseudonymise.open_db(tmp_db, 'wrong-passphrase')


# ============================================================================
# Value hashing
# ============================================================================


def test_value_hash_consistent(ctx):
    assert pseudonymise._value_hash('Alice Smith', ctx.mac_key) == pseudonymise._value_hash('Alice Smith', ctx.mac_key)


def test_value_hash_case_insensitive(ctx):
    assert pseudonymise._value_hash('alice smith', ctx.mac_key) == pseudonymise._value_hash('ALICE SMITH', ctx.mac_key)


def test_value_hash_strips_whitespace(ctx):
    assert pseudonymise._value_hash('Alice', ctx.mac_key) == pseudonymise._value_hash('  Alice  ', ctx.mac_key)


def test_value_hash_differs_for_different_values(ctx):
    assert pseudonymise._value_hash('Alice', ctx.mac_key) != pseudonymise._value_hash('Bob', ctx.mac_key)


# ============================================================================
# Column canonicalisation
# ============================================================================


def test_canonical_name_key_first_name_variants():
    assert pseudonymise._canonical_name_key('First Name') == 'first_name'
    assert pseudonymise._canonical_name_key('first_name') == 'first_name'
    assert pseudonymise._canonical_name_key('Forename') == 'first_name'


def test_canonical_name_key_last_name_variants():
    assert pseudonymise._canonical_name_key('Last Name') == 'last_name'
    assert pseudonymise._canonical_name_key('Surname') == 'last_name'
    assert pseudonymise._canonical_name_key('LAST NAME') == 'last_name'


def test_canonical_name_key_generic_name_maps_to_first():
    assert pseudonymise._canonical_name_key('Name') == 'first_name'
    assert pseudonymise._canonical_name_key('Full Name') == 'first_name'
    assert pseudonymise._canonical_name_key('Member') == 'first_name'


def test_canonical_name_key_unknown_raises():
    with pytest.raises(ValueError, match='Cannot map column'):
        pseudonymise._canonical_name_key('Notes')


# ============================================================================
# Name validation regex
# ============================================================================


def test_valid_name_accepts_typical_names():
    assert pseudonymise._VALID_NAME_RE.match('Alice')
    assert pseudonymise._VALID_NAME_RE.match('Alice Smith')
    assert pseudonymise._VALID_NAME_RE.match("O'Brien")
    assert pseudonymise._VALID_NAME_RE.match('Mary-Jane')
    assert pseudonymise._VALID_NAME_RE.match('José García')


def test_valid_name_rejects_footer_text():
    assert not pseudonymise._VALID_NAME_RE.match('This list is correct as of 01/01/2024')
    assert not pseudonymise._VALID_NAME_RE.match('Total: 42')
    assert not pseudonymise._VALID_NAME_RE.match('alice@example.com')


def test_valid_name_rejects_digits():
    assert not pseudonymise._VALID_NAME_RE.match('Alice2')


# ============================================================================
# Dancer ID management
# ============================================================================


def test_get_or_create_dancer_id_format(ctx):
    did = pseudonymise.get_or_create_dancer_id(
        ctx,
        {'first_name': 'Alice', 'last_name': 'Smith'},
        {'email': 'alice@example.com'},
    )
    assert did.startswith('DNC-')
    assert len(did) == 12  # 'DNC-' + 8 hex chars


def test_get_or_create_dancer_id_deduplicates_by_email(ctx):
    alice = {'first_name': 'Alice', 'last_name': 'Smith'}
    alicia = {'first_name': 'Alicia', 'last_name': 'Schmidt'}
    email = {'email': 'alice@example.com'}
    id1 = pseudonymise.get_or_create_dancer_id(ctx, alice, email)
    id2 = pseudonymise.get_or_create_dancer_id(ctx, alicia, email)
    assert id1 == id2


def test_get_or_create_dancer_id_deduplicates_by_name(ctx):
    bob = {'first_name': 'Bob', 'last_name': 'Jones'}
    id1 = pseudonymise.get_or_create_dancer_id(ctx, bob, {'email': 'bob1@example.com'})
    id2 = pseudonymise.get_or_create_dancer_id(ctx, bob, {'email': 'bob2@example.com'})
    assert id1 == id2


def test_get_or_create_dancer_id_distinct_people(ctx):
    alice = {'first_name': 'Alice', 'last_name': 'Smith'}
    bob = {'first_name': 'Bob', 'last_name': 'Jones'}
    id1 = pseudonymise.get_or_create_dancer_id(ctx, alice, {'email': 'alice@example.com'})
    id2 = pseudonymise.get_or_create_dancer_id(ctx, bob, {'email': 'bob@example.com'})
    assert id1 != id2


_SELECT_ENC = 'SELECT enc_name, enc_email FROM pseudonyms WHERE dancer_id=?'


def test_get_or_create_dancer_id_name_only(ctx):
    alice = {'first_name': 'Alice', 'last_name': 'Smith'}
    did = pseudonymise.get_or_create_dancer_id(ctx, alice, None)
    assert did.startswith('DNC-')
    enc_name, enc_email = ctx.conn.execute(_SELECT_ENC, (did,)).fetchone()
    assert enc_name is not None
    assert enc_email is None


def test_get_or_create_dancer_id_email_only(ctx):
    did = pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'anon@example.com'})
    assert did.startswith('DNC-')
    enc_name, enc_email = ctx.conn.execute(_SELECT_ENC, (did,)).fetchone()
    assert enc_name is None
    assert enc_email is not None


def test_get_or_create_dancer_id_stores_json(ctx):
    name_fields = {'first_name': 'Alice', 'last_name': 'Smith'}
    email_fields = {'email': 'alice@example.com'}
    did = pseudonymise.get_or_create_dancer_id(ctx, name_fields, email_fields)
    enc_name, enc_email = ctx.conn.execute(_SELECT_ENC, (did,)).fetchone()
    assert json.loads(ctx.fernet.decrypt(enc_name.encode())) == name_fields
    assert json.loads(ctx.fernet.decrypt(enc_email.encode())) == email_fields


def test_get_or_create_updates_missing_name(ctx):
    """If dancer was found by email but had no name, name should be filled in."""
    did = pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'alice@example.com'})
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['name'] == {'first_name': 'Alice', 'last_name': 'Smith'}


def test_get_or_create_updates_missing_email(ctx):
    """If dancer was found by name but had no email, email should be filled in."""
    did = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['email'] == {'email': 'alice@example.com'}


def test_get_or_create_stores_alt_first_name(ctx):
    """Same email, different first name → alt_first_name stored on existing record."""
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Xiaoling', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['name']['first_name'] == 'Alice'
    assert result['name'].get('alt_first_name') == 'Xiaoling'


def test_get_or_create_alt_first_name_not_overwritten(ctx):
    """alt_first_name is only written once; a third name doesn't overwrite it."""
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Xiaoling', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Xiao', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['name']['alt_first_name'] == 'Xiaoling'  # not overwritten by 'Xiao'


def test_get_or_create_stores_alt_last_name(ctx):
    """Same email, different last name (e.g. married) → alt_last_name stored on existing record."""
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Jones'}, {'email': 'alice@example.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['name']['last_name'] == 'Smith'
    assert result['name'].get('alt_last_name') == 'Jones'


def test_get_or_create_alt_last_name_not_overwritten(ctx):
    """alt_last_name is only written once; a third last name doesn't overwrite it."""
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Jones'}, {'email': 'alice@example.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Brown'}, {'email': 'alice@example.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['name']['alt_last_name'] == 'Jones'  # not overwritten by 'Brown'


def test_get_or_create_stores_alt_first_and_last_name_together(ctx):
    """A differing first and last name on the same encounter are both captured."""
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Ali', 'last_name': 'Jones'}, {'email': 'alice@example.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['name']['alt_first_name'] == 'Ali'
    assert result['name']['alt_last_name'] == 'Jones'


def test_get_or_create_stores_alt_email(ctx):
    """Same name, different second email → alt_email stored on existing record."""
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@work.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@home.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['email']['email'] == 'alice@work.com'
    assert result['email'].get('alt_email') == 'alice@home.com'


def test_get_or_create_alt_email_not_overwritten(ctx):
    """alt_email is only written once; a third email doesn't overwrite it."""
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@work.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@home.com'}
    )
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@other.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['email']['alt_email'] == 'alice@home.com'  # not overwritten by 'alice@other.com'


def test_dancer_id_stable_across_fresh_dbs(tmp_path):
    """Same passphrase and input produces the same dancer ID in a freshly created DB."""
    name = {'first_name': 'Alice', 'last_name': 'Smith'}
    email = {'email': 'alice@example.com'}

    ctx1 = pseudonymise.open_db(tmp_path / 'db1.sqlite', PASSPHRASE)
    id1 = pseudonymise.get_or_create_dancer_id(ctx1, name, email)
    ctx1.conn.close()

    ctx2 = pseudonymise.open_db(tmp_path / 'db2.sqlite', PASSPHRASE)
    id2 = pseudonymise.get_or_create_dancer_id(ctx2, name, email)
    ctx2.conn.close()

    assert id1 == id2


def test_dancer_id_differs_with_different_passphrase(tmp_path):
    """Different passphrases produce different dancer IDs for the same input."""
    name = {'first_name': 'Alice', 'last_name': 'Smith'}
    email = {'email': 'alice@example.com'}

    ctx1 = pseudonymise.open_db(tmp_path / 'db1.sqlite', PASSPHRASE)
    id1 = pseudonymise.get_or_create_dancer_id(ctx1, name, email)
    ctx1.conn.close()

    ctx2 = pseudonymise.open_db(tmp_path / 'db2.sqlite', 'different-passphrase')
    id2 = pseudonymise.get_or_create_dancer_id(ctx2, name, email)
    ctx2.conn.close()

    assert id1 != id2


# ============================================================================
# Decryption
# ============================================================================


def test_decrypt_all_returns_canonical_dicts(ctx):
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, {'email': 'bob@example.com'})
    results = pseudonymise.decrypt_all(ctx)
    assert len(results) == 2
    names = {r['name']['first_name'] for r in results}
    assert names == {'Alice', 'Bob'}
    for r in results:
        assert 'dancer_id' in r
        assert 'last_name' in r['name']
        assert 'email' in r['email']


def test_decrypt_dancer_found(ctx):
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['dancer_id'] == did
    assert result['name'] == {'first_name': 'Alice', 'last_name': 'Smith'}
    assert result['email'] == {'email': 'alice@example.com'}


def test_decrypt_dancer_not_found(ctx):
    assert pseudonymise.decrypt_dancer(ctx, 'DNC-NONEXIST') is None


def test_decrypt_dancer_null_email(ctx):
    did = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    result = pseudonymise.decrypt_dancer(ctx, did)
    assert result['email'] is None
    assert result['name']['first_name'] == 'Alice'


# ============================================================================
# XLSX sheet reading
# ============================================================================


def test_read_sheet_simple(simple_xlsx):
    wb = openpyxl.load_workbook(simple_xlsx)
    fieldnames, rows, prefix = pseudonymise._read_sheet(wb.active)
    assert fieldnames == ['First Name', 'Last Name', 'Email', 'Notes']
    assert len(rows) == 2
    assert rows[0]['First Name'] == 'Alice'
    assert rows[1]['Last Name'] == 'Jones'
    assert prefix == []


def test_read_sheet_detects_prefix(prefixed_xlsx):
    wb = openpyxl.load_workbook(prefixed_xlsx)
    fieldnames, rows, prefix = pseudonymise._read_sheet(wb.active)
    assert fieldnames == ['Forename', 'Surname', 'E-mail', 'Attended']
    assert len(rows) == 1
    assert len(prefix) == 2
    assert prefix[0][0] == 'Class Register'


def test_read_sheet_skips_empty_rows(tmp_path):
    p = tmp_path / 'sparse.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['First Name', 'Last Name'])
    ws.append(['Alice', 'Smith'])
    ws.append([None, None])
    ws.append(['Bob', 'Jones'])
    wb.save(p)
    wb2 = openpyxl.load_workbook(p)
    _, rows, _ = pseudonymise._read_sheet(wb2.active)
    assert len(rows) == 2


def test_read_sheet_pads_short_rows(tmp_path):
    p = tmp_path / 'short.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['First Name', 'Last Name', 'Email'])
    ws.append(['Alice', 'Smith'])  # missing email cell
    wb.save(p)
    wb2 = openpyxl.load_workbook(p)
    _, rows, _ = pseudonymise._read_sheet(wb2.active)
    assert rows[0]['Email'] == ''


def test_read_sheet_note_row_not_treated_as_header(tmp_path):
    """A row containing 'First Name' as a substring should not be picked as header."""
    p = tmp_path / 'note.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['NB: order A_Z by First Name'])
    ws.append(['Forename', 'Surname'])
    ws.append(['Alice', 'Smith'])
    wb.save(p)
    wb2 = openpyxl.load_workbook(p)
    fieldnames, rows, prefix = pseudonymise._read_sheet(wb2.active)
    assert fieldnames == ['Forename', 'Surname']
    assert len(rows) == 1
    assert len(prefix) == 1


# ============================================================================
# XLSX sheet writing
# ============================================================================


def test_write_sheet_round_trip(tmp_path, simple_xlsx):
    wb = openpyxl.load_workbook(simple_xlsx)
    fieldnames, rows, prefix = pseudonymise._read_sheet(wb.active)
    wb_out = openpyxl.Workbook()
    pseudonymise._write_sheet(wb_out.active, fieldnames, rows, fieldnames, prefix)
    out = tmp_path / 'out.xlsx'
    wb_out.save(out)
    wb_check = openpyxl.load_workbook(out)
    data = list(wb_check.active.iter_rows(values_only=True))
    assert list(data[0]) == fieldnames
    assert data[1][0] == 'Alice'


def test_write_sheet_preserves_prefix(tmp_path, prefixed_xlsx):
    wb = openpyxl.load_workbook(prefixed_xlsx)
    fieldnames, rows, prefix = pseudonymise._read_sheet(wb.active)
    wb_out = openpyxl.Workbook()
    pseudonymise._write_sheet(wb_out.active, fieldnames, rows, fieldnames, prefix)
    out = tmp_path / 'out.xlsx'
    wb_out.save(out)
    wb_check = openpyxl.load_workbook(out)
    data = list(wb_check.active.iter_rows(values_only=True))
    assert data[0][0] == 'Class Register'
    assert list(data[2]) == fieldnames


def test_write_sheet_renamed_headers(tmp_path, simple_xlsx):
    wb = openpyxl.load_workbook(simple_xlsx)
    fieldnames, rows, prefix = pseudonymise._read_sheet(wb.active)
    out_fieldnames = ['dancer_id', 'redacted', 'redacted', 'Notes']
    wb_out = openpyxl.Workbook()
    pseudonymise._write_sheet(wb_out.active, out_fieldnames, rows, fieldnames, prefix)
    out = tmp_path / 'out.xlsx'
    wb_out.save(out)
    wb_check = openpyxl.load_workbook(out)
    header = [c.value for c in next(wb_check.active.iter_rows())]
    assert header == out_fieldnames


# ============================================================================
# Column detection
# ============================================================================


def test_detect_columns_by_header(simple_xlsx):
    wb = openpyxl.load_workbook(simple_xlsx)
    fieldnames, rows, _ = pseudonymise._read_sheet(wb.active)
    detected = pseudonymise.detect_columns(fieldnames, rows)
    assert 'First Name' in detected['name_cols']
    assert 'Last Name' in detected['name_cols']
    assert 'Email' in detected['email_cols']
    assert 'Notes' not in detected['name_cols']
    assert 'Notes' not in detected['email_cols']


def test_detect_columns_email_by_content(tmp_path):
    p = tmp_path / 'sniff.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['First Name', 'Last Name', 'Contact'])
    ws.append(['Alice', 'Smith', 'alice@example.com'])
    ws.append(['Bob', 'Jones', 'bob@example.com'])
    wb.save(p)
    wb2 = openpyxl.load_workbook(p)
    fieldnames, rows, _ = pseudonymise._read_sheet(wb2.active)
    detected = pseudonymise.detect_columns(fieldnames, rows)
    assert 'Contact' in detected['email_cols']


def test_detect_columns_member_exact_only(tmp_path):
    """'Member' alone is a valid name column; adjacent words disqualify it."""
    p = tmp_path / 't.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Member', 'Concession / Member?', 'Paid as member', 'Paid as non-member', 'Member 2022'])
    ws.append(['Alice Smith', 'No', 'Yes', 'No', 'Yes'])
    wb.save(p)
    wb2 = openpyxl.load_workbook(p)
    fieldnames, rows, _ = pseudonymise._read_sheet(wb2.active)
    detected = pseudonymise.detect_columns(fieldnames, rows)
    assert detected['name_cols'] == ['Member']


def test_detect_columns_email_false_positives(tmp_path):
    """Columns where 'email' is a modifier word, not the column type."""
    p = tmp_path / 't.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Email', 'Email subscriber status', 'email present in Wix Export?'])
    ws.append(['a@b.com', 'subscribed', 'Yes'])
    wb.save(p)
    wb2 = openpyxl.load_workbook(p)
    fieldnames, rows, _ = pseudonymise._read_sheet(wb2.active)
    detected = pseudonymise.detect_columns(fieldnames, rows)
    assert detected['email_cols'] == ['Email']


def test_detect_columns_content_sniff_max_5_rows(tmp_path):
    """Emails appearing only after row 5 should not trigger content detection."""
    p = tmp_path / 'late.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['First Name', 'Last Name', 'Contact'])
    for _ in range(5):
        ws.append(['Alice', 'Smith', 'not-an-email'])
    ws.append(['Bob', 'Jones', 'bob@example.com'])
    wb.save(p)
    wb2 = openpyxl.load_workbook(p)
    fieldnames, rows, _ = pseudonymise._read_sheet(wb2.active)
    detected = pseudonymise.detect_columns(fieldnames, rows)
    assert 'Contact' not in detected['email_cols']


# ============================================================================
# Full pipeline — single file
# ============================================================================


def test_pseudonymise_replaces_pii(simple_xlsx, tmp_path):
    out = tmp_path / 'out.xlsx'
    result = pseudonymise.pseudonymise(simple_xlsx, tmp_path / 'db.sqlite', PASSPHRASE, output_path=out)
    rows = result['Sheet1']
    assert rows[0]['First Name'].startswith('DNC-')
    assert rows[0]['Last Name'] == 'redacted'
    assert rows[0]['Email'] == 'redacted'
    assert rows[0]['Notes'] == 'Regular'


def test_pseudonymise_writes_output_file(simple_xlsx, tmp_path):
    out = tmp_path / 'out.xlsx'
    pseudonymise.pseudonymise(simple_xlsx, tmp_path / 'db.sqlite', PASSPHRASE, output_path=out)
    assert out.exists()
    wb = openpyxl.load_workbook(out)
    header = [c.value for c in next(wb.active.iter_rows())]
    assert header[0] == 'dancer_id'


def test_pseudonymise_auto_output_name(simple_xlsx, tmp_path):
    pseudonymise.pseudonymise(simple_xlsx, tmp_path / 'db.sqlite', PASSPHRASE)
    assert (simple_xlsx.parent / 'simple_pseudonymised.xlsx').exists()


def test_pseudonymise_preserves_prefix(prefixed_xlsx, tmp_path):
    out = tmp_path / 'out.xlsx'
    pseudonymise.pseudonymise(prefixed_xlsx, tmp_path / 'db.sqlite', PASSPHRASE, output_path=out)
    wb = openpyxl.load_workbook(out)
    data = list(wb.active.iter_rows(values_only=True))
    assert data[0][0] == 'Class Register'
    assert data[1][0] == 'Level 1'


def test_pseudonymise_stable_ids_across_runs(simple_xlsx, tmp_path):
    db = tmp_path / 'db.sqlite'
    result1 = pseudonymise.pseudonymise(simple_xlsx, db, PASSPHRASE, output_path=tmp_path / 'out1.xlsx')
    result2 = pseudonymise.pseudonymise(simple_xlsx, db, PASSPHRASE, output_path=tmp_path / 'out2.xlsx')
    assert result1['Sheet1'][0]['First Name'] == result2['Sheet1'][0]['First Name']


def test_pseudonymise_skips_footer_text(tmp_path):
    p = tmp_path / 'footer.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(['First Name', 'Last Name', 'Email'])
    ws.append(['Alice', 'Smith', 'alice@example.com'])
    ws.append(['This list is correct as of 01/01/2024', '', ''])
    wb.save(p)
    db = tmp_path / 'db.sqlite'
    result = pseudonymise.pseudonymise(p, db, PASSPHRASE, output_path=tmp_path / 'out.xlsx')
    rows = result['Sheet1']
    assert rows[0]['First Name'].startswith('DNC-')
    assert rows[1]['First Name'] == 'This list is correct as of 01/01/2024'
    c = pseudonymise.open_db(db, PASSPHRASE)
    assert len(pseudonymise.decrypt_all(c)) == 1
    c.conn.close()


def test_pseudonymise_manual_column_override(tmp_path):
    p = tmp_path / 'nonstandard.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(['Forename', 'Surname', 'Contact', 'Notes'])
    ws.append(['Alice', 'Smith', 'alice@example.com', 'Active'])
    wb.save(p)
    result = pseudonymise.pseudonymise(
        p,
        tmp_path / 'db.sqlite',
        PASSPHRASE,
        output_path=tmp_path / 'out.xlsx',
        name_cols=['Forename', 'Surname'],
        email_cols=['Contact'],
    )
    rows = result['Sheet1']
    assert rows[0]['Forename'].startswith('DNC-')
    assert rows[0]['Surname'] == 'redacted'
    assert rows[0]['Contact'] == 'redacted'


def test_pseudonymise_multi_sheet(tmp_path):
    p = tmp_path / 'multi.xlsx'
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = 'Jan'
    ws1.append(['First Name', 'Last Name'])
    ws1.append(['Alice', 'Smith'])
    ws2 = wb.create_sheet('Feb')
    ws2.append(['First Name', 'Last Name'])
    ws2.append(['Bob', 'Jones'])
    wb.save(p)
    result = pseudonymise.pseudonymise(p, tmp_path / 'db.sqlite', PASSPHRASE, output_path=tmp_path / 'out.xlsx')
    assert 'Jan' in result
    assert 'Feb' in result
    assert result['Jan'][0]['First Name'].startswith('DNC-')
    assert result['Feb'][0]['First Name'].startswith('DNC-')


def test_pseudonymise_copies_non_pii_sheet_verbatim(tmp_path):
    p = tmp_path / 'tally.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Tally'
    ws.append(['Week 1', 'Members', 'Tot:', 19])
    ws.append([None, True, True, False])
    ws.append([None, False, True, True])
    wb.save(p)

    """A sheet with no name/email columns (e.g. an aggregate tally grid) must be
    copied through cell-for-cell, not rebuilt — rebuilding collapsed such sheets.
    """

    out = tmp_path / 'out.xlsx'
    result = pseudonymise.pseudonymise(p, tmp_path / 'db.sqlite', PASSPHRASE, output_path=out)
    assert result['Tally'] == []  # nothing redacted

    data = list(openpyxl.load_workbook(out)['Tally'].iter_rows(values_only=True))
    assert data[0] == ('Week 1', 'Members', 'Tot:', 19)
    assert data[1] == (None, True, True, False)
    assert data[2] == (None, False, True, True)


def test_pseudonymise_mixed_pii_and_tally_sheets(tmp_path):
    p = tmp_path / 'mixed.xlsx'
    wb = openpyxl.Workbook()
    roster = wb.active
    roster.title = 'Roster'
    roster.append(['First Name', 'Last Name'])
    roster.append(['Alice', 'Smith'])
    tally = wb.create_sheet('Tally')
    tally.append(['Members', 'Tot:', 7])
    tally.append([True, True, False])
    wb.save(p)

    """A roster sheet is pseudonymised while a tally sheet in the same workbook
    survives intact.
    """

    out = tmp_path / 'out.xlsx'
    result = pseudonymise.pseudonymise(p, tmp_path / 'db.sqlite', PASSPHRASE, output_path=out)
    assert result['Roster'][0]['First Name'].startswith('DNC-')
    assert result['Tally'] == []

    wb_out = openpyxl.load_workbook(out)
    assert list(wb_out['Tally'].iter_rows(values_only=True)) == [('Members', 'Tot:', 7), (True, True, False)]


# ============================================================================
# Folder processing
# ============================================================================


def test_pseudonymise_folder_basic(tmp_path, simple_xlsx):
    import shutil

    input_dir = tmp_path / 'in'
    input_dir.mkdir()
    shutil.copy(simple_xlsx, input_dir / 'file.xlsx')
    output_dir = tmp_path / 'out'
    pseudonymise.pseudonymise_folder(input_dir, output_dir, tmp_path / 'db.sqlite', PASSPHRASE)
    assert (output_dir / 'file_pseudonymised.xlsx').exists()


def test_pseudonymise_folder_preserves_subdirs(tmp_path, simple_xlsx):
    import shutil

    input_dir = tmp_path / 'in'
    sub = input_dir / 'sub'
    sub.mkdir(parents=True)
    shutil.copy(simple_xlsx, input_dir / 'top.xlsx')
    shutil.copy(simple_xlsx, sub / 'nested.xlsx')
    output_dir = tmp_path / 'out'
    pseudonymise.pseudonymise_folder(input_dir, output_dir, tmp_path / 'db.sqlite', PASSPHRASE)
    assert (output_dir / 'top_pseudonymised.xlsx').exists()
    assert (output_dir / 'sub' / 'nested_pseudonymised.xlsx').exists()


def test_pseudonymise_folder_shared_db(tmp_path, simple_xlsx):
    """Same person appearing in two files gets the same dancer ID."""
    import shutil

    input_dir = tmp_path / 'in'
    input_dir.mkdir()
    shutil.copy(simple_xlsx, input_dir / 'a.xlsx')
    shutil.copy(simple_xlsx, input_dir / 'b.xlsx')
    db = tmp_path / 'db.sqlite'
    output_dir = tmp_path / 'out'
    pseudonymise.pseudonymise_folder(input_dir, output_dir, db, PASSPHRASE)
    # Only the unique people from simple_xlsx (Alice, Bob) should be in the DB.
    c = pseudonymise.open_db(db, PASSPHRASE)
    assert len(pseudonymise.decrypt_all(c)) == 2
    c.conn.close()


# ============================================================================
# Dancer ID substitution
# ============================================================================


def test_substitute_removes_old_id(ctx):
    id1 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    id2 = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alicia', 'last_name': 'Smith'}, {'email': 'alice@example.com'}
    )
    pseudonymise.substitute_dancer_id(ctx, id1, id2)
    assert pseudonymise.decrypt_dancer(ctx, id1) is None


def test_substitute_merges_name_into_new(ctx):
    """old_id has name, new_id has email only → name moves to new_id."""
    id1 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    id2 = pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'alice@example.com'})
    pseudonymise.substitute_dancer_id(ctx, id1, id2)
    result = pseudonymise.decrypt_dancer(ctx, id2)
    assert result['name'] == {'first_name': 'Alice', 'last_name': 'Smith'}
    assert result['email'] == {'email': 'alice@example.com'}


def test_substitute_does_not_overwrite_existing_fields(ctx):
    """new_id already has a name — old_id's name should not overwrite it."""
    id1 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'OldName', 'last_name': 'X'}, None)
    id2 = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'NewName', 'last_name': 'Y'}, {'email': 'x@example.com'}
    )
    pseudonymise.substitute_dancer_id(ctx, id1, id2)
    result = pseudonymise.decrypt_dancer(ctx, id2)
    assert result['name']['first_name'] == 'NewName'


def test_substitute_stores_alt_first_name_when_explicitly_passed(ctx):
    """conflict_first_name is stored as alt_first_name on new_id when provided."""
    id1 = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Mei', 'last_name': 'Chen'}, {'email': 'mei.chen@example.com'}
    )
    id2 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Mary', 'last_name': 'Chen'}, None)
    pseudonymise.substitute_dancer_id(ctx, id1, id2, conflict_first_name='Mei')
    result = pseudonymise.decrypt_dancer(ctx, id2)
    assert result['name']['first_name'] == 'Mary'
    assert result['name']['alt_first_name'] == 'Mei'


def test_substitute_discards_conflict_first_name_by_default(ctx):
    """Without conflict_first_name, differing first names are silently dropped."""
    id1 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Mei', 'last_name': 'Chen'}, None)
    id2 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Mary', 'last_name': 'Chen'}, None)
    pseudonymise.substitute_dancer_id(ctx, id1, id2)
    result = pseudonymise.decrypt_dancer(ctx, id2)
    assert result['name']['first_name'] == 'Mary'
    assert 'alt_first_name' not in result['name']


def test_substitute_stores_alt_last_name_when_explicitly_passed(ctx):
    """conflict_last_name is stored as alt_last_name on new_id when provided."""
    id1 = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Mei', 'last_name': 'Smith'}, {'email': 'mei.smith@example.com'}
    )
    id2 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Mei', 'last_name': 'Jones'}, None)
    pseudonymise.substitute_dancer_id(ctx, id1, id2, conflict_last_name='Smith')
    result = pseudonymise.decrypt_dancer(ctx, id2)
    assert result['name']['last_name'] == 'Jones'
    assert result['name']['alt_last_name'] == 'Smith'


def test_substitute_discards_conflict_last_name_by_default(ctx):
    """Without conflict_last_name, differing last names are silently dropped."""
    id1 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Mei', 'last_name': 'Smith'}, None)
    id2 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Mei', 'last_name': 'Jones'}, None)
    pseudonymise.substitute_dancer_id(ctx, id1, id2)
    result = pseudonymise.decrypt_dancer(ctx, id2)
    assert result['name']['last_name'] == 'Jones'
    assert 'alt_last_name' not in result['name']


def test_substitute_stores_alt_email_when_explicitly_passed(ctx):
    """conflict_email is stored as alt_email on new_id when provided."""
    id1 = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@work.com'}
    )
    id2 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alicia', 'last_name': 'Smith'}, None)
    pseudonymise.substitute_dancer_id(ctx, id2, id1, conflict_email='alice@personal.com')
    result = pseudonymise.decrypt_dancer(ctx, id1)
    assert result['email']['email'] == 'alice@work.com'
    assert result['email']['alt_email'] == 'alice@personal.com'


def test_substitute_discards_conflict_email_by_default(ctx):
    """Without conflict_email, no alt_email is written to the surviving record."""
    id1 = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@work.com'}
    )
    id2 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alicia', 'last_name': 'Smith'}, None)
    pseudonymise.substitute_dancer_id(ctx, id2, id1)
    result = pseudonymise.decrypt_dancer(ctx, id1)
    assert 'alt_email' not in result['email']


def test_substitute_raises_for_missing_id(ctx):
    id1 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    with pytest.raises(ValueError):
        pseudonymise.substitute_dancer_id(ctx, id1, 'DNC-NOTEXIST')


def test_substitute_rewrites_xlsx_files(tmp_path, ctx):
    id1 = pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    id2 = pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'alice@example.com'})

    # Write an xlsx that contains id1.
    p = tmp_path / 'out.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['dancer_id', 'Notes'])
    ws.append([id1, 'something'])
    wb.save(p)

    pseudonymise.substitute_dancer_id(ctx, id1, id2, output_dir=tmp_path)

    wb2 = openpyxl.load_workbook(p)
    values = [row[0].value for row in wb2.active.iter_rows()]
    assert id2 in values
    assert id1 not in values


# ============================================================================
# Fuzzy duplicate detection
# ============================================================================


def test_find_duplicate_candidates_finds_similar_names(ctx):
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smyth'}, None)
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    candidates = pseudonymise.find_duplicate_candidates(ctx, threshold=0.7)
    last_names = {(c[0]['name']['last_name'], c[1]['name']['last_name']) for c in candidates}
    assert ('Smith', 'Smyth') in last_names or ('Smyth', 'Smith') in last_names


def test_find_duplicate_candidates_sorted_by_score(ctx):
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smyth'}, None)
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Ali', 'last_name': 'Smit'}, None)
    candidates = pseudonymise.find_duplicate_candidates(ctx, threshold=0.5)
    scores = [c[2] for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_find_duplicate_candidates_finds_similar_emails(ctx):
    pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'alice.smith@example.com'})
    pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'alice.smyth@example.com'})
    candidates = pseudonymise.find_duplicate_candidates(ctx, threshold=0.8)
    assert len(candidates) == 1


def test_find_duplicate_candidates_no_false_positives(ctx):
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    candidates = pseudonymise.find_duplicate_candidates(ctx, threshold=0.9)
    assert len(candidates) == 0


def test_find_duplicate_candidates_name_vs_email_local(ctx):
    # One record has only a name; the other has only an email whose local part encodes the same name.
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Chris', 'last_name': 'Leeson'}, None)
    pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'chris.leeson@example.com'})
    candidates = pseudonymise.find_duplicate_candidates(ctx, threshold=0.8)
    assert len(candidates) == 1


def test_find_duplicate_candidates_considers_alt_email(ctx):
    """A match on alt_email should surface as a duplicate candidate."""
    id1 = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Dave', 'last_name': 'Brown'}, {'email': 'dave@work.com'}
    )
    # Manually store alt_email via a second encounter.
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Dave', 'last_name': 'Brown'}, {'email': 'dave@home.com'})
    # A second record whose primary email matches the alt_email of id1.
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'David', 'last_name': 'Browne'}, {'email': 'dave@home.com'}
    )
    # id1 now has alt_email=dave@home.com; the third record has email=dave@home.com — should score high.
    candidates = pseudonymise.find_duplicate_candidates(ctx, threshold=0.8)
    ids_in_candidates = {c[0]['dancer_id'] for c in candidates} | {c[1]['dancer_id'] for c in candidates}
    assert id1 in ids_in_candidates


# ============================================================================
# Dancer search
# ============================================================================


def test_search_dancer_finds_by_name(ctx):
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    results = pseudonymise.search_dancer(ctx, 'alice smith', threshold=0.9)
    assert len(results) == 1
    assert results[0][0]['name']['first_name'] == 'Alice'


def test_search_dancer_partial_query_scores_high(ctx):
    """A prefix query should score ~1.0 via partial ratio, not be penalised for length."""
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    results = pseudonymise.search_dancer(ctx, 'alice', threshold=0.9)
    assert len(results) == 1
    assert results[0][1] >= 0.9


def test_search_dancer_finds_by_email_local(ctx):
    pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'alice.smith@example.com'})
    pseudonymise.get_or_create_dancer_id(ctx, None, {'email': 'bob.jones@example.com'})
    results = pseudonymise.search_dancer(ctx, 'alice smith', threshold=0.9)
    assert len(results) == 1
    assert results[0][0]['email']['email'] == 'alice.smith@example.com'


def test_search_dancer_sorted_by_score(ctx):
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Ali', 'last_name': 'Smit'}, None)
    results = pseudonymise.search_dancer(ctx, 'alice smith', threshold=0.5)
    assert len(results) == 2
    assert results[0][1] >= results[1][1]


def test_search_dancer_max_results(ctx):
    for i in range(5):
        pseudonymise.get_or_create_dancer_id(ctx, {'first_name': f'Alice{i}', 'last_name': 'Smith'}, None)
    results = pseudonymise.search_dancer(ctx, 'alice smith', threshold=0.5, max_results=3)
    assert len(results) <= 3


def test_search_dancer_no_results(ctx):
    pseudonymise.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    results = pseudonymise.search_dancer(ctx, 'zzz', threshold=0.9)
    assert results == []


def test_search_dancer_finds_by_alt_email(ctx):
    did = pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@work.com'}
    )
    # Trigger alt_email storage.
    pseudonymise.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@home.com'}
    )
    results = pseudonymise.search_dancer(ctx, 'alice@home.com', threshold=0.9)
    assert len(results) == 1
    assert results[0][0]['dancer_id'] == did
