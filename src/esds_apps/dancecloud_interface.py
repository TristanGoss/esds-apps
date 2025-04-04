import logging
from datetime import datetime
from typing import Dict, List, Optional

import requests

from esds_apps import config
from esds_apps.classes import MembershipCard, MembershipCardStatus

log = logging.getLogger(__name__)


def fetch_membership_cards(additional_params: Optional[Dict] = None) -> List[MembershipCard]:
    log.debug('Polling Dancecloud for membership cards...')

    params = {'page[size]': 9999, 'include': 'member'}
    if additional_params is not None:
        params.update(additional_params)
    response = requests.get(
        f'{config.DC_SERVER}/{config.DC_API_PATH}/membership-cards',
        headers=config.DC_GET_HEADERS,
        params=params,
    )
    response.raise_for_status()

    # parse the output to extract the bits we care about
    card_data = response.json()['data']
    member_data = [x for x in response.json()['included'] if x['type'] == 'members']
    cards = []

    for d in card_data:
        member_details = [x for x in member_data if x['id'] == d['relationships']['member']['data']['id']][0]
        cards.append(
            MembershipCard(
                expires_at=datetime.fromisoformat(d['attributes']['expiresAt']),
                member_uuid=d['relationships']['member']['data']['id'],
                card_uuid=d['id'],
                status=MembershipCardStatus(d['attributes']['status']),
                card_number=d['attributes']['number'],
                first_name=member_details['attributes']['firstName'],
                last_name=member_details['attributes']['lastName'],
                email=member_details['attributes']['email'],
            )
        )

    log.debug(f'Found {len(cards)} membership cards.')

    return cards


def set_membership_card_status(card_uuid: str, status: MembershipCardStatus) -> None:
    response = requests.patch(
        f'{config.DC_SERVER}/{config.DC_API_PATH}/membership-cards/{card_uuid}',
        headers=config.DC_PATCH_HEADERS,
        json={'data': {'type': 'membership-cards', 'id': card_uuid, 'attributes': {'status': str(status)}}},
    )
    response.raise_for_status()

    log.debug(f'Informed Dancecloud that membership card with ID {card_uuid} now has status {status}')


def reissue_membership_card(card_uuid: str, reason: MembershipCardStatus) -> None:
    assert reason in [MembershipCardStatus.DAMAGED, MembershipCardStatus.LOST, MembershipCardStatus.STOLEN], (
        'You may only reissue a card as a result of it being damaged, lost or stolen.'
    )
    response = requests.post(
        f'{config.DC_SERVER}/{config.DC_API_PATH}/membership-cards/{card_uuid}/-actions/reissue',
        headers=config.DC_POST_HEADERS,
        json={'action': {'status': str(reason)}},
    )
    response.raise_for_status()
    # TODO: Note that as of 1710 1st April, this 404s.

    log.debug(f'Asked Dancecloud to reissue membership card with ID {card_uuid} because it was {reason}.')
