import datetime
import sqlite3

import pytest

from esds_apps.attendance import analysis
from esds_apps.attendance.attendance_db import ActivityType, AttendanceStatus, EventType, open_db


def _add_course(db, name, difficulty, start, n_lessons, dancers, teachers=None, social_with=None):  # noqa: PLR0913
    """Add a course event with ``n_lessons`` weekly lessons; optionally teachers and a social.

    ``social_with`` is an iterable of dancers who attend a post-class social on the start date.
    """
    event_id = db.upsert_event(name, EventType.COURSE)
    if teachers:
        db.set_event_teachers(event_id, teachers)
    day = start
    for week in range(n_lessons):
        activity_id = db.upsert_activity(event_id, f'{name} wk{week}', day, ActivityType.LESSON, difficulty)
        for dancer in dancers:
            db.record_attendance(activity_id, dancer, AttendanceStatus.ATTENDED)
        day += datetime.timedelta(days=7)
    if social_with:
        social_id = db.upsert_activity(event_id, f'{name} social', start, ActivityType.SOCIAL, None)
        for dancer in social_with:
            db.record_attendance(social_id, dancer, AttendanceStatus.ATTENDED)
    return event_id


@pytest.fixture
def built_db(tmp_path, monkeypatch):
    """A small attendance DB spanning two academic years, pointed at by config.ATTENDANCE_DB_PATH.

    Two terms in 2024/25 and one in 2025/26 (starts > 21 days apart so they don't merge), each
    with a Level 1 and a Level 2 course; the 2024/25 terms carry a post-class social. Dancers
    overlap across terms so the retention matrix has off-diagonal structure.
    """
    path = tmp_path / 'attendance.sqlite'
    db = open_db(path, enforce_foreign_keys=False)  # records built without seeding the pseudonyms store

    t1 = datetime.date(2024, 9, 2)
    t2 = datetime.date(2024, 11, 4)
    t3 = datetime.date(2025, 9, 1)
    teachers_a = ['DNC-AAAA1111', 'DNC-BBBB2222']
    teachers_b = ['DNC-CCCC3333', 'DNC-DDDD4444']

    # 2024/25 term 1: five L1 and L2 dancers, a social, taught by team A.
    l1_autumn_2024 = _add_course(
        db,
        'L1 Autumn 2024',
        'Level 1',
        t1,
        4,
        ['d1', 'd2', 'd3', 'd4', 'd5'],
        teachers=teachers_a,
        social_with=['d1', 'd2'],
    )
    db.record_waitlist(l1_autumn_2024, 'DNC-WAIT0001')  # two waitlisted for the autumn L1 course
    db.record_waitlist(l1_autumn_2024, 'DNC-WAIT0002')
    _add_course(db, 'L2 Autumn 2024', 'Level 2', t1, 4, ['e1', 'e2', 'e3'])
    # 2024/25 term 2: d1/d2 return (retained), taught by team B.
    _add_course(db, 'L1 Winter 2024', 'Level 1', t2, 4, ['d1', 'd2', 'd6'], teachers=teachers_b)
    _add_course(db, 'L2 Winter 2024', 'Level 2', t2, 4, ['e1', 'e2'])
    # 2025/26 term 1: d1 still active a year later, taught by team A again.
    _add_course(db, 'L1 Autumn 2025', 'Level 1', t3, 4, ['d1', 'd7', 'd8'], teachers=teachers_a, social_with=['d1'])
    _add_course(db, 'L2 Autumn 2025', 'Level 2', t3, 4, ['e1'])

    # 2026: named Level 2 attendance begins, so the community chart has data. A spring L2 block
    # (Level 2 doesn't anchor a term, so the term count is unchanged) plus the 30th anniversary
    # weekender, so the 30th can be filtered out and incl/excl differ. f1 attends both, g1 only
    # the anniversary, so excluding it drops g1 and one of f1's dates.
    t4 = datetime.date(2026, 1, 13)
    _add_course(db, 'L2 Spring 2026', 'Level 2', t4, 4, ['e1', 'f1', 'f2'])
    anniv = db.upsert_event('30 Years of ESDS', EventType.WEEKENDER)
    anniv_act = db.upsert_activity(anniv, '30th party', datetime.date(2026, 3, 20), ActivityType.SOCIAL, None)
    for dancer in ('f1', 'g1'):
        db.record_attendance(anniv_act, dancer, AttendanceStatus.ATTENDED)
    db.record_count(anniv_act, None, 5)  # five anonymous door heads at the party
    db.close()

    monkeypatch.setattr(analysis.config, 'ATTENDANCE_DB_PATH', path)
    return path


@pytest.fixture
def termly_db(tmp_path, monkeypatch):
    """A DB with a single 2026-anchored term, for the termly active-community builder (Plot 8).

    A Level 1 course anchors one 2026 term; a 30th anniversary weekender falls inside it so the
    incl/excl views differ. Attendance is set per dancer so all four counts come out distinct:
      a2 -> all four L1 lessons          (regular in both views)
      a1 -> two L1 lessons + anniversary (regular in both)
      c1 -> one L1 lesson  + anniversary (regular incl only; active excl)
      b1 -> anniversary only             (active incl only; absent from excl)
    So for the one term: active incl/excl = 4/3, regulars incl/excl = 3/2.
    """
    path = tmp_path / 'attendance.sqlite'
    db = open_db(path, enforce_foreign_keys=False)

    event_id = db.upsert_event('L1 Spring 2026', EventType.COURSE)
    day = datetime.date(2026, 1, 13)
    lessons = []
    for week in range(4):  # four lessons so the L1 block anchors a term
        lessons.append(db.upsert_activity(event_id, f'L1 wk{week}', day, ActivityType.LESSON, 'Level 1'))
        day += datetime.timedelta(days=7)
    for dancer, n in (('a2', 4), ('a1', 2), ('c1', 1)):
        for activity_id in lessons[:n]:
            db.record_attendance(activity_id, dancer, AttendanceStatus.ATTENDED)

    anniv = db.upsert_event('30 Years of ESDS', EventType.WEEKENDER)
    anniv_act = db.upsert_activity(anniv, '30th party', datetime.date(2026, 3, 20), ActivityType.SOCIAL, None)
    for dancer in ('a1', 'c1', 'b1'):
        db.record_attendance(anniv_act, dancer, AttendanceStatus.ATTENDED)
    db.close()

    monkeypatch.setattr(analysis.config, 'ATTENDANCE_DB_PATH', path)
    return path


def test_summaries_top_level_shape(built_db):
    s = analysis.summaries()
    assert set(s) == {
        'beginner_intake',
        'level2_socials',
        'cohort_retention',
        'community_2026',
        'termly_active',
        'year_min',
        'year_max',
    }
    # The colour-ramp span covers both the derived terms (to 2025) and the early stats (from 2017).
    assert s['year_min'] == 2017
    assert s['year_max'] == 2025


def test_beginner_intake_groups_by_year(built_db):
    years = {y['label']: y for y in analysis.summaries()['beginner_intake']}
    assert {'24/25', '25/26'} <= set(years)
    # Two terms ingested in 2024/25, one in 2025/26.
    assert len(years['24/25']['points']) == 2
    assert len(years['25/26']['points']) == 1
    # Attended (5 named) < registered: 5 named registrations + 2 waitlisted folded in.
    p = years['24/25']['points'][0]
    assert p['term_num'] == 1
    assert p['attended'] == 5.0
    assert p['registered'] == 7.0  # 5 registered + 2 waitlisted


def test_beginner_intake_includes_pre_database_years(built_db):
    """The pre-database early-stats L1 years appear as mean-attendance-only series (no registered)."""
    years = {y['label']: y for y in analysis.summaries()['beginner_intake']}
    # Years are ordered oldest-first so the colour ramp reads chronologically.
    labels = [y['label'] for y in analysis.summaries()['beginner_intake']]
    assert labels == sorted(labels, key=lambda s: int('20' + s[:2]))
    assert '17/18' in years  # earliest early-stats L1 year
    early = years['17/18']['points'][0]
    assert early['attended'] > 0
    assert early['registered'] is None  # no registration figures recovered pre-database


def test_level2_socials_includes_pre_database_years(built_db):
    """The pre-database early-stats L2 years appear as class-only series with no paired socials."""
    years = {y['label']: y for y in analysis.summaries()['level2_socials']}
    assert '21/22' in years  # earliest early-stats L2 year
    assert years['21/22']['class_points']
    assert years['21/22']['social_points'] == []


def test_early_term_mean_lines_are_2020_on_segments():
    lines = analysis.early_term_mean_lines()
    # Only 2020-on periods are kept (the 2018/2019 early rows are dropped from the scatter).
    assert lines and all(int(ln['start'][:4]) >= 2020 for ln in lines)
    assert {ln['level'] for ln in lines} == {'L1', 'L2'}
    for ln in lines:
        assert ln['start'] <= ln['end']
        assert ln['mean'] > 0
    # A known row: Sept-Oct 2022 L1 spans the whole two-month block.
    sep = next(ln for ln in lines if ln['level'] == 'L1' and ln['start'] == '2022-09-01')
    assert sep['end'] == '2022-10-31'


def test_beginner_intake_folds_waitlist_into_registered(built_db):
    """The dashed 'registered' series adds the course's per-event waitlist on top of registrations."""
    term1 = {y['label']: y for y in analysis.summaries()['beginner_intake']}['24/25']['points'][0]
    # 5 dancers registered on every lesson; 2 waitlisters for the event; no one turned away in later terms.
    assert term1['registered'] - term1['attended'] == 2.0
    # A term with no waitlist keeps registered == attended (all 5 present, none waitlisted).
    term25 = {y['label']: y for y in analysis.summaries()['beginner_intake']}['25/26']['points'][0]
    assert term25['registered'] == term25['attended']


def test_level2_socials_only_show_paired_socials_from_2024(built_db):
    years = {y['label']: y for y in analysis.summaries()['level2_socials']}
    assert {'24/25', '25/26'} <= set(years)
    assert all(len(y['class_points']) >= 1 for y in years.values())
    # Both years here are >= 2024/25, so each has its social line.
    assert years['24/25']['social_points']
    assert years['25/26']['social_points']


def test_cohort_retention_matrix_and_teams(built_db):
    cr = analysis.summaries()['cohort_retention']
    n = len(cr['terms'])
    assert n == 3
    assert all(len(row) == n for row in cr['matrix'])
    # Cohort's own joining term is by definition 100% present.
    assert cr['matrix'][0][0] == 100.0
    # The impossible lower-right triangle is blanked, not zero.
    assert cr['matrix'][-1][1] is None
    # d1/d2 joined in term 0 and returned in term 1, so term 0 retains some of its cohort.
    assert cr['matrix'][0][1] is not None and cr['matrix'][0][1] > 0
    # Teaching teams are surfaced with stripped DNC- labels; team A taught two of the three terms.
    labels = {t['label'] for t in cr['teams']}
    assert 'AAAA1111+BBBB2222' in labels


def test_community_2026_size_and_commitment(built_db):
    c = analysis.summaries()['community_2026']
    # 4 spring L2 dates + 1 anniversary date, the fixed denominator for both series.
    assert c['total_dates'] == 5
    incl, excl = c['incl_30th'], c['excl_30th']
    # Survival counts are non-increasing as the threshold rises.
    ys = [p['dancers'] for p in incl]
    assert ys == sorted(ys, reverse=True)
    # Leftmost point is the whole active community; the 30th adds g1 on top of e1/f1/f2.
    assert incl[0]['dancers'] == 4
    assert excl[0]['dancers'] == 3
    # pct uses the fixed 5-date denominator: 1 date -> 20%.
    assert incl[0]['pct'] == 20.0


def test_termly_active_counts_split_by_threshold_and_anniversary(termly_db):
    rows = analysis.summaries()['termly_active']
    assert len(rows) == 1  # one term starts in 2026
    r = rows[0]
    assert r['label']
    # active (>= 1): the anniversary-only dancer (b1) shows in incl but not excl.
    assert (r['active_incl'], r['active_excl']) == (4, 3)
    # regulars (>= 2): c1 reaches two activities only by counting the anniversary, so drops from excl.
    assert (r['regular_incl'], r['regular_excl']) == (3, 2)


def test_termly_active_empty_without_a_2026_term(built_db):
    # built_db folds its 2026 activity into the 2025-anchored term, so no term *starts* in 2026 and
    # the chart -- which filters on term start, as the notebook does -- has nothing to show.
    assert analysis.summaries()['termly_active'] == []


def test_termly_active_dancer_rows_by_threshold_and_scope(termly_db):
    term_start = analysis.summaries()['termly_active'][0]['term_start']

    def ids(scope, min_activities):
        return _ids(analysis.termly_active_dancer_rows(term_start, scope, min_activities))

    # active (>= 1): the anniversary-only dancer b1 counts incl but not excl.
    assert ids('incl', 1) == ['a1', 'a2', 'b1', 'c1']
    assert ids('excl', 1) == ['a1', 'a2', 'c1']
    # regulars (>= 2): c1 only reaches two activities by counting the anniversary, so drops from excl.
    assert ids('incl', 2) == ['a1', 'a2', 'c1']
    assert ids('excl', 2) == ['a1', 'a2']
    # enc_name slot is always present (None here: this fixture seeds no encrypted identities).
    assert all('enc_name' in r for r in analysis.termly_active_dancer_rows(term_start, 'incl', 1))


def test_termly_active_dancer_rows_unknown_term(termly_db):
    # A term_start matching no term yields an empty list rather than raising.
    assert analysis.termly_active_dancer_rows('2019-09-01', 'incl', 1) == []


def _ids(rows):
    return [r['dancer_id'] for r in rows]


def test_community_2026_dancers_scope(built_db):
    assert _ids(analysis.community_2026_dancer_rows('incl', 1)) == ['e1', 'f1', 'f2', 'g1']
    assert _ids(analysis.community_2026_dancer_rows('excl', 1)) == ['e1', 'f1', 'f2']
    # Only f1 reaches all five dates (four L2 + the anniversary).
    assert _ids(analysis.community_2026_dancer_rows('incl', 5)) == ['f1']
    # Each row carries an enc_name slot (None here: this fixture seeds no encrypted identities).
    assert all('enc_name' in r for r in analysis.community_2026_dancer_rows('incl', 1))


def test_activity_records(built_db):
    conn = sqlite3.connect(built_db)
    activity_id = conn.execute("SELECT activity_id FROM activity WHERE name = '30th party'").fetchone()[0]
    conn.close()
    rows = analysis.activity_records(activity_id)

    named = [r for r in rows if r['record_type'] == 'named']
    aggregate = [r for r in rows if r['record_type'] == 'aggregate']
    assert sorted(r['dancer_id'] for r in named) == ['f1', 'g1']
    assert all(r['status'] == 'attended' for r in named)
    # The anonymous door head-count comes through as one aggregate row, no dancer attached.
    assert len(aggregate) == 1
    assert aggregate[0]['head_count'] == 5
    assert aggregate[0]['dancer_id'] == ''
    # Every row carries the parent event/activity context, plus an enc_name slot for the browser
    # to decrypt (None here: this fixture seeds no encrypted identities).
    assert all(r['event_name'] == '30 Years of ESDS' for r in rows)
    assert all(r['activity_name'] == '30th party' for r in rows)
    assert all('enc_name' in r for r in rows)


def test_activity_records_unknown_activity(built_db):
    assert analysis.activity_records(99999) == []


def test_summaries_missing_db_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis.config, 'ATTENDANCE_DB_PATH', tmp_path / 'nope.sqlite')
    with pytest.raises(FileNotFoundError):
        analysis.summaries()


def test_community_and_activity_dancers_missing_db_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis.config, 'ATTENDANCE_DB_PATH', tmp_path / 'nope.sqlite')
    with pytest.raises(FileNotFoundError):
        analysis.community_2026_dancer_rows('incl', 1)
    with pytest.raises(FileNotFoundError):
        analysis.termly_active_dancer_rows('2026-01-01', 'incl', 1)
    with pytest.raises(FileNotFoundError):
        analysis.activity_records(1)
    with pytest.raises(FileNotFoundError):
        analysis.decrypt_params()


def test_cohort_retention_exposes_teacher_ciphertext(built_db):
    cr = analysis.summaries()['cohort_retention']
    # Each team carries the dancer ids of its teachers, for the browser to relabel the legend.
    team_a = next(t for t in cr['teams'] if t['label'] == 'AAAA1111+BBBB2222')
    assert team_a['ids'] == ['DNC-AAAA1111', 'DNC-BBBB2222']
    # teacher_enc maps every teacher id present; this fixture seeds no ciphertext, so it's empty.
    assert isinstance(cr['teacher_enc'], dict)


def test_decrypt_params_shape(built_db):
    # The attendance DB carries the meta keys; this fixture (built without the pseudonymiser) leaves
    # them unset, so the values are None but the keys are always present.
    params = analysis.decrypt_params()
    assert set(params) == {'salt', 'sentinel'}
