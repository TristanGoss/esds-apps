import asyncio
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from io import BytesIO
from typing import List

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from esds_apps import config
from esds_apps.auth import password_auth, require_valid_cookie
from esds_apps.classes import MembershipCardStatus, PrintablePdfError
from esds_apps.dancecloud_interface import fetch_membership_cards, reissue_membership_card
from esds_apps.membership_cards import auto_issue_unissued_cards, generate_card_front_png, printable_pdf

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
app.mount('/public', StaticFiles(directory=config.PUBLIC_DIR), name='public')

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


@app.get('/', response_class=HTMLResponse)
async def landing_page(request: Request):
    return config.TEMPLATES.TemplateResponse(request, 'landing.html')


@app.api_route('/membership-cards', methods=['GET', 'POST'], response_class=HTMLResponse)
@password_auth
async def membership_cards(request: Request):
    return config.TEMPLATES.TemplateResponse(
        request, 'membership_cards.html', {'cards': await fetch_membership_cards()}
    )


@app.post('/membership-cards/{card_uuid}/reissue', response_class=RedirectResponse)
async def reissue_card(
    request: Request, card_uuid: str, reason: MembershipCardStatus = Form(...), _: None = Depends(require_valid_cookie)
):
    await reissue_membership_card(card_uuid, reason)
    log.info(f'Reissued card with UUID {card_uuid} because it was {reason}')

    # Redirect back to the table view
    return RedirectResponse(url='/membership_cards', status_code=303)


@app.get('/membership-cards/{card_number}/card-front.png', response_class=Response)
async def fetch_card_front(request: Request, card_number: int, _: None = Depends(require_valid_cookie)):
    # Remember this route alone uses the card_number because I don't think I can filter on card UUID!
    this_card = await fetch_membership_cards({'filter[number]': card_number})
    if len(this_card) != 1:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            f'which looking for card number {card_number}, found {len(this_card)} card(s), but expected exactly one.',
        )

    return Response(content=generate_card_front_png(this_card[0]), media_type='image/png')


@app.post('/membership-cards/print-layout/pdf', response_class=StreamingResponse)
async def download_selected_cards(  # noqa: PLR0913
    request: Request,
    card_width_mm: float = Form(...),
    card_height_mm: float = Form(...),
    margin_top_mm: float = Form(...),
    margin_left_mm: float = Form(...),
    horizontal_gap_mm: float = Form(...),
    vertical_gap_mm: float = Form(...),
    card_uuids: List[str] = Form(...),
    _: None = Depends(require_valid_cookie),
):
    try:
        pdf_bytes = await printable_pdf(
            request=request,
            card_width_mm=card_width_mm,
            card_height_mm=card_height_mm,
            margin_top_mm=margin_top_mm,
            margin_left_mm=margin_left_mm,
            horizontal_gap_mm=horizontal_gap_mm,
            vertical_gap_mm=vertical_gap_mm,
            card_uuids=card_uuids,
        )
        log.debug(f'Created a printable pdf for {len(card_uuids)} cards.')

    except PrintablePdfError as e:
        return config.TEMPLATES.TemplateResponse(request, 'pdf_card_error.html', {'message': str(e)}, status_code=400)

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type='application/pdf',
        headers={'Content-Disposition': 'attachment; filename=esds_membership_cards.pdf'},
    )
