import logging
from pathlib import Path

from dotenv import dotenv_values
from fastapi.templating import Jinja2Templates

LOGGING_LEVEL = logging.DEBUG

CACHE_ROOT = '/tmp/esds_cache'
BASE_URL = 'https://apps.esds.org.uk'

SECRETS = dotenv_values('.env')
for var_name in ['DC_API_TOKEN', 'GMAIL_APP_PASSWORD', 'UI_PASSWORD', 'PASS2U_API_KEY', 'DOOR_VOLUNTEERS_TEAM_ID']:
    if var_name not in SECRETS:
        raise RuntimeError(f'Environment variable {var_name} is missing from the .env file.')

IS_CARD_DISTRIBUTION_ENABLED = False
CARD_DISTRIBUTION_EMAIL_BATCH_SIZE = 20
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
CARD_LAYOUT_QR_ERROR_CORRECTION = 'M'
CARD_LAYOUT_QR_CODE_WIDTH_MM = 25  # includes 4-symbols-either-side safe zone
CARD_LAYOUT_QR_CODE_TRANSFORM = 'translate(8, 9)'
# Do not use units in definitions as renderers will assume 96 DPI and ignore the SVG width and height!
# Instead, use floating point numbers only!
# 1pt is 0.3528mm, so 18pt is 6.3504mm, so 18pt font has size 6.3504 below
CARD_LAYOUT_FIRST_NAME_PARAMS = {
    'x': '4',
    'y': '41',
    'fill': '#00479E',
    'font-size': '6.35',
    'font-family': 'Futura, sans-serif',
}
CARD_LAYOUT_FIRST_NAME_MAX_LENGTH = 18  # test this by rendering with the average case name "Anamericalindesontraviel"
CARD_LAYOUT_LAST_NAME_PARAMS = {
    'x': '4',
    'y': '48',
    'fill': '#00479E',
    'font-size': '6.35',
    'font-family': 'Futura, sans-serif',
}
CARD_LAYOUT_LAST_NAME_MAX_LENGTH = 20  # test this by rendering with "Anamericalindesontraviel"
CARD_LAYOUT_CARD_NUMBER_PARAMS = {
    'x': '78',
    'y': '5.5',
    'fill': '#00479E',
    'transform': 'rotate(90 78 5.5)',
    'font-size': '3.175',
    'font-family': 'Futura, sans-serif',
}
CARD_LAYOUT_EXPIRY_DATE_PARAMS = {
    'x': '8',
    'y': '8',
    'fill': '#00479E',
    'font-size': '3.175',
    'font-family': 'Futura Medium, sans-serif',
}

A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297

DC_API_PATH = 'api/v1'
DC_HOST = 'https://esds.dancecloud.com'
DC_POLL_INTERVAL_S = 60 * 60 * 24
DC_GET_HEADERS = {'Authorization': f'Bearer {SECRETS["DC_API_TOKEN"]}', 'Accept': 'application/vnd.api+json'}
DC_PATCH_HEADERS = {**DC_GET_HEADERS, 'Content-Type': 'application/vnd.api+json'}
DC_POST_HEADERS = {**DC_GET_HEADERS, 'Content-Type': 'application/json'}

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

# Pass2u.net is used for Apple Wallet and Google Wallet integration only
PASS2U_MODEL_ID = 311534
PASS2U_API_PATH = 'v2'
PASS2U_HOST = 'https://api.pass2u.net'

FOREVER_CACHE_TIMEOUT_S = 9999 * 365.25 * 24 * 60 * 60
