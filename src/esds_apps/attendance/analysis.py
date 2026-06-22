"""Read-only analytics over the deployed attendance database for the /attendance summary charts.

The notebook (``working/attendance.ipynb``) is the design surface for these charts; this module
is the production port of three of them:

* **beginner intake** — mean Level 1 attendance per term, one series per academic year;
* **Level 2 and its paired social** — mean Level 2 attendance per term with the post-class
  social-only turnout overlaid, one series per academic year;
* **cohort retention** — the share of each joining cohort still active a given number of terms
  later, plus the teaching team that ran each joining term.

The term-bucketing and team derivation are lifted near-verbatim from the notebook (see
``_term_calendar``) deliberately: terms are not a stored column, the logic is intricate, and
keeping one implementation means the web charts can't silently drift from the validated
notebook ones. Everything here is read-only — only SELECTs run — so it never disturbs the
database the notebook rebuilds.
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from esds_apps import config

# A substantial Level 1 block (>= this many weekly lessons) anchors a term; shorter L1
# appearances (one-offs, Stockbridge) never define one.
_MIN_L1_LESSONS_FOR_TERM = 4
# Two L1 block starts within this many days are the same term (a twice-weekly strand or a
# paired social mustn't split a term).
_TERM_MERGE_DAYS = 21
# Charts that lean on confirmed turnout start here: 2021/22 and 2022/23 logged mostly
# enrolment, so including them would read misleadingly (see the notebook's per-chart notes).
_FIRST_TURNOUT_ACAD_YEAR = 2023
# The paired post-class socials only begin in autumn 2024; earlier social points are artefacts.
_FIRST_PAIRED_SOCIAL_ACAD_YEAR = 2024
# Academic years run September -> August, so a date in this month or later belongs to the year
# that's just starting (Sept 2024 -> the 2024/25 year; Jan 2025 -> still 2024/25).
_ACADEMIC_YEAR_START_MONTH = 8
# The community chart looks at 2026 only: that is when we began recording Level 2 attendees by
# name (January 2026), so earlier years carry no improvers' names and would undercount the core.
_FIRST_L2_NAMES_DATE = '2026-01-01'


def _acad_year_label(acad_year: int) -> str:
    """'2023' -> '23/24'."""
    return f'{str(acad_year)[2:]}/{str(acad_year + 1)[2:]}'


def _term_calendar(conn: sqlite3.Connection):
    """Derive the teaching-term calendar and an ``assign`` function, as the notebook does.

    Returns ``(terms, assign)`` where ``terms`` is a DataFrame indexed implicitly by row order
    (one row per term, with ``term_idx``, ``acad_year``, ``term_num``, ``label`` and the
    teaching-team columns) and ``assign`` maps datetime-like values to their ``term_idx``.
    """
    l1 = pd.read_sql_query(
        'SELECT MIN(a.date) AS start, GROUP_CONCAT(DISTINCT et.dancer_id) AS teachers '
        'FROM event e JOIN activity a USING(event_id) '
        'LEFT JOIN event_teacher et ON et.event_id = e.event_id '
        "WHERE e.event_type = 'course' AND a.difficulty = 'Level 1' "
        'GROUP BY e.event_id HAVING COUNT(a.activity_id) >= ? ORDER BY start',
        conn,
        params=(_MIN_L1_LESSONS_FOR_TERM,),
        parse_dates=['start'],
    )

    anchors: list[pd.Timestamp] = []
    for d in sorted(l1['start']):
        if not anchors or (d - anchors[-1]).days > _TERM_MERGE_DAYS:
            anchors.append(d)

    terms = pd.DataFrame({'term_idx': range(len(anchors)), 'term_start': pd.to_datetime(anchors)})
    terms['acad_year'] = terms['term_start'].apply(
        lambda d: d.year if d.month >= _ACADEMIC_YEAR_START_MONTH else d.year - 1
    )
    terms['term_num'] = terms.groupby('acad_year').cumcount() + 1
    terms['label'] = terms.apply(lambda r: f'{_acad_year_label(r.acad_year)} T{r.term_num}', axis=1)

    term_starts = terms['term_start'].to_numpy()

    def assign(dates):
        """Map datetime-like values to their term_idx via the calendar (NaN before the first term)."""
        d = pd.to_datetime(dates)
        if not isinstance(d, pd.Series):
            d = pd.Series(d)
        idx = np.searchsorted(term_starts, d.to_numpy(), side='right') - 1
        return pd.Series(idx, index=d.index).where(idx >= 0).astype('Int64')

    # Teaching team per term: the union of Level 1 teachers on the courses that fall in it.
    l1['term_idx'] = assign(l1['start'])
    team = (
        l1.dropna(subset=['teachers'])
        .groupby('term_idx')['teachers']
        .apply(lambda s: frozenset(t for v in s for t in v.split(',')))
    )
    terms['teacher_set'] = terms['term_idx'].map(team).apply(lambda s: s if isinstance(s, frozenset) else frozenset())

    order: list[frozenset] = []
    for s in terms['teacher_set']:
        if s and s not in order:
            order.append(s)
    team_id = {s: i for i, s in enumerate(order)}  # stable int id by first appearance
    terms['teacher_id'] = terms['teacher_set'].apply(lambda s: team_id.get(s, -1))
    terms['teacher_label'] = terms['teacher_set'].apply(
        lambda s: '+'.join(sorted(d.replace('DNC-', '') for d in s)) if s else 'unknown'
    )
    return terms, assign


def _beginner_intake(conn: sqlite3.Connection, terms: pd.DataFrame, assign) -> list[dict]:
    """Plot 2: mean Level 1 attendance and registrations per term, grouped by academic year."""
    l1 = pd.read_sql_query(
        "SELECT date, total, named_registered FROM activity_attendance WHERE difficulty = 'Level 1'",
        conn,
        parse_dates=['date'],
    )
    l1['term_idx'] = assign(l1['date'])
    per_term = l1.groupby('term_idx')[['total', 'named_registered']].mean()
    m = terms.set_index('term_idx').join(per_term).dropna(subset=['total'])
    m = m[m['acad_year'] >= _FIRST_TURNOUT_ACAD_YEAR]

    out = []
    for ay in sorted(m['acad_year'].unique()):
        g = m[m['acad_year'] == ay].sort_values('term_num')
        out.append(
            {
                'acad_year': int(ay),
                'label': _acad_year_label(int(ay)),
                'points': [
                    {'term_num': int(tn), 'attended': float(a), 'registered': float(r)}
                    for tn, a, r in zip(g['term_num'], g['total'], g['named_registered'])
                ],
            }
        )
    return out


def _level2_and_socials(conn: sqlite3.Connection, terms: pd.DataFrame, assign) -> list[dict]:
    """Plot 3: mean Level 2 attendance per term with the paired social-only turnout, by year."""
    l2 = pd.read_sql_query(
        "SELECT date, total FROM activity_attendance WHERE difficulty = 'Level 2'", conn, parse_dates=['date']
    )
    soc = pd.read_sql_query(
        "SELECT date, total FROM activity_attendance WHERE activity_type = 'social' AND event_type = 'course'",
        conn,
        parse_dates=['date'],
    )

    def per_term_mean(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['term_idx'] = assign(df['date'])
        m = (
            terms.set_index('term_idx')
            .join(df.groupby('term_idx')['total'].mean().rename('mean'))
            .dropna(subset=['mean'])
        )
        return m[m['acad_year'] >= _FIRST_TURNOUT_ACAD_YEAR]

    m_l2 = per_term_mean(l2)
    m_soc = per_term_mean(soc)
    m_soc = m_soc[m_soc['acad_year'] >= _FIRST_PAIRED_SOCIAL_ACAD_YEAR]

    out = []
    for ay in sorted(m_l2['acad_year'].unique()):
        g = m_l2[m_l2['acad_year'] == ay].sort_values('term_num')
        gs = m_soc[m_soc['acad_year'] == ay].sort_values('term_num')
        out.append(
            {
                'acad_year': int(ay),
                'label': _acad_year_label(int(ay)),
                'class_points': [{'term_num': int(t), 'value': float(v)} for t, v in zip(g['term_num'], g['mean'])],
                'social_points': [{'term_num': int(t), 'value': float(v)} for t, v in zip(gs['term_num'], gs['mean'])],
            }
        )
    return out


def _cohort_retention(conn: sqlite3.Connection, terms: pd.DataFrame, assign) -> dict:
    """Plot 5: % of each joining cohort still present a given number of terms later, plus teams.

    Presence (status 'attended' OR 'unknown') rather than confirmed attendance, because turnout
    was only consistently recorded from ~2024 and an attended-only rule would make every
    pre-2024 cohort look like it churned instantly.
    """
    pres = pd.read_sql_query(
        'SELECT at.dancer_id, a.date FROM attendance at JOIN activity a USING(activity_id) '
        "WHERE at.status IN ('attended', 'unknown')",
        conn,
        parse_dates=['date'],
    )
    pres['term_idx'] = assign(pres['date'])
    pres = pres.dropna(subset=['term_idx'])
    pres['term_idx'] = pres['term_idx'].astype(int)

    terms_active = pres.groupby('dancer_id')['term_idx'].apply(set)
    cohort = pres.groupby('dancer_id')['term_idx'].min()

    n = len(terms)
    counts = np.zeros((n, n))  # rows = cohort term, cols = terms since joining
    totals = np.zeros(n)
    for dancer_id, joined in cohort.items():
        totals[joined] += 1
        for t in terms_active[dancer_id]:
            if t >= joined:
                counts[joined, t - joined] += 1
    with np.errstate(invalid='ignore'):
        pct = np.where(totals[:, None] > 0, counts / totals[:, None] * 100, np.nan)

    # Blank the impossible lower-right triangle (cohort i can't have data more than n-1-i terms
    # out) and empty cohorts, so the chart shows gaps there rather than a misleading zero.
    matrix = [
        [round(float(pct[i, j]), 1) if (totals[i] > 0 and j <= n - 1 - i) else None for j in range(n)] for i in range(n)
    ]

    term_meta = [
        {
            'idx': int(r.term_idx),
            'label': r.label,
            'total': int(totals[r.term_idx]),
            'teacher_id': int(r.teacher_id),
            'teacher_label': r.teacher_label,
        }
        for r in terms.itertuples()
    ]
    teams: list[dict] = []
    seen: set[int] = set()
    for r in terms.itertuples():
        if r.teacher_id not in seen:
            seen.add(r.teacher_id)
            teams.append({'id': int(r.teacher_id), 'label': r.teacher_label, 'ids': sorted(r.teacher_set)})
    teams.sort(key=lambda t: t['id'])

    teacher_enc = _enc_names(conn, sorted({i for t in teams for i in t['ids']}))

    return {'terms': term_meta, 'matrix': matrix, 'teams': teams, 'teacher_enc': teacher_enc}


def _community_frame(conn: sqlite3.Connection) -> pd.DataFrame:
    """Named confirmed attendances since the Level 2 names began, one row per (dancer, date).

    Flagged with ``is_30th`` so the 30th anniversary weekender can be filtered out. Shared by the
    chart dataset and the CSV download so the two can't disagree about who is counted.
    """
    return pd.read_sql_query(
        "SELECT DISTINCT at.dancer_id, a.date, (e.name LIKE '%30 Years%') AS is_30th "
        'FROM attendance at JOIN activity a USING(activity_id) JOIN event e USING(event_id) '
        "WHERE at.status = 'attended' AND a.date >= ?",
        conn,
        params=(_FIRST_L2_NAMES_DATE,),
        parse_dates=['date'],
    )


def _community_2026(conn: sqlite3.Connection) -> dict:
    """Plot 7: the 2026 survival curve — dancers attending at least each share of the calendar.

    For each series (with and without the 30th anniversary) one point per distinct attendance
    count: how many dancers reached that many unique dates, and that count as a share of the full
    2026 calendar. The denominator is the full calendar for both series so the two are directly
    comparable. ``min_dates`` is carried so a click can fetch exactly that point's dancers.
    """
    raw = _community_frame(conn)
    total_dates = int(raw['date'].nunique())

    def series(df: pd.DataFrame) -> list[dict]:
        per_dancer = df.groupby('dancer_id')['date'].nunique()
        if per_dancer.empty or total_dates == 0:
            return []
        return [
            {
                'min_dates': c,
                'pct': round(c / total_dates * 100, 2),
                'dancers': int((per_dancer >= c).sum()),
            }
            for c in range(1, int(per_dancer.max()) + 1)
        ]

    return {
        'total_dates': total_dates,
        'incl_30th': series(raw),
        'excl_30th': series(raw[raw['is_30th'] == 0]),
    }


def community_2026_dancer_rows(scope: str, min_dates: int) -> list[dict]:
    """(dancer_id, enc_name) for dancers who attended at least ``min_dates`` unique dates in 2026.

    ``scope`` is 'incl' or 'excl' for whether the 30th anniversary weekender counts. ``enc_name``
    is the Fernet ciphertext of the dancer's name (or None if no name is on file); the browser
    decrypts it so the server never emits plaintext. Backs the click-to-download on the community
    chart. Raises FileNotFoundError if the database is absent.
    """
    if not Path(config.ATTENDANCE_DB_PATH).exists():
        raise FileNotFoundError(config.ATTENDANCE_DB_PATH)
    conn = sqlite3.connect(config.ATTENDANCE_DB_PATH)
    try:
        raw = _community_frame(conn)
        if scope == 'excl':
            raw = raw[raw['is_30th'] == 0]
        per_dancer = raw.groupby('dancer_id')['date'].nunique()
        ids = sorted(per_dancer[per_dancer >= min_dates].index.tolist())
        enc = _enc_names(conn, ids)
    finally:
        conn.close()
    return [{'dancer_id': i, 'enc_name': enc.get(i)} for i in ids]


def _enc_names(conn: sqlite3.Connection, dancer_ids: list[str]) -> dict[str, str | None]:
    """Map each dancer_id to its enc_name ciphertext (absent ids and null names simply don't appear)."""
    if not dancer_ids:
        return {}
    placeholders = ','.join('?' * len(dancer_ids))
    rows = conn.execute(
        f'SELECT dancer_id, enc_name FROM dancer WHERE dancer_id IN ({placeholders})', dancer_ids
    ).fetchall()
    return {d: e for d, e in rows}


def decrypt_params() -> dict:
    """The non-secret parameters the browser needs to derive the decryption key and check the passphrase.

    ``salt`` (hex) feeds PBKDF2-SHA256 (480k iterations) to reproduce the Fernet key from the
    operator's passphrase; ``sentinel`` is a token the browser decrypts to confirm the passphrase
    is right before doing any real work. Neither is secret — the passphrase and the derived key
    never leave the browser. Raises FileNotFoundError if the database is absent.
    """
    if not Path(config.ATTENDANCE_DB_PATH).exists():
        raise FileNotFoundError(config.ATTENDANCE_DB_PATH)
    conn = sqlite3.connect(config.ATTENDANCE_DB_PATH)
    try:
        rows = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('salt', 'sentinel')").fetchall())
    finally:
        conn.close()
    return {'salt': rows.get('salt'), 'sentinel': rows.get('sentinel')}


# The fields on each activity-record row: event/activity context repeated on every row, then the
# record itself. Named per-person rows fill dancer_id/status; anonymous aggregate head-count rows
# fill head_count instead. The rows also carry an ``enc_name`` ciphertext (not listed here): the
# browser decrypts it and writes the CSV, inserting first_name/last_name columns, so plaintext
# names are never assembled server-side.
ACTIVITY_RECORD_FIELDS = [
    'event_name',
    'event_type',
    'venue',
    'activity_name',
    'activity_type',
    'difficulty',
    'date',
    'record_type',
    'dancer_id',
    'status',
    'ticket_type',
    'head_count',
]


def activity_records(activity_id: int) -> list[dict]:
    """Every attendance record for one activity, with its parent event/activity context.

    One row per named attendee (dancer_id + status, plus the ``enc_name`` ciphertext the browser
    decrypts to first/last name) and one per anonymous aggregate head-count (head_count, no
    dancer), each carrying the event and activity it belongs to. Backs the click-to-download on
    the all-activities scatter. Returns an empty list for an unknown activity; raises
    FileNotFoundError if the database is absent.
    """
    if not Path(config.ATTENDANCE_DB_PATH).exists():
        raise FileNotFoundError(config.ATTENDANCE_DB_PATH)
    conn = sqlite3.connect(config.ATTENDANCE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        context = conn.execute(
            'SELECT e.name AS event_name, e.event_type, e.venue, '
            'a.name AS activity_name, a.activity_type, a.difficulty, a.date '
            'FROM activity a JOIN event e USING(event_id) WHERE a.activity_id = ?',
            (activity_id,),
        ).fetchone()
        if context is None:
            return []
        named = conn.execute(
            'SELECT at.dancer_id, at.status, at.ticket_type, d.enc_name '
            'FROM attendance at LEFT JOIN dancer d ON d.dancer_id = at.dancer_id '
            'WHERE at.activity_id = ? ORDER BY at.dancer_id',
            (activity_id,),
        ).fetchall()
        aggregate = conn.execute(
            'SELECT ticket_type, head_count FROM attendance_count WHERE activity_id = ? ORDER BY ticket_type',
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()

    ctx = dict(context)
    rows = [
        {
            **ctx,
            'record_type': 'named',
            'dancer_id': r['dancer_id'],
            'status': r['status'],
            'ticket_type': r['ticket_type'],
            'head_count': '',
            'enc_name': r['enc_name'],
        }
        for r in named
    ]
    rows += [
        {
            **ctx,
            'record_type': 'aggregate',
            'dancer_id': '',
            'status': '',
            'ticket_type': r['ticket_type'],
            'head_count': r['head_count'],
            'enc_name': None,
        }
        for r in aggregate
    ]
    return rows


def summaries() -> dict:
    """Build the /attendance summary-chart datasets from the deployed attendance database.

    Raises FileNotFoundError if that database hasn't been built. The term calendar is derived
    once and shared by the builders that bucket by term.
    """
    if not Path(config.ATTENDANCE_DB_PATH).exists():
        raise FileNotFoundError(config.ATTENDANCE_DB_PATH)

    conn = sqlite3.connect(config.ATTENDANCE_DB_PATH)
    try:
        terms, assign = _term_calendar(conn)
        return {
            'beginner_intake': _beginner_intake(conn, terms, assign),
            'level2_socials': _level2_and_socials(conn, terms, assign),
            'cohort_retention': _cohort_retention(conn, terms, assign),
            'community_2026': _community_2026(conn),
        }
    finally:
        conn.close()
