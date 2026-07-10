import asyncio
import csv
import io
import logging
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import httpx
import pytz
import segno
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from esds_apps import config
from esds_apps.attendance import analysis
from esds_apps.auth import build_login_redirect, handle_oauth_callback, login_required, require_valid_cookie
from esds_apps.classes import MembershipCardStatus, PrintablePdfError
from esds_apps.dancecloud_interface import (
    add_pos_permissions,
    fetch_membership_card_checks,
    fetch_membership_cards,
    fetch_pos_permissions,
    reissue_membership_card,
    remove_pos_permissions,
    set_membership_card_status,
)
from esds_apps.membership_cards import auto_issue_unissued_cards, generate_card_front_png, printable_pdf
from esds_apps.pass2u_interface import (
    MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE,
    create_wallet_pass,
    void_wallet_pass_if_exists,
)
from esds_apps.qr_code_db import QRCodeDB

qr_db = QRCodeDB()

logging.basicConfig(
    level=config.LOGGING_LEVEL,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_SAFE_FILENAME_RE = re.compile(r'[^\w\-.]')

# Responses carrying name ciphertext (or the salt/sentinel used to decrypt it) must not be cached,
# so nothing that could be decrypted to PII survives in the browser cache after the tab is closed.
_NO_STORE = {'Cache-Control': 'no-store'}


def _safe_filename(name: str) -> str:
    """Strip anything that isn't alphanumeric, hyphen, or dot from a filename segment."""
    return _SAFE_FILENAME_RE.sub('_', name) or 'download'


def _is_safe_url(url: str) -> bool:
    """Return True only if the URL has an http or https scheme."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:
        return False


def _attendance_activity_rows() -> list[dict]:
    """Read the per-activity attendance summary from the offline attendance database.

    One row per activity: its date, the event and activity it belongs to, and the headcounts
    the /attendance scatter plots. Read-only — only SELECTs run here — so it never disturbs the
    database the notebook rebuilds. Raises FileNotFoundError if that database hasn't been built.
    """
    if not Path(config.ATTENDANCE_DB_PATH).exists():
        raise FileNotFoundError(config.ATTENDANCE_DB_PATH)

    conn = sqlite3.connect(config.ATTENDANCE_DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT av.activity_id,
                   av.date,
                   e.name   AS event_name,
                   av.event_type,
                   act.name AS activity_name,
                   av.activity_type,
                   av.difficulty,
                   av.named_total,
                   av.aggregate_total,
                   av.total,
                   av.named_unknown,
                   av.named_registered
            FROM activity_attendance av
            JOIN event e    ON e.event_id      = av.event_id
            JOIN activity act ON act.activity_id = av.activity_id
            ORDER BY av.date
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


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
        config.DC_HOST,
    ],
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'DELETE'],
    allow_headers=['Content-Type', 'Authorization'],
)


@app.exception_handler(Exception)
async def internal_server_error_handler(request: Request, exc: Exception):
    log.exception(f'Unhandled exception on {request.method} {request.url}: {exc}')
    return config.TEMPLATES.TemplateResponse(request, '500.html', status_code=500)


@app.get('/', response_class=HTMLResponse)
async def landing_page(request: Request):
    return config.TEMPLATES.TemplateResponse(request, 'landing.html')


@app.get('/attendance', response_class=HTMLResponse)
@login_required
async def attendance_overview(request: Request):
    return config.TEMPLATES.TemplateResponse(request, 'attendance.html')


@app.get('/attendance/activities.json')
async def attendance_activities(request: Request, _: None = Depends(require_valid_cookie)):
    """Serve the all-activities scatter data as JSON for the client-side Plotly chart.

    Guarded by the cookie dependency (not the redirecting login_required) so an unauthenticated
    fetch gets a clean 401 rather than a 302 into Google's OAuth flow.
    """
    try:
        return JSONResponse({'activities': _attendance_activity_rows()})
    except FileNotFoundError:
        log.warning('Attendance database not found at %s', config.ATTENDANCE_DB_PATH)
        return JSONResponse(
            {'error': 'The attendance database has not been built yet.'},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )


@app.get('/attendance/summaries.json')
async def attendance_summaries(request: Request, _: None = Depends(require_valid_cookie)):
    """Serve the per-term summary-chart datasets (beginner intake, Level 2 + social, retention).

    Sent ``no-store`` because the retention dataset carries teacher-name ciphertext for the
    browser to decrypt: nothing derived from it should linger in the disk cache.
    """
    try:
        return JSONResponse(analysis.summaries(), headers=_NO_STORE)
    except FileNotFoundError:
        log.warning('Attendance database not found at %s', config.ATTENDANCE_DB_PATH)
        return JSONResponse(
            {'error': 'The attendance database has not been built yet.'},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )


@app.get('/attendance/decrypt-params')
async def attendance_decrypt_params(request: Request, _: None = Depends(require_valid_cookie)):
    """The salt + sentinel the browser needs to derive the decryption key and check the passphrase.

    Neither value is secret; the passphrase and the key derived from it never leave the browser.
    """
    try:
        return JSONResponse(analysis.decrypt_params(), headers=_NO_STORE)
    except FileNotFoundError:
        log.warning('Attendance database not found at %s', config.ATTENDANCE_DB_PATH)
        return JSONResponse(
            {'error': 'The attendance database has not been built yet.'},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )


@app.get('/attendance/activity/{activity_id}/records.json')
async def attendance_activity_records(request: Request, activity_id: int, _: None = Depends(require_valid_cookie)):
    """Every record for one activity as ciphertext JSON: named attendees, head-counts, and context.

    Named rows carry ``enc_name`` ciphertext; the browser decrypts it (once the operator supplies
    the passphrase) and assembles the CSV with first/last name columns, so the server never emits
    plaintext names. Guarded by the cookie dependency so an unauthenticated request gets a clean
    401 rather than a redirect into Google's OAuth flow; ``no-store`` so the ciphertext isn't cached.
    """
    try:
        rows = analysis.activity_records(activity_id)
    except FileNotFoundError:
        log.warning('Attendance database not found at %s', config.ATTENDANCE_DB_PATH)
        return JSONResponse(
            {'error': 'The attendance database has not been built yet.'},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )
    return JSONResponse({'activity_id': activity_id, 'rows': rows}, headers=_NO_STORE)


@app.get('/attendance/community/dancers.json')
async def attendance_community_dancers(
    request: Request,
    scope: str = Query(pattern='^(incl|excl)$'),
    min_dates: int = Query(ge=1),
    _: None = Depends(require_valid_cookie),
):
    """Dancers who attended at least ``min_dates`` unique dates in 2026, as ciphertext JSON.

    Each row is ``{dancer_id, enc_name}``; the browser decrypts ``enc_name`` (once the operator
    supplies the passphrase) into the first/last name columns of the downloaded CSV. ``scope`` is
    'incl' or 'excl' for whether the 30th anniversary weekender counts. ``no-store`` so the
    ciphertext isn't cached.
    """
    try:
        dancers = analysis.community_2026_dancer_rows(scope, min_dates)
    except FileNotFoundError:
        log.warning('Attendance database not found at %s', config.ATTENDANCE_DB_PATH)
        return JSONResponse(
            {'error': 'The attendance database has not been built yet.'},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )
    return JSONResponse({'scope': scope, 'min_dates': min_dates, 'dancers': dancers}, headers=_NO_STORE)


@app.get('/attendance/community/term-dancers.json')
async def attendance_community_term_dancers(
    request: Request,
    term_start: str = Query(pattern=r'^\d{4}-\d{2}-\d{2}$'),
    scope: str = Query(pattern='^(incl|excl)$'),
    min_activities: int = Query(ge=1),
    _: None = Depends(require_valid_cookie),
):
    """Dancers who attended at least ``min_activities`` activities in one teaching term, as ciphertext.

    Backs the click-to-download on the termly active-community chart. ``term_start`` (YYYY-MM-DD)
    picks the term by its start date; ``scope`` is 'incl' or 'excl' for whether the 30th anniversary
    weekender counts. Each row is ``{dancer_id, enc_name}``; the browser decrypts ``enc_name`` into
    the CSV's name columns. ``no-store`` so the ciphertext isn't cached.
    """
    try:
        dancers = analysis.termly_active_dancer_rows(term_start, scope, min_activities)
    except FileNotFoundError:
        log.warning('Attendance database not found at %s', config.ATTENDANCE_DB_PATH)
        return JSONResponse(
            {'error': 'The attendance database has not been built yet.'},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )
    return JSONResponse(
        {'term_start': term_start, 'scope': scope, 'min_activities': min_activities, 'dancers': dancers},
        headers=_NO_STORE,
    )


@app.api_route('/health', methods=['GET', 'HEAD'])
async def health():
    return {'status': 'ok'}


@app.get('/auth/login')
async def auth_login(request: Request, next: str = '/'):
    return build_login_redirect(next)


@app.get('/auth/callback')
async def auth_callback(request: Request):
    return await handle_oauth_callback(request)


@app.get('/auth/logout')
async def auth_logout():
    response = RedirectResponse('/', status_code=302)
    response.delete_cookie(config.AUTH_COOKIE_NAME)
    return response


@app.get('/membership-cards', response_class=HTMLResponse)
@login_required
async def membership_cards(request: Request):
    return config.TEMPLATES.TemplateResponse(
        request, 'membership_cards.html', {'cards': await fetch_membership_cards()}
    )


@app.get('/pos-permissions', response_class=HTMLResponse)
@login_required
async def pos_permissions(request: Request):
    return config.TEMPLATES.TemplateResponse(
        request, 'pos_permissions.html', {'volunteers': await fetch_pos_permissions()}
    )


@app.post('/pos-permissions/add', response_class=RedirectResponse)
async def add_volunteer(request: Request, _: None = Depends(require_valid_cookie)):
    # add POS permissions to a volunteer
    data = await request.json()
    email = data.get('volunteer_email', '')
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail='Invalid email address.')
    await add_pos_permissions(email)

    # Redirect back to the table view
    return RedirectResponse(url='/pos-permissions', status_code=303)


@app.post('/pos-permissions/{volunteer_uuid}/remove', response_class=RedirectResponse)
async def remove_volunteer(request: Request, volunteer_uuid: str, _: None = Depends(require_valid_cookie)):
    # remove POS permissions from a volunteer
    await remove_pos_permissions(volunteer_uuid)

    # Redirect back to the table view
    return RedirectResponse(url='/pos-permissions', status_code=303)


@app.get('/membership-cards/scanner', response_class=HTMLResponse)
async def scanner(request: Request):
    return config.TEMPLATES.TemplateResponse(request, 'rapid_scanner.html')


@app.get('/proxy-card-check')
async def proxy_card_check(url: str):
    """Prevent CORS from blocking access between the client and Dancecloud.

    Used together with the /membership-cards/scanner route.
    """
    if url.startswith(config.DC_HOST):
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type'))
    else:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail='URL not permitted.')


@app.get('/membership-cards/checks/logs', response_class=HTMLResponse)
@login_required
async def card_scanning_log(request: Request):
    card_checks = await fetch_membership_card_checks()
    checks_in_the_last_30_days = [
        check
        for check in card_checks
        if check.checked_at > datetime.now(pytz.timezone('Europe/London')) - timedelta(days=30)
    ]

    # for easy checking of how many members have turned up to a class / AGM,
    # report the number of unique cards scanned in the past hour.
    unique_card_uuids_scanned_in_the_last_hour = set()
    unique_checks_in_the_last_hour = []
    for check in checks_in_the_last_30_days:
        if (
            check.checked_at > datetime.now(pytz.timezone('Europe/London')) - timedelta(hours=1)
        ) and check.card_uuid not in unique_card_uuids_scanned_in_the_last_hour:
            unique_card_uuids_scanned_in_the_last_hour.add(check.card_uuid)
            unique_checks_in_the_last_hour.append(check)

    return config.TEMPLATES.TemplateResponse(
        request,
        'check_logs.html',
        {
            'num_unique_checks_in_last_hour': len(unique_checks_in_the_last_hour),
            'checks': sorted(
                checks_in_the_last_30_days,
                reverse=True,
                key=lambda x: x.checked_at,
            ),
        },
    )


@app.get('/membership-cards/checks/download', response_class=StreamingResponse)
@login_required
async def download_checks(request: Request, days_ago: int = Query(ge=0)):
    card_checks = await fetch_membership_card_checks()
    rows = sorted(
        [
            asdict(check)
            for check in card_checks
            if check.checked_at > datetime.now(pytz.timezone('Europe/London')) - timedelta(days=days_ago)
        ],
        reverse=True,
        key=lambda x: x['checked_at'],
    )

    # Prepare CSV output
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename=membership_card_checks.csv'},
    )


@app.post('/membership-cards/{card_uuid}/reissue', response_class=RedirectResponse)
async def reissue_card(
    request: Request,
    card_uuid: str,
    reason: MembershipCardStatus = Form(...),
    _: None = Depends(require_valid_cookie),
):
    # find the full details of the card that this UUID refers to
    # Why do this? Because we can only filter on card number,
    # but the dancecloud reissue route wants the card_uuid as an argument.
    card_to_void = [card for card in await fetch_membership_cards() if card.card_uuid == card_uuid][0]

    # void the associated wallet pass, if it exists
    await void_wallet_pass_if_exists(card_to_void)

    # reissue the card via dancecloud - this will cause the periodic check to pick it up and issue an email later on.
    await reissue_membership_card(card_uuid, reason)
    log.info(f'Reissued card with UUID {card_uuid} because it was {reason}')

    # Redirect back to the table view
    return RedirectResponse(url='/membership-cards', status_code=303)


@app.post('/membership-cards/{card_uuid}/cancel', response_class=RedirectResponse)
async def cancel_card(
    request: Request,
    card_uuid: str,
    _: None = Depends(require_valid_cookie),
):
    # find the full details of the card that this UUID refers to
    # Why do this? Because we can only filter on card number,
    # but the dancecloud reissue route wants the card_uuid as an argument.
    card_to_cancel = [card for card in await fetch_membership_cards() if card.card_uuid == card_uuid][0]

    # void the associated wallet pass, if it exists
    await void_wallet_pass_if_exists(card_to_cancel)

    # reissue the card via dancecloud - this will cause the periodic check to pick it up and issue an email later on.
    await set_membership_card_status(card_uuid, MembershipCardStatus.CANCELLED)
    log.info(f'Cancelled card with UUID {card_uuid}')

    # Redirect back to the table view
    return RedirectResponse(url='/membership-cards', status_code=303)


@app.get('/membership-cards/{card_number}/card-front.png', response_class=Response)
async def fetch_card_front(request: Request, card_number: int, _: None = Depends(require_valid_cookie)):
    # Remember this route uses the card_number because I don't think I can filter on card UUID!
    matching_cards = await fetch_membership_cards({'filter[number]': card_number})
    if len(matching_cards) != 1:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            f'when looking for card number {card_number}, '
            f'found {len(matching_cards)} card(s), but expected exactly one.',
        )

    return Response(content=generate_card_front_png(matching_cards[0]), media_type='image/png')


@app.get('/membership-cards/{card_uuid}/wallet-pass', response_class=RedirectResponse)
async def create_and_or_return_wallet_pass_link(request: Request, card_uuid: str):
    """Create a wallet pass if necessary and redirect the user to its url.

    The link that we distribute in the email labelled "Add to wallet" actually hits this route.
    This ensures that we only generate passes if the user actually clicks on the link
    AND they didn't already exist. This is important, because passes from pass2u cost money!

    However, that means the general public need to be able to hit this route,
    so it can't require a login cookie or trigger a password form.

    Ideally we would provide the card number because it prevents us from having to fetch
    every card in the membership scheme, but that number is "easy" to brute force,
    and this route doesn't have the protection of the others, so instead we're using the card_uuid,
    which is vastly harder to guess.
    """
    matching_cards = [card for card in await fetch_membership_cards() if card.card_uuid == card_uuid]
    if len(matching_cards) != 1:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            f'when looking for card {card_uuid}, found {len(matching_cards)} card(s), but expected exactly one.',
        )
    this_card = matching_cards[0]

    # check whether the card number already has an associated wallet pass id
    # remember that because the cache is JSON, the keys will always be strings.
    cache_content = MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.read()
    if (cache_content is not None) and (str(this_card.card_number) in cache_content):
        # we can generate and return the link directly - no need to create a new wallet pass.
        pass_id = cache_content[str(this_card.card_number)]
        log.debug(
            f'found existing wallet pass id {pass_id} for card number {this_card.card_number}, '
            'so returning that instead of creating one'
        )

    else:
        # we need to create a new wallet pass (this costs money,
        # which is why we only do it when people click on the link in the email)
        pass_id = await create_wallet_pass(this_card)
        log.debug(f'created a new wallet pass with id {pass_id} for card number {this_card.card_number}')

    # redirect the user to the pass2u page.
    return RedirectResponse(url=f'https://www.pass2u.net/d/{pass_id}', status_code=303)


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


# Management UI for tracked QR codes (with creation form)
@app.api_route('/qr-codes', methods=['GET', 'POST'], response_class=HTMLResponse)
@login_required
async def qr_codes_table(request: Request):
    error = None
    if request.method == 'POST':
        form = await request.form()
        # Handle deletion
        delete_code_id = form.get('delete_code_id')
        if delete_code_id:
            qr_db.delete_qr_code(delete_code_id)
            return RedirectResponse('/qr-codes', status_code=303)
        # Handle creation
        target_url = form.get('target_url', '').strip()
        description = form.get('description', '').strip()
        if not target_url:
            error = 'Please enter a target URL.'
        elif not _is_safe_url(target_url):
            error = 'Target URL must start with http:// or https://.'
        else:
            code_id = str(uuid.uuid4())[:8]
            qr_db.add_qr_code(code_id, target_url, description)
            return RedirectResponse('/qr-codes', status_code=303)
    qr_codes = qr_db.list_qr_codes()
    return config.TEMPLATES.TemplateResponse(
        'qr_codes.html',
        {
            'request': request,
            'qr_codes': qr_codes,
            'error': error,
        },
    )


# Endpoint to serve the QR code image (SVG or PNG)
@app.get('/qr-codes/{code_id}/qr.{fmt}')
@login_required
async def serve_tracked_qr_code(request: Request, code_id: str, fmt: str):
    qr_info = qr_db.get_qr_code(code_id)
    if not qr_info:
        return Response('QR code not found', status_code=404)
    qr_url = f'{config.BASE_URL}/s/{code_id}'
    qr = segno.make(qr_url)
    buf = io.BytesIO()
    # Use description for filename, fallback to code_id
    desc = qr_info.get('description') or code_id
    safe_desc = _safe_filename(desc)
    if fmt == 'svg':
        qr.save(buf, kind='svg', scale=4)
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type='image/svg+xml',
            headers={'Content-Disposition': f'attachment; filename="{safe_desc}.svg"'},
        )
    elif fmt == 'png':
        qr.save(buf, kind='png', scale=32)
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type='image/png',
            headers={'Content-Disposition': f'attachment; filename="{safe_desc}.png"'},
        )
    else:
        return Response('Invalid format', status_code=400)


# Redirection endpoint that counts scans - this is the URL the qr codes target
# We allow HEAD so Free Tier UptimeRobot can check the scan specifically.
@app.api_route('/s/{code_id}', methods=['GET', 'HEAD'])
async def tracked_qr_scan(code_id: str):
    qr_info = qr_db.get_qr_code(code_id)
    if not qr_info:
        return Response('QR code not found', status_code=404)
    target_url = qr_info['target_url']
    if not _is_safe_url(target_url):
        log.error(f'QR code {code_id} has an unsafe target_url in the database: {target_url!r}')
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail='Invalid redirect target.')
    qr_db.increment_scan(code_id)
    return RedirectResponse(target_url, status_code=302)


# Download scan datetimes as CSV for a QR code
@app.get('/qr-codes/{code_id}/scans.csv')
@login_required
async def download_qr_code_scans_csv(request: Request, code_id: str):
    qr_info = qr_db.get_qr_code(code_id)
    if not qr_info:
        return Response('QR code not found', status_code=404)
    scan_datetimes = qr_db.get_scan_datetimes(code_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['scanned_at_utc'])
    for dt in scan_datetimes:
        # Ensure ISO 8601 format (with timezone if present)
        if hasattr(dt, 'isoformat'):
            writer.writerow([dt.isoformat()])
        else:
            writer.writerow([str(dt)])
    buf.seek(0)
    desc = qr_info.get('description') or code_id
    safe_desc = _safe_filename(desc)
    return Response(
        content=buf.read(),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{safe_desc}_scans.csv"'},
    )
