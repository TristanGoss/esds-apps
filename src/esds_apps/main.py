import asyncio
from contextlib import asynccontextmanager
import logging

from esds_apps.config import LOGGING_LEVEL
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from esds_apps.membership_cards import auto_issue_unissued_cards

logging.basicConfig(
    level=LOGGING_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
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
            log.debug("Dancecloud unissued card poller shutdown")


app = FastAPI(lifespan=lifespan_manager)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://dancecloud.com",
        "http://localhost:5173",  # for local development
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/hello_world")
def hello_world() -> str:
    return "Hello There!"
