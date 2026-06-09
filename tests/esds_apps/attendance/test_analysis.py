import datetime

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
    db = open_db(path)

    t1 = datetime.date(2024, 9, 2)
    t2 = datetime.date(2024, 11, 4)
    t3 = datetime.date(2025, 9, 1)
    teachers_a = ['DNC-AAAA1111', 'DNC-BBBB2222']
    teachers_b = ['DNC-CCCC3333', 'DNC-DDDD4444']

    # 2024/25 term 1: five L1 and L2 dancers, a social, taught by team A.
    _add_course(
        db,
        'L1 Autumn 2024',
        'Level 1',
        t1,
        4,
        ['d1', 'd2', 'd3', 'd4', 'd5'],
        teachers=teachers_a,
        social_with=['d1', 'd2'],
    )
    _add_course(db, 'L2 Autumn 2024', 'Level 2', t1, 4, ['e1', 'e2', 'e3'])
    # 2024/25 term 2: d1/d2 return (retained), taught by team B.
    _add_course(db, 'L1 Winter 2024', 'Level 1', t2, 4, ['d1', 'd2', 'd6'], teachers=teachers_b)
    _add_course(db, 'L2 Winter 2024', 'Level 2', t2, 4, ['e1', 'e2'])
    # 2025/26 term 1: d1 still active a year later, taught by team A again.
    _add_course(db, 'L1 Autumn 2025', 'Level 1', t3, 4, ['d1', 'd7', 'd8'], teachers=teachers_a, social_with=['d1'])
    _add_course(db, 'L2 Autumn 2025', 'Level 2', t3, 4, ['e1'])
    db.close()

    monkeypatch.setattr(analysis.config, 'ATTENDANCE_DB_PATH', path)
    return path


def test_summaries_top_level_shape(built_db):
    s = analysis.summaries()
    assert set(s) == {'beginner_intake', 'level2_socials', 'cohort_retention'}


def test_beginner_intake_groups_by_year(built_db):
    years = {y['label']: y for y in analysis.summaries()['beginner_intake']}
    assert set(years) == {'24/25', '25/26'}
    # Two terms ingested in 2024/25, one in 2025/26.
    assert len(years['24/25']['points']) == 2
    assert len(years['25/26']['points']) == 1
    # Attended (5 named) <= registered (also 5 here); both populated and positive.
    p = years['24/25']['points'][0]
    assert p['term_num'] == 1
    assert p['attended'] > 0 and p['registered'] > 0


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


def test_summaries_missing_db_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis.config, 'ATTENDANCE_DB_PATH', tmp_path / 'nope.sqlite')
    with pytest.raises(FileNotFoundError):
        analysis.summaries()
