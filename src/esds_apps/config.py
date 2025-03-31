import logging
import os
from pathlib import Path

LOGGING_LEVEL = logging.DEBUG

ASSETS_PATH = directory=Path(__file__).parent.parent.parent / "assets"

CARD_DPI = 300

DC_API_PATH = 'api/v1'
DC_SERVER = 'https://esds-test.dancecloud.xyz'
DC_POLL_INTERVAL_S = 86400
DC_GET_HEADERS = {
    'Authorization': f'Bearer {os.environ['DC_API_TOKEN']}',
    'Accept': 'application/vnd.api+json'}
DC_PATCH_HEADERS = DC_GET_HEADERS.copy().update({
    'Content-Type': 'application/vnd.api+json'})

GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']
MAIL_NO_HTML_FALLBACK_MESSAGE = """
Welcome to Edinburgh Swing Dance Society!
This email contains your digital membership card.

If you're reading this text, please open this email
in a mail client that supports HTML.

If you'd like a physical card instead of or in addition
to this digital one, please contact info@esds.org.uk
to request a physical card.
"""

MAIL_HTML_TEMPLATE = f"""
<html>
  <body>
    <h2>Welcome to the Edinburgh Swing Dance Society!</h2>
    <p>Here is your QR code:</p>
    <img src="cid:qr_code_cid" alt="QR Code" />
    <p>
      <a href="https://yourdomain.com/card.pkpass">
        <button style="padding:10px;font-size:16px;">Add to Apple Wallet</button>
      </a>
    </p>
  </body>
</html>
"""
