import json
import logging
import secrets
import time
from functools import wraps
from http import HTTPStatus
from typing import Callable
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey
from authlib.jose import jwt as jose_jwt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from esds_apps import config

log = logging.getLogger(__name__)

SERIALIZER = URLSafeTimedSerializer(secret_key=config.SECRETS['COOKIE_SECRET'], salt='session')

_GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
_GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
_GOOGLE_CERTS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
_GOOGLE_ADMIN_API = 'https://admin.googleapis.com/admin/directory/v1'
_OAUTH_STATE_COOKIE = 'oauth_state'
_OAUTH_NEXT_COOKIE = 'oauth_next'


def _safe_next_path(path: str) -> str:
    """Return path only if it looks like a local path, else '/'."""
    if path and path.startswith('/') and not path.startswith('//'):
        return path
    return '/'


def _get_authenticated_email(request: Request) -> str | None:
    """Return the email in the session cookie if valid, else None."""
    cookie = request.cookies.get(config.AUTH_COOKIE_NAME)
    if not cookie:
        return None
    try:
        return SERIALIZER.loads(cookie, max_age=config.AUTH_COOKIE_TIMEOUT_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def _set_session_cookie(response, email: str) -> None:
    response.set_cookie(
        config.AUTH_COOKIE_NAME,
        SERIALIZER.dumps(email),
        max_age=config.AUTH_COOKIE_TIMEOUT_SECONDS,
        httponly=True,
        secure=True,
        samesite='Lax',
    )


def require_valid_cookie(request: Request) -> None:
    """FastAPI Depends() guard: raise 401 if the session cookie is missing or invalid."""
    if not _get_authenticated_email(request):
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail='Authentication required.')


def login_required(route_func: Callable) -> Callable:
    """Decorator: redirect to Google OAuth if the user has no valid session cookie."""

    @wraps(route_func)
    async def wrapper(request: Request, *args, **kwargs):
        if _get_authenticated_email(request):
            return await route_func(request, *args, **kwargs)
        if request.method == 'GET':
            return RedirectResponse(f'/auth/login?next={request.url.path}', status_code=302)
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail='Authentication required.')

    return wrapper


def build_login_redirect(next_path: str) -> RedirectResponse:
    """Build a redirect to Google OAuth, storing the CSRF state and next-path in cookies."""
    state = secrets.token_urlsafe(32)
    params = {
        'client_id': config.SECRETS['GOOGLE_CLIENT_ID'],
        'redirect_uri': config.SECRETS['GOOGLE_OAUTH_REDIRECT_URI'],
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    }

    response = RedirectResponse(f'{_GOOGLE_AUTH_URL}?{urlencode(params)}', status_code=302)
    response.set_cookie(_OAUTH_STATE_COOKIE, state, max_age=600, httponly=True, samesite='Lax', secure=True)
    response.set_cookie(_OAUTH_NEXT_COOKIE, next_path, max_age=600, httponly=True, samesite='Lax', secure=True)
    return response


async def handle_oauth_callback(request: Request) -> RedirectResponse:
    """Process the Google OAuth callback; set a session cookie and redirect on success."""
    error = request.query_params.get('error')
    if error:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=f'Google OAuth error: {error}')

    code = request.query_params.get('code')
    state = request.query_params.get('state')
    expected_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if not state or state != expected_state:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail='Invalid OAuth state — possible CSRF.')

    next_path = _safe_next_path(request.cookies.get(_OAUTH_NEXT_COOKIE, '/'))

    # Exchange the auth code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                'code': code,
                'client_id': config.SECRETS['GOOGLE_CLIENT_ID'],
                'client_secret': config.SECRETS['GOOGLE_CLIENT_SECRET'],
                'redirect_uri': config.SECRETS['GOOGLE_OAUTH_REDIRECT_URI'],
                'grant_type': 'authorization_code',
            },
        )
    token_resp.raise_for_status()
    id_token = token_resp.json()['id_token']

    # Verify the ID token using Google's public keys
    async with httpx.AsyncClient() as client:
        jwks_resp = await client.get(_GOOGLE_CERTS_URL)
    jwks = JsonWebKey.import_key_set(jwks_resp.json())
    claims = jose_jwt.decode(
        id_token,
        jwks,
        claims_options={
            'iss': {'essential': True, 'values': ['https://accounts.google.com', 'accounts.google.com']},
            'aud': {'essential': True, 'value': config.SECRETS['GOOGLE_CLIENT_ID']},
        },
    )
    claims.validate()
    email = claims['email']

    # Check the user is in the allowed group
    if not await _is_group_member(email):
        log.warning(f'{email} authenticated with Google but is not in the allowed group')
        return config.TEMPLATES.TemplateResponse(
            request,
            'login.html',
            {'error': f'{email} is not in the authorised group. Try signing in with a different account.'},
            status_code=403,
        )

    log.info(f'{email} logged in successfully')
    response = RedirectResponse(next_path, status_code=303)
    _set_session_cookie(response, email)
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    response.delete_cookie(_OAUTH_NEXT_COOKIE)
    return response


async def _get_service_account_token(scopes: list[str]) -> str:
    """Obtain a Bearer token for the service account via the JWT bearer grant flow."""
    with open(config.SECRETS['GOOGLE_SERVICE_ACCOUNT_FILE']) as f:
        sa_info = json.load(f)

    now = int(time.time())
    sa_claims = {
        'iss': sa_info['client_email'],
        'sub': config.SECRETS['GOOGLE_ADMIN_IMPERSONATE_EMAIL'],
        'scope': ' '.join(scopes),
        'aud': _GOOGLE_TOKEN_URL,
        'iat': now,
        'exp': now + 3600,
    }
    token = jose_jwt.encode({'alg': 'RS256'}, sa_claims, sa_info['private_key'].encode())
    assertion = token.decode() if isinstance(token, bytes) else token

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion},
        )
    resp.raise_for_status()
    return resp.json()['access_token']


async def _is_group_member(email: str) -> bool:
    """Return True if email is a member of the configured Google Workspace group."""
    group = config.SECRETS['GOOGLE_ALLOWED_GROUP_EMAIL']
    access_token = await _get_service_account_token(
        ['https://www.googleapis.com/auth/admin.directory.group.member.readonly']
    )
    url = f'{_GOOGLE_ADMIN_API}/groups/{group}/hasMember/{email}'
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={'Authorization': f'Bearer {access_token}'})
    if resp.status_code == HTTPStatus.OK:
        is_member = resp.json().get('isMember', False)
        if is_member:
            log.info(f'{email} is a member of {group}, access granted')
        return is_member
    log.warning(f'Group membership check for {email} returned {resp.status_code}: {resp.text}')
    return False
