from functools import wraps

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from esds_apps import config

SIGNER = TimestampSigner(secret_key=config.SECRETS['DC_API_TOKEN'])


def cookie_value(client_host: str) -> str:
    """Create a cookie that is tied to the host and the current time."""
    return SIGNER.sign(client_host.encode()).decode()


def is_cookie_valid(cookie_value: str, client_host: str) -> bool:
    """Verify the cookie belongs to the host and has not expired."""
    try:
        original = SIGNER.unsign(cookie_value, max_age=config.AUTH_COOKIE_TTL_SECONDS).decode()
        return original == client_host
    except (BadSignature, SignatureExpired):
        return False


def password_protected(route_func):
    """Prevent route access until a password has been provided.

    A simple auth method that can be easily shared between people.
    Once a person has logged in, they get a cookie allowing them to
    access other routes wrapped with this decorator without having to login.
    """

    @wraps(route_func)
    async def wrapper(request: Request, *args, **kwargs):
        # Check for valid cookie
        cookie = request.cookies.get(config.AUTH_COOKIE_NAME)
        client_host = request.client.host

        if cookie and is_cookie_valid(cookie, client_host):
            return await route_func(request, *args, **kwargs)

        # If it's a login attempt
        if request.method == 'POST':
            form = await request.form()
            if form.get('password') == config.SECRETS['UI_PASSWORD']:
                response = RedirectResponse(request.url.path, status_code=303)
                response.set_cookie(
                    config.AUTH_COOKIE_NAME,
                    cookie_value(client_host),
                    max_age=config.AUTH_COOKIE_TTL_SECONDS,
                    httponly=True,
                    secure=True,
                    samesite='Strict',
                )
                return response
            else:
                return config.TEMPLATES.TemplateResponse(
                    'login.html', {'request': request, 'error': 'Incorrect password'}
                )

        # Show login form
        return config.TEMPLATES.TemplateResponse('login.html', {'request': request, 'error': None})

    return wrapper
