import logging
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from esds_apps import config
from esds_apps.classes import MembershipCard, MembershipCardCheck, MembershipCardStatus

log = logging.getLogger(__name__)


async def fetch_membership_cards(additional_params: Optional[Dict] = None) -> List[MembershipCard]:
    # Note that this returns membership cards for *all* schemes at the moment!
    log.debug('Polling Dancecloud for membership cards...')

    params = {'page[size]': 9999, 'include': 'member'}
    if additional_params is not None:
        params.update(additional_params)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f'{config.DC_HOST}/{config.DC_API_PATH}/membership-cards',
            headers=config.DC_GET_HEADERS,
            params=params,
        )
    response.raise_for_status()

    # parse the output to extract the bits we care about
    card_data = response.json()['data']

    # return early if no results
    if len(card_data) == 0:
        return []

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


async def fetch_membership_card_checks(additional_params: Optional[Dict] = None) -> List[MembershipCardCheck]:
    log.debug('Polling Dancecloud for membership card checks...')

    params = {'page[size]': 9999, 'include': 'card.member,checkedBy'}
    if additional_params is not None:
        params.update(additional_params)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f'{config.DC_HOST}/{config.DC_API_PATH}/membership-card-checks',
            headers=config.DC_GET_HEADERS,
            params=params,
        )
    response.raise_for_status()

    # parse the output to extract the bits we care about
    check_data = response.json()['data']

    # return early if no results
    if len(check_data) == 0:
        return []

    membership_card_data = [x for x in response.json()['included'] if x['type'] == 'membership-cards']
    member_data = [x for x in response.json()['included'] if x['type'] == 'members']
    checks = []

    for d in check_data:
        membership_card_details = [
            x for x in membership_card_data if x['id'] == d['relationships']['card']['data']['id']
        ][0]
        member_details = [
            x for x in member_data if x['id'] == membership_card_details['relationships']['member']['data']['id']
        ][0]
        checks.append(
            MembershipCardCheck(
                member_uuid=member_details['id'],
                card_uuid=membership_card_details['id'],
                card_number=membership_card_details['attributes']['number'],
                first_name=member_details['attributes']['firstName'],
                last_name=member_details['attributes']['lastName'],
                checked_at=datetime.fromisoformat(d['attributes']['checkedAt']),
                checked_by=d['relationships']['checkedBy']['data']['id']
                if d['relationships']['checkedBy']['data'] is not None
                else None,
            )
        )

    log.debug(f'Found {len(checks)} membership card checks.')

    return checks


async def set_membership_card_status(card_uuid: str, status: MembershipCardStatus) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f'{config.DC_HOST}/{config.DC_API_PATH}/membership-cards/{card_uuid}',
            headers=config.DC_PATCH_HEADERS,
            json={'data': {'type': 'membership-cards', 'id': card_uuid, 'attributes': {'status': str(status)}}},
        )
        response.raise_for_status()

    log.debug(f'Informed Dancecloud that membership card with ID {card_uuid} now has status {status}')


async def reissue_membership_card(card_uuid: str, reason: MembershipCardStatus) -> None:
    assert reason in [MembershipCardStatus.DAMAGED, MembershipCardStatus.LOST, MembershipCardStatus.STOLEN], (
        'You may only reissue a card as a result of it being damaged, lost or stolen.'
    )
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f'{config.DC_HOST}/{config.DC_API_PATH}/membership-cards/{card_uuid}/-actions/reissue',
            headers=config.DC_POST_HEADERS,
            json={'action': {'status': str(reason)}},
        )
        response.raise_for_status()
        # TODO: Note that as of 1710 1st April, this 404s.

    log.debug(f'Asked Dancecloud to reissue membership card with ID {card_uuid} because it was {reason}.')
