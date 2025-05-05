from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from esds_apps.main import (
    card_scanning_log,
    create_and_or_return_wallet_pass_link,
    download_checks,
    landing_page,
    proxy_card_check,
)


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
