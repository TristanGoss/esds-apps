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
from typing import List

import cairosvg
import segno
from fastapi import Request
from lxml import etree
from weasyprint import HTML

from esds_apps import config
from esds_apps.classes import MembershipCard, PrintablePdfError
from esds_apps.dancecloud_interface import fetch_membership_cards

log = logging.getLogger(__name__)

MM_TO_A4_SCREEN_PX = 3.77953  # correct conversion for compositing svgs within an A4 html page


def generate_card_face_png(card: MembershipCard) -> str:
    # load the static svg template
    # TODO: Replace with Melinda's work and update the positioning & fonts
    with open(config.PUBLIC_DIR / 'membership_card_background.svg', 'rb') as f:
        background = etree.parse(f)

    # fetch own QR code
    # TODO: Dancecloud apparently hasn't implemented the below route yet.
    # response = requests.get(f"{config.DC_SERVER}/members/cards/{self.card_uuid}/qr-code.svg")
    # response.raise_for_status()
    #     qr_code = etree.parse(response.text)
    # let's do it ourselves for now, since we know what the url will be
    qr_svg = segno.make(f'{config.DC_SERVER}/members/cards/{card.card_uuid}/check', error='m').svg_inline()

    # Define SVG namespace
    SVG_NS = 'http://www.w3.org/2000/svg'
    NSMAP = {None: SVG_NS}

    # Create the root SVG container
    combined_svg = etree.Element('svg', nsmap=NSMAP)
    # Define svg as ISO/IEC 7810 ID-1 card, working in millimeters
    combined_svg.set('width', '85.6mm')
    combined_svg.set('height', '53.98mm')
    combined_svg.set('viewBox', '0 0 85.6 53.98')

    # Add the background
    g1 = etree.SubElement(combined_svg, 'g', transform='translate(0, 0), scale(0.033)')
    for el in background.getroot():
        g1.append(el)

    # Add the QR code
    combined_svg.append(etree.fromstring(f'<g transform="translate(55, 10), scale(0.5)">{qr_svg}</g>'))

    # Add name
    text = etree.SubElement(combined_svg, 'text', {'x': '20', 'y': '10', 'fill': 'black', 'font-size': '0.2mm'})
    text.text = card.first_name + ' ' + card.last_name

    # Add membership card number
    text = etree.SubElement(
        combined_svg,
        'text',
        {'x': '10', 'y': '20', 'fill': 'black', 'font-size': '0.2mm', 'transform': 'rotate(-90 10 20)'},
    )
    text.text = f'M: {card.card_number:06}'

    # Add expiry date
    text = etree.SubElement(combined_svg, 'text', {'x': '20', 'y': '30', 'fill': 'black', 'font-size': '0.2mm'})
    text.text = 'EXP: ' + card.expires_at.strftime('%d/%m/%Y')

    return cairosvg.svg2png(
        bytestring=etree.tostring(combined_svg, pretty_print=True, xml_declaration=True, encoding='UTF-8'),
        dpi=config.CARD_DPI,
        background_color='white',
    )


async def auto_issue_unissued_cards():
    log.debug('Dancecloud unissued cards poller started.')
    while True:
        await asyncio.sleep(config.DC_POLL_INTERVAL_S)
        log.info('Dancecloud unissued cards poller awoken.')
        new_cards = fetch_membership_cards({'filter[status]': 'new'})
        log.info(f'found {len(new_cards)} new cards to issue.')

        # TODO: The "add to Google/Apple wallet" option is non-trivial,
        # since it's certified proof.

        emails = [compose_membership_email(card) for card in new_cards]
        log.info(f'composed {len(emails)} membership emails.')

        # TODO: restore for committee test
        # succesfully_delivered = send_emails(emails)
        # log.info(f'succesfully sent {sum(succesfully_delivered)} emails.')

        # for delivered, card in zip(succesfully_delivered, new_cards):
        #     if delivered:
        #         set_dancecloud_card_status(card.card_uuid, MembershipCardStatus.ISSUED)
        log.info('Dancecloud unissued cards poller returning to sleep.')


def compose_membership_email(card: MembershipCard) -> EmailMessage:
    msg = EmailMessage()
    msg['Subject'] = 'Your ESDS Membership'
    msg['From'] = 'info@esds.org.uk'
    msg['To'] = f'{card.email}'
    msg.set_content("Here's your code!")

    # Add plain text fallback content.
    msg.set_content(config.MAIL_NO_HTML_FALLBACK_MESSAGE)

    # Add HTML version.
    msg.add_alternative(
        config.TEMPLATES.env.get_template('new_membership_email.html').render(
            {'first_name': card.first_name, 'last_name': card.last_name}
        ),
        subtype='html',
    )
    msg_html_part = msg.get_payload()[-1]

    qr_code_png = generate_card_face_png(card)

    # Attach card face inline using Content-ID.
    msg_html_part.add_related(qr_code_png, maintype='image', subtype='png', cid='membership_card_cid')

    # Add it as an attachment as well.
    msg.add_attachment(qr_code_png, maintype='image', subtype='png', filename=f'membership_card_{card.card_uuid}.png')

    # Add the rest of the images used in the template
    with open(config.PUBLIC_DIR / 'new_membership_email_image_to_cid_map.json') as fh:
        image_to_cid_map = json.load(fh)

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
                log.debug(f'About to send new email to {email["To"]}')
                smtp.send_message(email)
                was_email_succesfully_delivered[i] = True
            except SMTPResponseException as e:
                log.error(
                    f'Email was not delivered; SMTP error: {e.smtp_code} - {e.smtp_error.decode(errors="ignore")}'
                )
            sleep(config.MAIL_SEND_INTERVAL_S)

    return was_email_succesfully_delivered


def printable_pdf(  # noqa: PLR0913
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
    # Convert mm to px for layout
    card_width_px = card_width_mm * config.A4_SCREEN_PX_PER_MM
    card_height_px = card_height_mm * config.A4_SCREEN_PX_PER_MM
    margin_top_px = margin_top_mm * config.A4_SCREEN_PX_PER_MM
    margin_left_px = margin_left_mm * config.A4_SCREEN_PX_PER_MM
    horizontal_gap_px = horizontal_gap_mm * config.A4_SCREEN_PX_PER_MM
    vertical_gap_px = vertical_gap_mm * config.A4_SCREEN_PX_PER_MM

    # Calculate how many cards fit per page
    usable_w = (config.A4_WIDTH_MM * config.A4_SCREEN_PX_PER_MM) - margin_left_px
    usable_h = (config.A4_HEIGHT_MM * config.A4_SCREEN_PX_PER_MM) - margin_top_px

    cards_per_row = floor((usable_w + horizontal_gap_px) / (card_width_px + horizontal_gap_px))
    cards_per_col = floor((usable_h + vertical_gap_px) / (card_height_px + vertical_gap_px))
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

    # Generate card face pngs
    card_pngs = [
        base64.b64encode(generate_card_face_png(card)).decode('UTF-8')
        for card in fetch_membership_cards()
        if card.card_uuid in card_uuids
    ]

    # Group pngs into pages
    pages = [card_pngs[i : i + cards_per_page] for i in range(0, len(card_pngs), cards_per_page)]

    log.debug(f'The generated pdf will contain {len(pages)} pages')

    # Create and return PDF
    context = {
        'request': request,
        'card_width_px': card_width_px,
        'card_height_px': card_height_px,
        'margin_top_px': margin_top_px,
        'margin_left_px': margin_left_px,
        'horizontal_gap_px': horizontal_gap_px,
        'vertical_gap_px': vertical_gap_px,
        'pages': pages,
    }

    html_string = config.TEMPLATES.get_template('pdf_card_sheet.html').render(context)
    return HTML(string=html_string).write_pdf()
