import types
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.testclient import TestClient

from esds_apps import config
from esds_apps.main import (
    _attendance_activity_rows,
    app,
    card_scanning_log,
    create_and_or_return_wallet_pass_link,
    download_checks,
    landing_page,
)


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def auth_client(monkeypatch):
    """TestClient with auth bypassed."""
    monkeypatch.setattr('esds_apps.auth._get_authenticated_email', lambda req: 'user@example.com')
    return TestClient(app, follow_redirects=False)


def test_qr_codes_table_get(auth_client, monkeypatch):
    dummy_codes = [{'code_id': 'abc123', 'target_url': 'https://example.com', 'description': 'desc', 'scan_count': 0}]
    monkeypatch.setattr('esds_apps.main.qr_db', types.SimpleNamespace(list_qr_codes=lambda: dummy_codes))
    response = auth_client.get('/qr-codes')
    assert response.status_code == HTTPStatus.OK
    assert 'abc123' in response.text


def test_qr_codes_table_post_create(auth_client, monkeypatch):
    called = {}

    def fake_add_qr_code(code_id, target_url, description):
        called['added'] = (code_id, target_url, description)

    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            add_qr_code=fake_add_qr_code, list_qr_codes=lambda: [], delete_qr_code=lambda code_id: None
        ),
    )
    response = auth_client.post('/qr-codes', data={'target_url': 'https://test.com', 'description': 'desc'})
    assert response.status_code == HTTPStatus.SEE_OTHER
    assert called['added'][1] == 'https://test.com'


def test_qr_codes_table_post_delete(auth_client, monkeypatch):
    called = {}

    def fake_delete_qr_code(code_id):
        called['deleted'] = code_id

    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            delete_qr_code=fake_delete_qr_code, list_qr_codes=lambda: [], add_qr_code=lambda *a, **kw: None
        ),
    )
    response = auth_client.post('/qr-codes', data={'delete_code_id': 'abc123'})
    assert response.status_code == HTTPStatus.SEE_OTHER
    assert called['deleted'] == 'abc123'


def test_serve_tracked_qr_code_svg(auth_client, monkeypatch):
    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            get_qr_code=lambda code_id: {
                'code_id': code_id,
                'target_url': 'https://example.com',
                'description': 'desc',
                'scan_count': 0,
            }
        ),
    )

    class DummyQR:
        def save(self, buf, kind, scale=None):
            buf.write(b'<svg>dummy</svg>')

    monkeypatch.setattr('esds_apps.main.segno', types.SimpleNamespace(make=lambda url: DummyQR()))
    response = auth_client.get('/qr-codes/abc123/qr.svg')
    assert response.status_code == HTTPStatus.OK
    assert b'dummy' in response.content
    assert response.headers['content-type'] == 'image/svg+xml'


def test_serve_tracked_qr_code_png(auth_client, monkeypatch):
    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            get_qr_code=lambda code_id: {
                'code_id': code_id,
                'target_url': 'https://example.com',
                'description': 'desc',
                'scan_count': 0,
            }
        ),
    )

    class DummyQR:
        def save(self, buf, kind, scale=None):
            buf.write(b'PNGDATA')

    monkeypatch.setattr('esds_apps.main.segno', types.SimpleNamespace(make=lambda url: DummyQR()))
    response = auth_client.get('/qr-codes/abc123/qr.png')
    assert response.status_code == HTTPStatus.OK
    assert b'PNGDATA' in response.content
    assert response.headers['content-type'] == 'image/png'


def test_serve_tracked_qr_code_not_found(auth_client, monkeypatch):
    monkeypatch.setattr('esds_apps.main.qr_db', types.SimpleNamespace(get_qr_code=lambda code_id: None))
    response = auth_client.get('/qr-codes/abc123/qr.svg')
    assert response.status_code == HTTPStatus.NOT_FOUND


def test_tracked_qr_scan_redirect(client, monkeypatch):
    called = {}

    def fake_increment_scan(code_id):
        called['incremented'] = code_id

    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            get_qr_code=lambda code_id: {
                'code_id': code_id,
                'target_url': 'https://example.com',
                'description': 'desc',
                'scan_count': 0,
            },
            increment_scan=fake_increment_scan,
        ),
    )
    response = client.get('/s/abc123')
    assert response.status_code == HTTPStatus.FOUND
    assert response.headers['location'] == 'https://example.com'
    assert called['incremented'] == 'abc123'


def test_tracked_qr_scan_not_found(client, monkeypatch):
    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(get_qr_code=lambda code_id: None, increment_scan=lambda code_id: None),
    )
    response = client.get('/s/abc123')
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
@patch('esds_apps.main.config.TEMPLATES.TemplateResponse')
async def test_landing_page(mock_template):
    request = MagicMock()
    await landing_page(request)
    mock_template.assert_called_once_with(request, 'landing.html')


def test_proxy_card_check_valid_url(client):
    target = config.DC_HOST + '/api/dummy'
    with respx.mock:
        respx.get(target).mock(return_value=httpx.Response(200, content=b'abc', headers={'content-type': 'text/plain'}))
        response = client.get(f'/proxy-card-check?url={target}')
    assert response.status_code == HTTPStatus.OK
    assert response.content == b'abc'


def test_proxy_card_check_invalid_url(client):
    response = client.get('/proxy-card-check?url=https://malicious.com')
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
@patch('esds_apps.auth._get_authenticated_email', return_value='user@example.com')
@patch('esds_apps.main.fetch_membership_card_checks')
@patch('esds_apps.main.config.TEMPLATES.TemplateResponse')
async def test_card_scanning_log(mock_template, mock_fetch, mock_auth, sample_check):
    request = MagicMock()
    mock_fetch.return_value = [sample_check]
    await card_scanning_log(request)
    assert mock_template.called
    context = mock_template.call_args[0][2]
    assert 'checks' in context
    assert len(context['checks']) == 1


@pytest.mark.asyncio
@patch('esds_apps.auth._get_authenticated_email', return_value='user@example.com')
@patch('esds_apps.main.fetch_membership_card_checks')
async def test_download_checks_csv(mock_fetch, mock_auth, sample_check):
    mock_fetch.return_value = [sample_check]
    response = await download_checks(MagicMock(), days_ago=10)
    assert isinstance(response, StreamingResponse)
    text = ''.join([chunk async for chunk in response.body_iterator])
    assert 'member_uuid' in text
    assert 'Alice' in text


@pytest.mark.asyncio
@patch('esds_apps.main.fetch_membership_cards')
@patch('esds_apps.main.MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.read')
async def test_wallet_pass_redirect_cache_hit(mock_cache_read, mock_fetch_cards):
    mock_card = MagicMock()
    mock_card.card_uuid = 'abc123'
    mock_card.card_number = '123456'
    mock_fetch_cards.return_value = [mock_card]
    mock_cache_read.return_value = {'123456': 'cachedpass'}

    response = await create_and_or_return_wallet_pass_link(MagicMock(), 'abc123')
    assert isinstance(response, RedirectResponse)
    assert 'pass2u.net' in response.headers['location']


@pytest.mark.asyncio
@patch('esds_apps.main.create_wallet_pass', return_value='newpassid')
@patch('esds_apps.main.fetch_membership_cards')
@patch('esds_apps.main.MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.read', return_value={})
async def test_wallet_pass_redirect_cache_miss(mock_cache, mock_fetch, mock_create):
    mock_card = MagicMock()
    mock_card.card_uuid = 'abc123'
    mock_card.card_number = '123456'
    mock_fetch.return_value = [mock_card]

    response = await create_and_or_return_wallet_pass_link(MagicMock(), 'abc123')
    assert isinstance(response, RedirectResponse)
    assert response.status_code == HTTPStatus.SEE_OTHER
    assert 'pass2u.net/d/newpassid' in response.headers['location']


@pytest.mark.asyncio
@patch('esds_apps.auth._get_authenticated_email', return_value='user@example.com')
@patch('esds_apps.main.fetch_membership_cards', return_value=[])
@patch('esds_apps.main.config.TEMPLATES.TemplateResponse')
async def test_membership_cards_page(mock_template, mock_fetch, mock_auth):
    from esds_apps.main import membership_cards

    await membership_cards(MagicMock())
    mock_template.assert_called_once()


@pytest.mark.asyncio
@patch('esds_apps.auth._get_authenticated_email', return_value='user@example.com')
@patch('esds_apps.main.fetch_pos_permissions', return_value=[])
@patch('esds_apps.main.config.TEMPLATES.TemplateResponse')
async def test_pos_permissions_page(mock_template, mock_fetch, mock_auth):
    from esds_apps.main import pos_permissions

    await pos_permissions(MagicMock())
    mock_template.assert_called_once()


def test_serve_tracked_qr_code_invalid_format(auth_client, monkeypatch):
    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            get_qr_code=lambda code_id: {
                'code_id': code_id,
                'target_url': 'https://example.com',
                'description': 'desc',
                'scan_count': 0,
            }
        ),
    )
    monkeypatch.setattr('esds_apps.main.segno', types.SimpleNamespace(make=lambda url: MagicMock()))
    response = auth_client.get('/qr-codes/abc123/qr.pdf')
    assert response.status_code == HTTPStatus.BAD_REQUEST


def _stub_qr_db(**extra):
    return types.SimpleNamespace(
        list_qr_codes=lambda: [],
        add_qr_code=lambda *a, **kw: None,
        delete_qr_code=lambda *a: None,
        **extra,
    )


def test_qr_codes_table_post_empty_url(auth_client, monkeypatch):
    monkeypatch.setattr('esds_apps.main.qr_db', _stub_qr_db())
    response = auth_client.post('/qr-codes', data={'target_url': '', 'description': 'desc'})
    assert response.status_code == HTTPStatus.OK
    assert 'Please enter a target URL' in response.text


def test_qr_codes_table_post_unsafe_url(auth_client, monkeypatch):
    monkeypatch.setattr('esds_apps.main.qr_db', _stub_qr_db())
    response = auth_client.post('/qr-codes', data={'target_url': 'ftp://bad.com', 'description': 'desc'})
    assert response.status_code == HTTPStatus.OK
    assert 'http' in response.text


def test_tracked_qr_scan_unsafe_url(client, monkeypatch):
    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            get_qr_code=lambda code_id: {
                'code_id': code_id,
                'target_url': 'javascript:evil()',
                'description': 'x',
                'scan_count': 0,
            },
            increment_scan=lambda code_id: None,
        ),
    )
    response = client.get('/s/abc123')
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


def test_download_qr_code_scans_csv(auth_client, monkeypatch):
    from datetime import datetime, timezone

    dt1 = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    dt2 = datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            get_qr_code=lambda code_id: {
                'code_id': code_id,
                'target_url': 'https://x.com',
                'description': 'My Event',
                'scan_count': 2,
            },
            get_scan_datetimes=lambda code_id: [dt1, dt2],
        ),
    )
    response = auth_client.get('/qr-codes/abc123/scans.csv')
    assert response.status_code == HTTPStatus.OK
    assert 'scanned_at_utc' in response.text
    assert '2024-01-01' in response.text


def test_attendance_activities_includes_early_term_means(auth_client, monkeypatch):
    monkeypatch.setattr('esds_apps.main._attendance_activity_rows', lambda: [{'activity_id': 1}])
    monkeypatch.setattr(
        'esds_apps.main.analysis.early_term_mean_lines',
        lambda: [{'level': 'L1', 'start': '2022-09-01', 'end': '2022-10-31', 'mean': 33.43}],
    )
    response = auth_client.get('/attendance/activities.json')
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body['activities'] == [{'activity_id': 1}]
    assert body['early_term_means'][0]['level'] == 'L1'


def test_attendance_summaries_ok(auth_client, monkeypatch):
    payload = {'beginner_intake': [{'label': '24/25', 'points': []}], 'level2_socials': [], 'cohort_retention': {}}
    monkeypatch.setattr('esds_apps.main.analysis.summaries', lambda: payload)
    response = auth_client.get('/attendance/summaries.json')
    assert response.status_code == HTTPStatus.OK
    assert response.json() == payload


def test_attendance_summaries_db_missing(auth_client, monkeypatch):
    def _raise():
        raise FileNotFoundError('no db')

    monkeypatch.setattr('esds_apps.main.analysis.summaries', _raise)
    response = auth_client.get('/attendance/summaries.json')
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert 'error' in response.json()


def test_attendance_summaries_requires_auth(client):
    assert client.get('/attendance/summaries.json').status_code == HTTPStatus.UNAUTHORIZED


def test_attendance_activity_records_json(auth_client, monkeypatch):
    def fake(activity_id):
        return [
            {'record_type': 'named', 'dancer_id': 'DNC-AAAA1111', 'status': 'attended', 'enc_name': 'gAAAA-ciphertext'},
            {'record_type': 'aggregate', 'head_count': 5, 'enc_name': None},
        ]

    monkeypatch.setattr('esds_apps.main.analysis.activity_records', fake)
    response = auth_client.get('/attendance/activity/7/records.json')
    assert response.status_code == HTTPStatus.OK
    assert response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert body['activity_id'] == 7
    # The endpoint serves ciphertext only; the browser decrypts enc_name into name columns.
    assert body['rows'][0]['enc_name'] == 'gAAAA-ciphertext'
    assert body['rows'][0]['dancer_id'] == 'DNC-AAAA1111'


def test_attendance_activity_records_db_missing(auth_client, monkeypatch):
    def _raise(activity_id):
        raise FileNotFoundError('no db')

    monkeypatch.setattr('esds_apps.main.analysis.activity_records', _raise)
    response = auth_client.get('/attendance/activity/7/records.json')
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert 'error' in response.json()


def test_attendance_activity_records_requires_auth(client):
    assert client.get('/attendance/activity/7/records.json').status_code == HTTPStatus.UNAUTHORIZED


def test_attendance_activity_rows_include_event_waitlist(tmp_path, monkeypatch):
    """Each scatter row carries its parent event's waitlist count (0 where there is none)."""
    from esds_apps.attendance.attendance_db import ActivityType, AttendanceStatus, EventType, open_db

    path = tmp_path / 'attendance.sqlite'
    db = open_db(path, enforce_foreign_keys=False)
    course = db.upsert_event('L1 Block', EventType.COURSE)
    lesson = db.upsert_activity(course, 'wk1', '2026-01-13', ActivityType.LESSON, 'Level 1')
    db.record_attendance(lesson, 'DNC-1', AttendanceStatus.ATTENDED)
    db.record_waitlist(course, 'DNC-8')  # two people waitlisted for the course
    db.record_waitlist(course, 'DNC-9')
    social = db.upsert_event('Solo Social', EventType.SOCIAL)  # a second event, no waitlist
    party = db.upsert_activity(social, 'party', '2026-02-01', ActivityType.SOCIAL, None)
    db.record_attendance(party, 'DNC-1', AttendanceStatus.ATTENDED)
    db.close()
    monkeypatch.setattr(config, 'ATTENDANCE_DB_PATH', path)

    rows = {r['activity_name']: r for r in _attendance_activity_rows()}
    assert rows['wk1']['waitlisted'] == 2  # the course's waitlisters attach to its lesson
    assert rows['party']['waitlisted'] == 0  # COALESCE to 0, not null, where there is no waitlist


def test_attendance_community_dancers_json(auth_client, monkeypatch):
    captured = {}

    def fake(scope, min_dates):
        captured['args'] = (scope, min_dates)
        return [{'dancer_id': 'DNC-AAAA1111', 'enc_name': 'gAAAA-1'}, {'dancer_id': 'DNC-BBBB2222', 'enc_name': None}]

    monkeypatch.setattr('esds_apps.main.analysis.community_2026_dancer_rows', fake)
    response = auth_client.get('/attendance/community/dancers.json?scope=excl&min_dates=3')
    assert response.status_code == HTTPStatus.OK
    assert response.headers['cache-control'] == 'no-store'
    assert captured['args'] == ('excl', 3)
    body = response.json()
    assert body['scope'] == 'excl' and body['min_dates'] == 3
    assert [d['dancer_id'] for d in body['dancers']] == ['DNC-AAAA1111', 'DNC-BBBB2222']
    assert body['dancers'][0]['enc_name'] == 'gAAAA-1'


def test_attendance_community_dancers_rejects_bad_scope(auth_client):
    response = auth_client.get('/attendance/community/dancers.json?scope=nonsense&min_dates=1')
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_attendance_community_dancers_requires_auth(client):
    response = client.get('/attendance/community/dancers.json?scope=incl&min_dates=1')
    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_attendance_community_term_dancers_json(auth_client, monkeypatch):
    captured = {}

    def fake(term_start, scope, min_activities):
        captured['args'] = (term_start, scope, min_activities)
        return [{'dancer_id': 'DNC-AAAA1111', 'enc_name': 'gAAAA-1'}, {'dancer_id': 'DNC-BBBB2222', 'enc_name': None}]

    monkeypatch.setattr('esds_apps.main.analysis.termly_active_dancer_rows', fake)
    response = auth_client.get(
        '/attendance/community/term-dancers.json?term_start=2026-01-08&scope=excl&min_activities=2'
    )
    assert response.status_code == HTTPStatus.OK
    assert response.headers['cache-control'] == 'no-store'
    assert captured['args'] == ('2026-01-08', 'excl', 2)
    body = response.json()
    assert body['term_start'] == '2026-01-08' and body['scope'] == 'excl' and body['min_activities'] == 2
    assert [d['dancer_id'] for d in body['dancers']] == ['DNC-AAAA1111', 'DNC-BBBB2222']
    assert body['dancers'][0]['enc_name'] == 'gAAAA-1'


def test_attendance_community_term_dancers_rejects_bad_term_start(auth_client):
    response = auth_client.get(
        '/attendance/community/term-dancers.json?term_start=not-a-date&scope=incl&min_activities=1'
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_attendance_community_term_dancers_requires_auth(client):
    response = client.get('/attendance/community/term-dancers.json?term_start=2026-01-08&scope=incl&min_activities=1')
    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_attendance_decrypt_params(auth_client, monkeypatch):
    monkeypatch.setattr(
        'esds_apps.main.analysis.decrypt_params', lambda: {'salt': 'ab12', 'sentinel': 'gAAAA-sentinel'}
    )
    response = auth_client.get('/attendance/decrypt-params')
    assert response.status_code == HTTPStatus.OK
    assert response.headers['cache-control'] == 'no-store'
    assert response.json() == {'salt': 'ab12', 'sentinel': 'gAAAA-sentinel'}


def test_attendance_decrypt_params_db_missing(auth_client, monkeypatch):
    def _raise():
        raise FileNotFoundError('no db')

    monkeypatch.setattr('esds_apps.main.analysis.decrypt_params', _raise)
    response = auth_client.get('/attendance/decrypt-params')
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert 'error' in response.json()


def test_attendance_decrypt_params_requires_auth(client):
    assert client.get('/attendance/decrypt-params').status_code == HTTPStatus.UNAUTHORIZED


def test_health_endpoint(client):
    assert client.get('/health').status_code == HTTPStatus.OK


def test_auth_login_redirects_to_google(client):
    response = client.get('/auth/login')
    assert response.status_code == HTTPStatus.FOUND
    assert 'accounts.google.com' in response.headers['location']


def test_auth_logout_clears_cookie(client):
    response = client.get('/auth/logout')
    assert response.status_code == HTTPStatus.FOUND
    assert response.headers['location'] == '/'
