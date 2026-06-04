from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from esds_apps.auth import (
    _safe_next_path,
    build_login_redirect,
    handle_oauth_callback,
    login_required,
    require_valid_cookie,
)

# ---------------------------------------------------------------------------
# _safe_next_path
# ---------------------------------------------------------------------------


def test_safe_next_path_local():
    assert _safe_next_path('/membership-cards') == '/membership-cards'


def test_safe_next_path_root():
    assert _safe_next_path('/') == '/'


def test_safe_next_path_empty():
    assert _safe_next_path('') == '/'


def test_safe_next_path_external_url():
    assert _safe_next_path('https://evil.com') == '/'


def test_safe_next_path_double_slash():
    assert _safe_next_path('//evil.com/steal') == '/'


# ---------------------------------------------------------------------------
# require_valid_cookie
# ---------------------------------------------------------------------------


@patch('esds_apps.auth._get_authenticated_email', return_value=None)
def test_require_valid_cookie_raises_when_not_authenticated(mock_auth):
    with pytest.raises(HTTPException) as exc:
        require_valid_cookie(MagicMock())
    assert exc.value.status_code == HTTPStatus.UNAUTHORIZED


@patch('esds_apps.auth._get_authenticated_email', return_value='user@example.com')
def test_require_valid_cookie_passes_when_authenticated(mock_auth):
    assert require_valid_cookie(MagicMock()) is None


# ---------------------------------------------------------------------------
# login_required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch('esds_apps.auth._get_authenticated_email', return_value=None)
async def test_login_required_redirects_get_to_login(mock_auth):
    request = MagicMock()
    request.method = 'GET'
    request.url.path = '/protected'

    @login_required
    async def route(request):
        return 'ok'

    result = await route(request)
    assert isinstance(result, RedirectResponse)
    assert '/auth/login' in result.headers['location']


@pytest.mark.asyncio
@patch('esds_apps.auth._get_authenticated_email', return_value=None)
async def test_login_required_raises_401_for_non_get(mock_auth):
    request = MagicMock()
    request.method = 'POST'

    @login_required
    async def route(request):
        return 'ok'

    with pytest.raises(HTTPException) as exc:
        await route(request)
    assert exc.value.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.asyncio
@patch('esds_apps.auth._get_authenticated_email', return_value='user@example.com')
async def test_login_required_calls_route_when_authenticated(mock_auth):
    @login_required
    async def route(request):
        return 'authenticated'

    assert await route(MagicMock()) == 'authenticated'


# ---------------------------------------------------------------------------
# build_login_redirect
# ---------------------------------------------------------------------------


def test_build_login_redirect_points_to_google():
    response = build_login_redirect('/next-path')
    assert isinstance(response, RedirectResponse)
    assert 'accounts.google.com' in response.headers['location']


def test_build_login_redirect_sets_state_cookie():
    response = build_login_redirect('/')
    cookies = response.headers.getlist('set-cookie')
    cookie_names = [c.split('=')[0] for c in cookies]
    assert 'oauth_state' in cookie_names


# ---------------------------------------------------------------------------
# _get_authenticated_email  (private but worth testing — drives all auth)
# ---------------------------------------------------------------------------


def test_get_authenticated_email_no_cookie():
    from esds_apps.auth import _get_authenticated_email

    request = MagicMock()
    request.cookies.get.return_value = None
    assert _get_authenticated_email(request) is None


def test_get_authenticated_email_invalid_cookie():
    from esds_apps.auth import _get_authenticated_email

    request = MagicMock()
    request.cookies.get.return_value = 'not-a-valid-signed-value'
    assert _get_authenticated_email(request) is None


def test_get_authenticated_email_valid_cookie():
    from esds_apps.auth import SERIALIZER, _get_authenticated_email

    email = 'user@example.com'
    cookie = SERIALIZER.dumps(email)
    request = MagicMock()
    request.cookies.get.return_value = cookie
    assert _get_authenticated_email(request) == email


# ---------------------------------------------------------------------------
# _set_session_cookie
# ---------------------------------------------------------------------------


def test_set_session_cookie_calls_set_cookie():
    from esds_apps.auth import _set_session_cookie

    response = MagicMock()
    _set_session_cookie(response, 'user@example.com')
    response.set_cookie.assert_called_once()


# ---------------------------------------------------------------------------
# handle_oauth_callback — early error exits (no HTTP calls needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_callback_google_error_param():
    request = MagicMock()
    request.query_params.get = lambda k, *_: 'access_denied' if k == 'error' else None
    with pytest.raises(HTTPException) as exc:
        await handle_oauth_callback(request)
    assert exc.value.status_code == HTTPStatus.FORBIDDEN
    assert 'access_denied' in exc.value.detail


@pytest.mark.asyncio
async def test_oauth_callback_bad_state():
    request = MagicMock()
    request.query_params.get = lambda k, *_: None if k == 'error' else 'some-code' if k == 'code' else 'wrong-state'
    request.cookies.get = lambda *_: 'expected-state'
    with pytest.raises(HTTPException) as exc:
        await handle_oauth_callback(request)
    assert exc.value.status_code == HTTPStatus.FORBIDDEN
    assert 'CSRF' in exc.value.detail
