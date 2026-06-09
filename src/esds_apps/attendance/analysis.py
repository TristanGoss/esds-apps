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
            teams.append({'id': int(r.teacher_id), 'label': r.teacher_label})
    teams.sort(key=lambda t: t['id'])

    return {'terms': term_meta, 'matrix': matrix, 'teams': teams}


def summaries() -> dict:
    """Build all three /attendance summary-chart datasets from the deployed attendance database.

    Raises FileNotFoundError if that database hasn't been built. The term calendar is derived
    once and shared by the three builders.
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
        }
    finally:
        conn.close()
