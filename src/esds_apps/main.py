import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from esds_apps import config
from esds_apps.auth import password_protected
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
@password_protected
async def membership_cards_status(request: Request):
    return 'Hello there!'
