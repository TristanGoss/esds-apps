import logging
from pathlib import Path

from dotenv import dotenv_values
from fastapi.templating import Jinja2Templates

LOGGING_LEVEL = logging.DEBUG

CACHE_ROOT = '/tmp/esds_cache'

SECRETS = dotenv_values('.env')
for var_name in ['DC_API_TOKEN', 'GMAIL_APP_PASSWORD', 'UI_PASSWORD']:
    if var_name not in SECRETS:
        raise RuntimeError(f'Environment variable {var_name} is missing from the .env file.')

AUTH_COOKIE_NAME = 'esds_apps_auth'
AUTH_COOKIE_TIMEOUT_SECONDS = 24 * 60 * 60
AUTH_MAX_LOGIN_ATTEMPTS = 10
AUTH_MAX_LOGIN_ATTEMPTS_TIMEOUT_S = 4 * 60 * 60

PUBLIC_DIR = directory = Path(__file__).resolve().parent.parent.parent / 'public'
TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parent.parent.parent / 'templates')

CARD_DPI = 300
A4_SCREEN_PX_PER_MM = 3.77953  # correct conversion for compositing svgs within an A4 html page
A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297

DC_API_PATH = 'api/v1'
DC_SERVER = 'https://esds-test.dancecloud.xyz'
DC_POLL_INTERVAL_S = 86400
DC_GET_HEADERS = {'Authorization': f'Bearer {SECRETS["DC_API_TOKEN"]}', 'Accept': 'application/vnd.api+json'}
DC_PATCH_HEADERS = DC_GET_HEADERS.copy().update({'Content-Type': 'application/vnd.api+json'})

MAIL_NO_HTML_FALLBACK_MESSAGE = """
Welcome to Edinburgh Swing Dance Society!
This email contains your digital membership card.

If you're reading this text, please open this email
in a mail client that supports HTML.

If you'd like a physical card instead of or in addition
to this digital one, please contact info@esds.org.uk
to request a physical card.
"""
MAIL_SEND_INTERVAL_S = 1
