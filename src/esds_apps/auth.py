import logging
import re
from functools import wraps
from http import HTTPStatus
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from esds_apps import config
from esds_apps.simple_cache import SimpleCache

log = logging.getLogger(__name__)

SERIALIZER = URLSafeTimedSerializer(secret_key=config.SECRETS['COOKIE_SECRET'], salt='session')


def sanitize_for_filename(s: str) -> str:
    """Replace non-alphanumeric characters with underscores."""
    return re.sub(r'[^a-zA-Z0-9_.-]', '_', s)


def cookie_value(client_host: str) -> str:
    """Create a signed, opaque cookie tied to the client host."""
    return SERIALIZER.dumps(client_host)


def is_cookie_valid(cookie_val: str, client_host: str) -> bool:
    """Verify the cookie belongs to the host and has not expired."""
    try:
        original = SERIALIZER.loads(cookie_val, max_age=config.AUTH_COOKIE_TIMEOUT_SECONDS)
        return original == client_host
    except (BadSignature, SignatureExpired):
        return False


def require_valid_cookie(request: Request) -> None:
    """Raise an error unless the user has a valid cookie.

    A cut-down alternative to the password_protected wrapper below,
    for use with Depends()
    """
    # Check for valid cookie
    cookie = request.cookies.get(config.AUTH_COOKIE_NAME)
    client_host = request.client.host

    if not cookie or not is_cookie_valid(cookie, client_host):
        log.warning(f'host {client_host} failed to authenticate with their cookie.')
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail='This route requires an authentication cookie, but either you do not have one or it is invalid.',
        )


def _is_request_over_https(request: Request) -> bool:
    """Return True if the request arrived via HTTPS.

    Relies on the X-Forwarded-Proto header set by the nginx reverse proxy.
    Falls back to True when the header is absent (e.g. local development without nginx).
    """
    forwarded_proto = request.headers.get('X-Forwarded-Proto')
    if forwarded_proto is None:
        return True
    return forwarded_proto == 'https'


def _failed_login_response(request: Request, past_activity_cache: SimpleCache) -> Response:
    """Build the response for a failed login attempt, updating the retry cache."""
    raw = past_activity_cache.read()

    if (
        isinstance(raw, dict)
        and isinstance(raw.get('attempts_remaining'), int)
        and 0 <= raw['attempts_remaining'] <= config.AUTH_MAX_LOGIN_ATTEMPTS
    ):
        past_auth_activity = raw
    else:
        past_auth_activity = None

    if past_auth_activity is None:
        past_auth_activity = {'attempts_remaining': config.AUTH_MAX_LOGIN_ATTEMPTS - 1}
    else:
        past_auth_activity['attempts_remaining'] = max(0, past_auth_activity['attempts_remaining'] - 1)

    past_activity_cache.clear()
    past_activity_cache.write(past_auth_activity)
    log.warning(
        f'failed login attempt from host {request.client.host}, '
        f'{past_auth_activity["attempts_remaining"]} attempts remaining.'
    )

    out_of_retries = past_auth_activity['attempts_remaining'] == 0
    error_msg = (
        'You have no further retries remaining. Please try again later.'
        if out_of_retries
        else f'Incorrect password, {past_auth_activity["attempts_remaining"]} retries remaining.'
    )
    return config.TEMPLATES.TemplateResponse(
        request,
        'login.html',
        {'error': error_msg, 'disable_submission': out_of_retries},
    )


def password_auth(route_func: Callable) -> Callable:
    """Prevent route access until a password has been provided.

    A simple auth method that can be easily shared between people.
    Once a person has logged in, they get a cookie allowing them to
    access other routes wrapped with this decorator without having to login.

    Remember, all routes decorated with this must support both GET and POST methods,
    and must have the request as their first argument!
    """

    @wraps(route_func)
    async def wrapper(request: Request, *args, **kwargs):
        # Check for valid cookie
        cookie = request.cookies.get(config.AUTH_COOKIE_NAME)
        client_host = request.client.host

        # Load user activity cache
        past_activity_cache = SimpleCache(
            sanitize_for_filename(request.client.host) + '_auth_activity',
            max_age_s=config.AUTH_MAX_LOGIN_ATTEMPTS_TIMEOUT_S,
        )

        if cookie and is_cookie_valid(cookie, client_host):
            # The user has already logged in, forward onto the route
            log.debug(f'host {request.client.host} authenticated using a valid auth cookie.')
            response = await route_func(request, *args, **kwargs)

        elif request.method == 'GET':
            # The user is trying to login; show them the login form
            response = config.TEMPLATES.TemplateResponse(request, 'login.html', {'error': None})

        elif request.method == 'POST':
            # It's a login attempt
            if not _is_request_over_https(request):
                raise HTTPException(
                    status_code=HTTPStatus.FORBIDDEN,
                    detail='Login is only permitted over HTTPS.',
                )

            form = await request.form()
            if form.get('password') == config.SECRETS['UI_PASSWORD']:
                # It's a successful login attempt
                past_activity_cache.clear()
                log.debug(f'host {request.client.host} succesfully logged in.')
                response = RedirectResponse(request.url.path, status_code=303)
                response.set_cookie(
                    config.AUTH_COOKIE_NAME,
                    cookie_value(client_host),
                    max_age=config.AUTH_COOKIE_TIMEOUT_SECONDS,
                    httponly=True,
                    secure=True,
                    samesite='Strict',
                )
                return response
            else:
                response = _failed_login_response(request, past_activity_cache)
        else:
            raise RuntimeError(f'User does not have a valid cookie and auth cannot handle the {request.method} method.')

        return response

    return wrapper
