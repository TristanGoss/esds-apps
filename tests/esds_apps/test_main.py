import types
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.testclient import TestClient

from esds_apps.main import (
    app,
    card_scanning_log,
    create_and_or_return_wallet_pass_link,
    download_checks,
    landing_page,
    proxy_card_check,
)


@pytest.fixture
def client():
    return TestClient(app)


def test_qr_codes_table_get(client, monkeypatch):
    # Patch qr_db.list_qr_codes to return dummy data
    dummy_codes = [{'code_id': 'abc123', 'target_url': 'https://example.com', 'description': 'desc', 'scan_count': 0}]
    monkeypatch.setattr('esds_apps.main.qr_db', types.SimpleNamespace(list_qr_codes=lambda: dummy_codes))
    response = client.get('/qr-codes')
    assert response.status_code == HTTPStatus.OK
    assert 'Tracked QR Codes' in response.text
    assert 'abc123' in response.text


def test_qr_codes_table_post_create(client, monkeypatch):
    # Patch qr_db.add_qr_code and list_qr_codes
    called = {}

    def fake_add_qr_code(code_id, target_url, description):
        called['added'] = (code_id, target_url, description)

    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            add_qr_code=fake_add_qr_code, list_qr_codes=lambda: [], delete_qr_code=lambda code_id: None
        ),
    )
    response = client.post('/qr-codes', data={'target_url': 'https://test.com', 'description': 'desc'})
    assert response.status_code == HTTPStatus.SEE_OTHER
    assert called['added'][1] == 'https://test.com'


def test_qr_codes_table_post_delete(client, monkeypatch):
    called = {}

    def fake_delete_qr_code(code_id):
        called['deleted'] = code_id

    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(
            delete_qr_code=fake_delete_qr_code, list_qr_codes=lambda: [], add_qr_code=lambda *a, **kw: None
        ),
    )
    response = client.post('/qr-codes', data={'delete_code_id': 'abc123'})
    assert response.status_code == HTTPStatus.SEE_OTHER
    assert called['deleted'] == 'abc123'


def test_serve_tracked_qr_code_svg(client, monkeypatch):
    # Patch qr_db.get_qr_code and segno.make
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
    response = client.get('/qr-codes/abc123/qr.svg')
    assert response.status_code == HTTPStatus.OK
    assert b'dummy' in response.content
    assert response.headers['content-type'] == 'image/svg+xml'


def test_serve_tracked_qr_code_png(client, monkeypatch):
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
    response = client.get('/qr-codes/abc123/qr.png')
    assert response.status_code == HTTPStatus.OK
    assert b'PNGDATA' in response.content
    assert response.headers['content-type'] == 'image/png'


def test_serve_tracked_qr_code_not_found(client, monkeypatch):
    monkeypatch.setattr('esds_apps.main.qr_db', types.SimpleNamespace(get_qr_code=lambda code_id: None))
    response = client.get('/qr-codes/abc123/qr.svg')
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
    response = client.get('/qr-codes/abc123/scan')
    assert response.status_code == HTTPStatus.FOUND
    assert response.headers['location'] == 'https://example.com'
    assert called['incremented'] == 'abc123'


def test_tracked_qr_scan_not_found(client, monkeypatch):
    monkeypatch.setattr(
        'esds_apps.main.qr_db',
        types.SimpleNamespace(get_qr_code=lambda code_id: None, increment_scan=lambda code_id: None),
    )
    response = client.get('/qr-codes/abc123/scan')
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
@patch('esds_apps.main.config.TEMPLATES.TemplateResponse')
async def test_landing_page(mock_template):
    request = MagicMock()
    await landing_page(request)
    mock_template.assert_called_once_with(request, 'landing.html')


@pytest.mark.asyncio
@patch('esds_apps.main.httpx.AsyncClient.get')
async def test_proxy_card_check_valid_url(mock_get):
    mock_get.return_value = MagicMock(content=b'abc', status_code=HTTPStatus.OK, headers={'content-type': 'text/plain'})
    result = await proxy_card_check(url='https://esds-test.dancecloud.xyz/dummy')
    assert isinstance(result, Response)
    assert result.body == b'abc'


@pytest.mark.asyncio
async def test_proxy_card_check_invalid_url():
    result = await proxy_card_check(url='https://malicious.com')
    assert isinstance(result, str)
    assert 'has not been proxied' in result


@pytest.mark.asyncio
@patch('esds_apps.main.fetch_membership_card_checks')
@patch('esds_apps.main.config.TEMPLATES.TemplateResponse')
async def test_card_scanning_log(mock_template, mock_fetch, sample_check):
    request = MagicMock()
    mock_fetch.return_value = [sample_check]
    await card_scanning_log(request)
    assert mock_template.called
    context = mock_template.call_args[0][2]
    assert 'checks' in context
    assert len(context['checks']) == 1


@pytest.mark.asyncio
@patch('esds_apps.main.fetch_membership_card_checks')
async def test_download_checks_csv(mock_fetch, sample_check):
    mock_fetch.return_value = [sample_check]
    response = await download_checks(days_ago=10)
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
