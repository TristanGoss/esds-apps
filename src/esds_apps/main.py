import asyncio
import logging
from contextlib import asynccontextmanager
from io import BytesIO
from typing import List

from fastapi import Depends, FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse

from esds_apps import config
from esds_apps.auth import password_auth, require_valid_cookie
from esds_apps.classes import MembershipCardStatus
from esds_apps.dancecloud_interface import fetch_membership_cards, reissue_membership_card
from esds_apps.membership_cards import auto_issue_unissued_cards

logging.basicConfig(
    level=config.LOGGING_LEVEL,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan_manager(_: FastAPI):
    """Create an async task that periodically issues unissued cards."""
    dc_poller = asyncio.create_task(auto_issue_unissued_cards())
    try:
        yield
    finally:
        dc_poller.cancel()
        try:
            await dc_poller
        except asyncio.CancelledError:
            log.debug('Dancecloud unissued card poller shutdown')


app = FastAPI(lifespan=lifespan_manager)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        'https://www.dancecloud.com',
        config.DC_SERVER,
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse('public/favicon.ico')


@app.get('/', response_class=HTMLResponse)
async def landing_page(request: Request):
    return config.TEMPLATES.TemplateResponse(request, 'landing.html')


@app.api_route('/membership-cards', methods=['GET', 'POST'], response_class=HTMLResponse)
@password_auth
async def membership_cards(request: Request):
    return config.TEMPLATES.TemplateResponse(request, 'membership_cards.html', {'cards': fetch_membership_cards()})


@app.post('/membership-cards/{card_uuid}/reissue', response_class=RedirectResponse)
def reissue_card(
    request: Request, card_uuid: str, reason: MembershipCardStatus = Form(...), _: None = Depends(require_valid_cookie)
):
    reissue_membership_card(card_uuid, reason)
    log.info(f'Reissued card with UUID {card_uuid} because it was {reason}')

    # Redirect back to the table view
    return RedirectResponse(url='/membership_cards', status_code=303)


@app.post('/membership-cards/download/pdf', response_class=StreamingResponse)
def download_selected_cards(
    request: Request, card_uuid: List[str] = Form(...), _: None = Depends(require_valid_cookie)
):
    # TODO: fill in printable PDF generation.
    buffer = BytesIO()
    buffer.write(b'%PDF-1.4\n...fake content...\n%%EOF')
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type='application/pdf',
        headers={'Content-Disposition': 'attachment; filename=esds_membership_cards.pdf'},
    )
