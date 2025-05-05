from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from esds_apps.auth import cookie_value, is_cookie_valid, password_auth, require_valid_cookie, sanitize_for_filename


def test_sanitize_for_filename():
    assert sanitize_for_filename('user@host:123') == 'user_host_123'
    assert sanitize_for_filename('safe_filename.ext') == 'safe_filename.ext'


@patch('esds_apps.auth.config.SECRETS', {'DC_API_TOKEN': 'testsecret'})
def test_cookie_value_and_validation():
    # rebind SIGNER with patched config
    from esds_apps.auth import SIGNER  # noqa: F401

    host = '127.0.0.1'
    cookie = cookie_value(host)
    assert is_cookie_valid(cookie, host) is True
    assert is_cookie_valid(cookie, 'wrong-host') is False


@patch('esds_apps.auth.is_cookie_valid', return_value=False)
def test_require_valid_cookie_raises(mock_valid):
    request = MagicMock()
    request.cookies.get.return_value = 'bad_cookie'
    request.client.host = '127.0.0.1'

    with pytest.raises(HTTPException) as exc:
        require_valid_cookie(request)

    assert exc.value.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.asyncio
@patch('esds_apps.auth.is_cookie_valid', return_value=True)
@patch('esds_apps.auth.SimpleCache')
async def test_password_auth_valid_cookie(mock_cache, mock_valid):
    request = MagicMock()
    request.cookies.get.return_value = 'valid_cookie'
    request.client.host = '127.0.0.1'

    @password_auth
    async def route(request):
        return 'authenticated'

    result = await route(request)
    assert result == 'authenticated'


@pytest.mark.asyncio
@patch('esds_apps.auth.SimpleCache')
@patch('esds_apps.auth.config.TEMPLATES.TemplateResponse', return_value='login_form')
async def test_password_auth_get_shows_login(mock_template, mock_cache):
    request = MagicMock()
    request.cookies.get.return_value = None
    request.method = 'GET'
    request.client.host = '127.0.0.1'

    @password_auth
    async def dummy_route(request): ...

    result = await dummy_route(request)
    assert result == 'login_form'
    mock_template.assert_called_once()


@pytest.mark.asyncio
@patch('esds_apps.auth.config.SECRETS', {'UI_PASSWORD': 'hunter2', 'DC_API_TOKEN': 'testsecret'})
@patch('esds_apps.auth.SimpleCache')
async def test_password_auth_post_success(mock_cache):
    request = MagicMock()
    request.cookies.get.return_value = None
    request.method = 'POST'
    request.client.host = '127.0.0.1'
    request.url.path = '/secret'
    request.form = AsyncMock(return_value={'password': 'hunter2'})

    @password_auth
    async def dummy_route(request): ...

    result = await dummy_route(request)
    assert isinstance(result, RedirectResponse)
    assert result.status_code == HTTPStatus.SEE_OTHER
    assert result.headers['location'] == '/secret'


@pytest.mark.asyncio
@patch('esds_apps.auth.config.SECRETS', {'UI_PASSWORD': 'hunter2', 'DC_API_TOKEN': 'testsecret'})
@patch('esds_apps.auth.config.TEMPLATES.TemplateResponse', return_value='login_failed')
@patch('esds_apps.auth.SimpleCache')
async def test_password_auth_post_failure(mock_cache_class, mock_template):
    mock_cache = MagicMock()
    mock_cache.read.return_value = None
    mock_cache_class.return_value = mock_cache

    request = MagicMock()
    request.cookies.get.return_value = None
    request.method = 'POST'
    request.client.host = '127.0.0.1'
    request.form = AsyncMock(return_value={'password': 'fisher4'})

    @password_auth
    async def dummy_route(request): ...

    result = await dummy_route(request)
    assert result == 'login_failed'
    mock_template.assert_called_once()
