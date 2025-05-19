import asyncio
import base64
import json
import logging
import os
import smtplib
from email.message import EmailMessage
from math import floor
from smtplib import SMTPResponseException
from time import sleep
from typing import List, Optional

import cairosvg
import segno
from fastapi import Request
from lxml import etree
from weasyprint import HTML

from esds_apps import config
from esds_apps.classes import MembershipCard, MembershipCardStatus, PrintablePdfError
from esds_apps.dancecloud_interface import fetch_membership_cards, set_membership_card_status

log = logging.getLogger(__name__)


def credit_card_svg() -> str:
    # Create the root SVG container
    svg = etree.Element('svg', nsmap={None: config.SVG_NAMESPACE})

    # Set basic properties
    svg.set('width', f'{config.CARD_LAYOUT_WIDTH_MM}mm')
    svg.set('height', f'{config.CARD_LAYOUT_HEIGHT_MM}mm')
    svg.set('viewBox', f'0 0 {config.CARD_LAYOUT_WIDTH_MM} {config.CARD_LAYOUT_HEIGHT_MM}')
    return svg


def generate_card_front_png(card: MembershipCard) -> bytes:
    combined_svg = credit_card_svg()

    # create a correctly scaled QR code
    # Remember QR codes have an ISO-mandated 4-module wide border on all sides, the "quiet zone"!
    # You can plot the quiet zone by providing light='#ff0000' as an argument to .svg_inline()
    qr = segno.make(card.check_url, error=config.CARD_LAYOUT_QR_ERROR_CORRECTION)
    qr_svg = qr.svg_inline(
        scale=config.CARD_LAYOUT_QR_CODE_WIDTH_MM / qr.symbol_size()[0],
    )

    # load the static svg template
    with open(config.PUBLIC_DIR / 'membership_card_front.svg', 'rb') as f:
        background = etree.parse(f)

    # Add the background
    g1 = etree.SubElement(combined_svg, 'g')
    for el in background.getroot():
        g1.append(el)

    # Add the QR code
    combined_svg.append(etree.fromstring(f'<g transform="{config.CARD_LAYOUT_QR_CODE_TRANSFORM}">{qr_svg}</g>'))

    # Add first name (truncated if necessary)
    text = etree.SubElement(combined_svg, 'text', config.CARD_LAYOUT_FIRST_NAME_PARAMS)
    text.text = card.first_name[: config.CARD_LAYOUT_FIRST_NAME_MAX_LENGTH]

    # Add last name (truncated if necessary)
    text = etree.SubElement(combined_svg, 'text', config.CARD_LAYOUT_LAST_NAME_PARAMS)
    text.text = card.last_name[: config.CARD_LAYOUT_LAST_NAME_MAX_LENGTH]

    # Add membership card number (this is a fixed 6 characters)
    text = etree.SubElement(combined_svg, 'text', config.CARD_LAYOUT_CARD_NUMBER_PARAMS)
    text.text = f'CRD: {card.card_number:06}'

    # Add expiry date (this is also fixed width)
    text = etree.SubElement(combined_svg, 'text', config.CARD_LAYOUT_EXPIRY_DATE_PARAMS)
    text.text = 'EXP: ' + card.expires_at.strftime('%d/%m/%Y')

    return cairosvg.svg2png(
        bytestring=etree.tostring(combined_svg, pretty_print=True, xml_declaration=True, encoding='UTF-8'),
        dpi=config.CARD_DPI,
        background_color='white',
    )


def generate_card_back_png() -> bytes:
    combined_svg = credit_card_svg()

    # load the static svg template
    with open(config.PUBLIC_DIR / 'membership_card_back.svg', 'rb') as f:
        background = etree.parse(f)

    # Add the background
    g1 = etree.SubElement(combined_svg, 'g')
    for el in background.getroot():
        g1.append(el)

    return cairosvg.svg2png(
        bytestring=etree.tostring(combined_svg, pretty_print=True, xml_declaration=True, encoding='UTF-8'),
        dpi=config.CARD_DPI,
        background_color='white',
    )


async def auto_issue_unissued_cards() -> None:
    log.debug('Dancecloud unissued cards poller started.')
    while True:
        log.info('Dancecloud unissued cards poller awoken.')
        new_cards = await fetch_membership_cards({'filter[status]': 'new'})
        log.info(f'found {len(new_cards)} new cards to issue.')

        if config.IS_CARD_DISTRIBUTION_ENABLED:
            # send emails in batches to reduce damage if a non-SMTP error is thrown.
            for i in range(0, len(new_cards), config.CARD_DISTRIBUTION_EMAIL_BATCH_SIZE):
                card_batch = new_cards[i : i + config.CARD_DISTRIBUTION_EMAIL_BATCH_SIZE]
                emails = [await compose_membership_email(card) for card in card_batch]
                log.info(f'composed {len(emails)} membership emails.')

                succesfully_delivered = send_emails(emails)
                log.info(f'succesfully sent {sum(succesfully_delivered)} emails.')

                for delivered, card in zip(succesfully_delivered, card_batch):
                    if delivered:
                        await set_membership_card_status(card.card_uuid, MembershipCardStatus.ISSUED)
        else:
            log.info(
                f'did not issue any cards as IS_CARD_DISTRIBUTION_ENABLED is set to '
                f'{config.IS_CARD_DISTRIBUTION_ENABLED}'
            )
        log.info('Dancecloud unissued cards poller returning to sleep.')
        await asyncio.sleep(config.DC_POLL_INTERVAL_S)


async def compose_membership_email(card: MembershipCard) -> EmailMessage:
    msg = EmailMessage()
    msg['Subject'] = 'Your ESDS Membership'
    msg['From'] = 'info@esds.org.uk'
    msg['To'] = f'{card.email}'
    msg.set_content("Here's your code!")

    # Add plain text fallback content.
    msg.set_content(config.MAIL_NO_HTML_FALLBACK_MESSAGE)

    # Add HTML version.
    # *yes*, both wallet urls are *supposed* to be the same!
    # They both hit the server, which then creates a new wallet pass via pass2u if one does not already exist.
    # We only want to create these passes once someone actually clicks on the button, or we'll waste money.
    msg.add_alternative(
        config.TEMPLATES.env.get_template('new_membership_email.html').render(
            {
                'first_name': card.first_name,
                'apple_wallet_url': f'https://apps.esds.org.uk/membership-cards/{card.card_uuid}/wallet-pass',
                'google_wallet_url': f'https://apps.esds.org.uk/membership-cards/{card.card_uuid}/wallet-pass',
            }
        ),
        subtype='html',
    )
    msg_html_part = msg.get_payload()[-1]

    qr_code_png = generate_card_front_png(card)

    # Attach card face inline using Content ID.
    msg_html_part.add_related(qr_code_png, maintype='image', subtype='png', cid='membership_card_cid')

    # Add it as an attachment as well.
    msg.add_attachment(qr_code_png, maintype='image', subtype='png', filename=f'membership_card_{card.card_number}.png')

    # Load the image to Content ID map
    with open(config.PUBLIC_DIR / 'new_membership_email_image_to_cid_map.json') as fh:
        image_to_cid_map = json.load(fh)

    # Embed the images into the email.
    # We do this so that reading the email does not rely on the server being up.
    for entry in image_to_cid_map:
        with open(config.PUBLIC_DIR / entry['image_path'], 'rb') as f:
            msg_html_part.add_related(
                f.read(),
                maintype='image',
                subtype=os.path.splitext(entry['image_path'])[1][1:],  # e.g., 'png'
                cid=entry['cid'],
            )
    return msg


def send_emails(emails: List[EmailMessage]) -> List[bool]:
    was_email_succesfully_delivered = [False] * len(emails)
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login('info@esds.org.uk', config.SECRETS['GMAIL_APP_PASSWORD'])
        for i, email in enumerate(emails):
            try:
                log.debug(f'About to send email to {email["To"]}')
                smtp.send_message(email)
                was_email_succesfully_delivered[i] = True
                log.debug(f'Succesfully sent email to {email["To"]}')
            except SMTPResponseException as e:
                log.error(
                    f'Email was not delivered; SMTP error: {e.smtp_code} - {e.smtp_error.decode(errors="ignore")}'
                )
            sleep(config.MAIL_SEND_INTERVAL_S)

    return was_email_succesfully_delivered


def mirror_page(page: list[str], cards_per_row: int, cards_per_page: int) -> List[Optional[str]]:
    """Return a page of base64 encoded card pngs, but flipped along the long axis.

    This, together with a css adjustment in the printing template,
    allows us to correctly position the card backs with respect to the card fronts.
    """
    # Pad with None
    padded = page + [None] * (cards_per_page - len(page))

    # Group into rows
    rows = [padded[i : i + cards_per_row] for i in range(0, cards_per_page, cards_per_row)]

    # Mirror each row
    mirrored = [list(reversed(row)) for row in rows]

    # Flatten and return
    return [img for row in mirrored for img in row]


async def printable_pdf(  # noqa: PLR0913
    request: Request,
    card_uuids: List[str],
    card_width_mm: float,
    card_height_mm: float,
    margin_top_mm: float,
    margin_left_mm: float,
    horizontal_gap_mm: float,
    vertical_gap_mm: float,
) -> bytes:
    """Create a printable pdf containing the card faces."""
    # Calculate how many cards fit per page
    usable_w = config.A4_WIDTH_MM - margin_left_mm
    usable_h = config.A4_HEIGHT_MM - margin_top_mm

    cards_per_row = floor((usable_w + horizontal_gap_mm) / (card_width_mm + horizontal_gap_mm))
    cards_per_col = floor((usable_h + vertical_gap_mm) / (card_height_mm + vertical_gap_mm))
    cards_per_page = cards_per_row * cards_per_col
    log.debug(
        f'Each A4 page will contain {cards_per_row} rows and {cards_per_col} columns '
        f'for a total of {cards_per_page} cards per page.'
    )

    if cards_per_page == 0:
        raise PrintablePdfError(
            'Your layout settings are too large — no cards would fit on an A4 page. '
            'Please reduce card size, margins, or gaps.'
        )

    # Generate card front pngs
    card_front_pngs = [
        base64.b64encode(generate_card_front_png(card)).decode('UTF-8')
        for card in await fetch_membership_cards()
        if card.card_uuid in card_uuids
    ]

    # Group pngs into pages
    front_pages = [card_front_pngs[i : i + cards_per_page] for i in range(0, len(card_front_pngs), cards_per_page)]

    # Add in card back pngs
    card_back_png = base64.b64encode(generate_card_back_png()).decode('UTF-8')

    interleaved_pages = []
    for page in front_pages:
        # Pad front page to full grid
        padded_front = page + [None] * (cards_per_page - len(page))
        interleaved_pages.append({'side': 'front', 'images': padded_front})

        # Insert mirrored back page
        mirrored_back = mirror_page(
            [card_back_png if img is not None else None for img in padded_front],
            cards_per_row=cards_per_row,
            cards_per_page=cards_per_page,
        )
        interleaved_pages.append({'side': 'back', 'images': mirrored_back})

    log.debug(f'The generated pdf will contain {len(interleaved_pages)} pages')

    # Calculate left margin for mirrored page
    grid_width_mm = cards_per_row * card_width_mm + (cards_per_row - 1) * horizontal_gap_mm
    mirrored_margin_left_mm = config.A4_WIDTH_MM - (grid_width_mm + margin_left_mm)

    # Create and return PDF
    context = {
        'request': request,
        'num_columns': cards_per_row,
        'num_rows': cards_per_col,
        'card_width_mm': card_width_mm,
        'card_height_mm': card_height_mm,
        'margin_top_mm': margin_top_mm,
        'margin_left_mm': margin_left_mm,
        'horizontal_gap_mm': horizontal_gap_mm,
        'vertical_gap_mm': vertical_gap_mm,
        'mirrored_margin_left_mm': mirrored_margin_left_mm,
        'pages': interleaved_pages,
    }

    html_string = config.TEMPLATES.get_template('pdf_card_sheet.html').render(context)
    return HTML(string=html_string).write_pdf()
