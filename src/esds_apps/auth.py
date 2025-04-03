import logging
import re
from functools import wraps
from http import HTTPStatus
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from esds_apps import config
from esds_apps.simple_cache import SimpleCache

log = logging.getLogger(__name__)

SIGNER = TimestampSigner(secret_key=config.SECRETS['DC_API_TOKEN'])


def sanitize_for_filename(s: str) -> str:
    """Replace non-alphanumeric characters with underscores."""
    return re.sub(r'[^a-zA-Z0-9_.-]', '_', s)


def cookie_value(client_host: str) -> str:
    """Create a cookie that is tied to the host and the current time."""
    return SIGNER.sign(client_host.encode()).decode()


def is_cookie_valid(cookie_value: str, client_host: str) -> bool:
    """Verify the cookie belongs to the host and has not expired."""
    try:
        original = SIGNER.unsign(cookie_value, max_age=config.AUTH_COOKIE_TIMEOUT_SECONDS).decode()
        return original == client_host
    except (BadSignature, SignatureExpired):
        return False


def require_valid_cookie(request: Request) -> None:
    """Raise an error unless he user has a valid cookie.

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
            # The user has already logged, in forward onto the route
            log.debug(f'host {request.client.host} authenticated using a valid auth cookie.')
            response = await route_func(request, *args, **kwargs)

        elif request.method == 'GET':
            # The user is trying to login; show them the login form
            response = config.TEMPLATES.TemplateResponse(request, 'login.html', {'error': None})

        elif request.method == 'POST':
            # It's a login attempt
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
                # It's a failed login attempt

                # Check login retries
                past_auth_activity = past_activity_cache.read()

                if past_auth_activity is None:
                    # It's the first failed login attempt
                    past_auth_activity = {
                        'attempts_remaining': config.AUTH_MAX_LOGIN_ATTEMPTS - 1,
                    }
                else:
                    # It's not the first failed login attempt
                    past_auth_activity['attempts_remaining'] = max(0, past_auth_activity['attempts_remaining'] - 1)

                # Record that the user has attempted to login
                past_activity_cache.clear()
                past_activity_cache.write(past_auth_activity)
                log.warning(
                    f'failed login attempt from host {request.client.host}, '
                    f'{past_auth_activity["attempts_remaining"]} attempts remaining.'
                )

                if past_auth_activity['attempts_remaining'] == 0:
                    # The user has no retries remaining
                    response = config.TEMPLATES.TemplateResponse(
                        request,
                        'login.html',
                        {
                            'error': 'You have no further retries remaining. Please try again later.',
                            'disable_submission': True,
                        },
                    )
                else:
                    response = config.TEMPLATES.TemplateResponse(
                        request,
                        'login.html',
                        {
                            'error': f'Incorrect password, {past_auth_activity["attempts_remaining"]} '
                            'retries remaining.',
                            'disable_submission': past_auth_activity['attempts_remaining'] == 0,
                        },
                    )
        else:
            raise RuntimeError(f'User does not have a valid cookie and auth cannot handle the {request.method} method.')

        return response

    return wrapper
