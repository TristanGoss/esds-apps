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

SVG_NAMESPACE = 'http://www.w3.org/2000/svg'

CARD_DPI = 300
# Define layout as ISO/IEC 7810 ID-1 card
CARD_LAYOUT_WIDTH_MM = 85.6
CARD_LAYOUT_HEIGHT_MM = 53.98
CARD_LAYOUT_FRONT_TRANSFORM = 'translate(0, 0), scale(0.345)'
CARD_LAYOUT_BACK_TRANSFORM = 'translate(0, 0), scale(0.345)'
CARD_LAYOUT_QR_TRANSFORM = 'translate(5.58, 7.45), scale(0.7)'
CARD_LAYOUT_NAME_PARAMS = {'x': '4', 'y': '48.5', 'fill': '#00479E', 'font-size': '0.6mm', 'font-family': 'Futura'}
CARD_LAYOUT_CARD_NUMBER_PARAMS = {
    'x': '7',
    'y': '8.2',
    'fill': '#00479E',
    'font-size': '0.3mm',
    'font-family': 'Futura',
}
CARD_LAYOUT_EXPIRY_DATE_PARAMS = {
    'x': '76',
    'y': '33',
    'fill': '#00479E',
    'font-size': '0.3mm',
    'transform': 'rotate(-90 76 33)',
    'font-family': 'Futura',
}

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
