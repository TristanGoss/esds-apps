import asyncio
from datetime import datetime
from dataclasses import dataclass
from email.message import EmailMessage
import json
import logging
import os
from pathlib import Path
import smtplib
from smtplib import SMTPResponseException
from time import sleep
from typing import List

import cairosvg
from fastapi.templating import Jinja2Templates
from lxml import etree
import requests
import segno

from esds_apps import config

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "assets")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MembershipCard():
    card_uuid: str
    member_uuid: str
    card_number: int
    expires_at: datetime
    first_name: str
    last_name: str
    email: str


def generate_card_face_png(card: MembershipCard) -> bytes:
    # load the static svg template
    with open(config.ASSETS_PATH / "membership_card_background.svg", "rb") as f:
        background = etree.parse(f)

    # fetch own QR code
    # TODO: Dancecloud apparently hasn't implemented the below route yet.
    # response = requests.get(f"{config.DC_SERVER}/members/cards/{self.card_uuid}/qr-code.svg")
    # response.raise_for_status()
    #     qr_code = etree.parse(response.text)
    # let's do it ourselves for now, since we know what the url will be
    qr_svg = segno.make(
        f"{config.DC_SERVER}/members/cards/{card.card_uuid}/check",
        error='m').svg_inline()

    # Define SVG namespace
    SVG_NS = "http://www.w3.org/2000/svg"
    NSMAP = {None: SVG_NS}

    # Create the root SVG container
    combined_svg = etree.Element("svg", nsmap=NSMAP)
    # Define svg as ISO/IEC 7810 ID-1 card, working in millimeters
    combined_svg.set("width", "85.6mm")
    combined_svg.set("height", "53.98mm")
    combined_svg.set("viewBox", "0 0 85.6 53.98")

    # Add the background
    g1 = etree.SubElement(combined_svg, "g", transform="translate(0, 0), scale(0.033)")
    for el in background.getroot():
        g1.append(el)

    # Add the QR code
    combined_svg.append(etree.fromstring(f'<g transform="translate(55, 10), scale(0.5)">{qr_svg}</g>'))

    # Add name
    text = etree.SubElement(
        combined_svg,
        "text",
        {
            "x": "20",
            "y": "10",
            "fill": "black",
            "font-size": "0.2mm"
        }
    )
    text.text = card.first_name + ' ' + card.last_name

    # Add membership card number
    text = etree.SubElement(
        combined_svg,
        "text",
        {
            "x": "10",
            "y": "20",
            "fill": "black",
            "font-size": "0.2mm",
            "transform": "rotate(-90 10 20)"
        }
    )
    text.text = f"M: {card.card_number:06}"

    # Add expiry date
    text = etree.SubElement(
        combined_svg,
        "text",
        {
            "x": "20",
            "y": "30",
            "fill": "black",
            "font-size": "0.2mm"
        }
    )
    text.text = 'EXP: ' + card.expires_at.strftime("%d/%m/%Y")

    # bake to png and return
    return cairosvg.svg2png(
        bytestring=etree.tostring(
            combined_svg,
            pretty_print=True,
            xml_declaration=True,
            encoding="UTF-8"
        ),
        dpi=config.CARD_DPI,
        background_color='white'
    )


async def auto_issue_unissued_cards():
    log.debug("Dancecloud unissued cards poller started.")
    while True:
        await asyncio.sleep(config.DC_POLL_INTERVAL_S)
        poll_dancecloud_for_unissued_cards()

        # TODO: Generate ESDS membership cards
        # need Melinda's new versions for this, and to have them as SVG.
        
        # TODO: The "add to Google/Apple wallet" option is actually
        # quite hard, since it's certified proof.

        # TODO: Email ESDS membership cards to new members
        # Let's do this without sendgrid, for simplicity.

        # TODO: Update membership card status to 'issued'
        # already implemented below.


def poll_dancecloud_for_unissued_cards() -> List[MembershipCard]:
    log.debug('Polling Dancecloud for unissued membership cards...')

    response = requests.get(
        f'{config.DC_SERVER}/{config.DC_API_PATH}/membership-cards',
        headers=config.DC_GET_HEADERS,
        params={'page[size]': 9999,
                'include': 'member',
                'filter[status]': 'new'})
    response.raise_for_status()

    # parse the output to extract the bits we care about
    card_data = response.json()['data']
    member_data = [x for x in response.json()['included']
                   if x['type'] == 'members']
    unissued_cards = []

    for d in card_data:
        member_details = [
            x for x in member_data
            if x['id'] == d['relationships']['member']['data']['id']][0]

        unissued_cards.append(MembershipCard(
            expires_at = datetime.fromisoformat(d['attributes']['expiresAt']),
            member_uuid = d['relationships']['member']['data']['id'],
            card_uuid = d['id'],
            card_number = d['attributes']['number'],
            first_name = member_details['attributes']['firstName'],
            last_name = member_details['attributes']['lastName'],
            email = member_details['attributes']['email']
        ))

    log.debug(f'Found {len(unissued_cards)} unissued membership cards.')

    return unissued_cards


def compose_membership_email(card: MembershipCard) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Your ESDS Membership"
    msg["From"] = "info@esds.org.uk"
    msg["To"] = f"{card.email}"
    msg.set_content("Here's your code!")

    # Add plain text fallback content.
    msg.set_content(config.MAIL_NO_HTML_FALLBACK_MESSAGE)

    # Add HTML version.
    msg.add_alternative(
        TEMPLATES.env.get_template("new_membership_email_template.html").render(
            {
                "first_name": card.first_name,
                "last_name": card.last_name
            }
        ),
        subtype="html")
    msg_html_part = msg.get_payload()[-1]

    qr_code_png = generate_card_face_png(card)

    # Attach card face inline using Content-ID.
    msg_html_part.add_related(
        qr_code_png,
        maintype="image",
        subtype="png",
        cid="membership_card_cid"
    )

    # Add it as an attachment as well.
    msg.add_attachment(
        qr_code_png, maintype="image",
        subtype="png",
        filename=f"membership_card_{card.card_uuid}.png")
    
    # Add the rest of the images used in the template
    with open(config.ASSETS_PATH / 'new_membership_email_image_to_cid_map.json') as fh:
        image_to_cid_map = json.load(fh)

    for entry in image_to_cid_map:
        with open(config.ASSETS_PATH / entry['image_path'], "rb") as f:
            msg_html_part.add_related(
                f.read(),
                maintype="image",
                subtype=os.path.splitext(entry['image_path'])[1][1:],  # e.g., 'png'
                cid=entry['cid']
            )
    return msg


def distribute_membership_emails(emails: List[EmailMessage]) -> List[bool]:
    was_email_succesfully_delivered = [False] * len(emails)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login("info@esds.org.uk", config.GMAIL_APP_PASSWORD)
        for i, email in enumerate(emails):
            try:
                log.debug(f'About to send new membership email to {email["To"]}')
                smtp.send_message(email)
                was_email_succesfully_delivered[i] = True
            except SMTPResponseException as e:
                log.error(f"Email was not delivered; SMTP error: {e.smtp_code} - "
                          f"{e.smtp_error.decode(errors='ignore')}")
            sleep(1)

    return was_email_succesfully_delivered


def inform_dancecloud_of_card_issue(card_uuid: str) -> None:
    response = requests.patch(
        f'{config.DC_SERVER}/{config.DC_API_PATH}/membership-cards/{card_uuid}',
        headers={config.DC_PATCH_HEADERS},
        data={
            "data": {
                "type": "membership-cards",
                "id": card_uuid,
                "attributes": {
                    "status": "issued"
                }
            }
        }
    )
    response.raise_for_status()

    log.debug(f'Informed Dancecloud that membership card with ID {card_uuid} has been issued.')
