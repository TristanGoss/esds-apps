import asyncio
from datetime import datetime
from dataclasses import dataclass
import logging
from typing import List

import requests

from esds_apps import config


@dataclass
class MembershipCard():
    card_uuid: str
    member_uuid: str
    card_number: int
    expires_at: datetime
    first_name: str
    last_name: str
    
    @property
    def qr_code_svg_url(self) -> str:
        return f"{config.DC_SERVER}/members/cards/{self.card_uuid}/qr-code.svg"


log = logging.getLogger(__name__)

async def auto_issue_unissued_cards():
    log.debug("Dancecloud unissued cards poller started.")
    while True:
        await asyncio.sleep(config.DC_POLL_INTERVAL_S)
        poll_dancecloud_for_unissued_cards()

        # TODO: Generate ESDS membership cards

        # TODO: Email ESDS membership cards to new members

        # TODO: Update membership card status to 'issued'



def poll_dancecloud_for_unissued_cards() -> List[MembershipCard]:
    log.debug('Polling Dancecloud for unissued membership cards...')

    response = requests.get(
        f'{config.DC_SERVER}/{config.DC_API_PATH}/membersip-cards',
        headers=config.DC_GET_HEADERS,
        params={'page[size]': 9999,
                'include': 'member',
                'filter[status]': 'new'})
    
    if not response.ok:
        log.error(f'dancecloud membership card status poll failed with code '
                  f'{response.status_code}: {response.text}')
        raise RuntimeError('Dancecloud membership card poll failed.')

    # parse the output to extract the bits we care about
    card_data = response.json()['data']
    unissued_cards = []

    for d in card_data:
        member_details = [
            x for x in d['included']
            if x['id'] == d['relationships']['member']['data']['id']][0]

        unissued_cards.append(MembershipCard(
            expires_at = datetime.fromisoformat(d['attributes']['expiresAt']),
            member_uuid = d['relationships']['member']['data']['id'],
            card_number = d['attributes']['number'],
            first_name = member_details['firstName'],
            last_name = member_details['lastName']
        ))

    log.debug(f'Found {len(unissued_cards)} unissued membership cards.')

    return unissued_cards


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

    if not response.ok:
        log.error('failed to update card status of membership card '
                  f'{card_uuid} on Dancecloud! got code {response.status_code}, {response.text}')
        raise RuntimeError('Failed to update membership card status')
    else:
        log.debug(f'Informed Dancecloud that membership card with ID {card_uuid} has been issued.')
