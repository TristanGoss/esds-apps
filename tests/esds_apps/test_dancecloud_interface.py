import json

import httpx
import pytest
import respx

from esds_apps import config
from esds_apps.classes import MembershipCard, MembershipCardCheck, MembershipCardStatus
from esds_apps.dancecloud_interface import (
    fetch_membership_card_checks,
    fetch_membership_cards,
    reissue_membership_card,
    set_membership_card_status,
)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_membership_cards():
    fake_response = {
        'data': [
            {
                'id': 'card1',
                'attributes': {
                    'expiresAt': '2025-12-31T23:59:59',
                    'status': 'issued',
                    'number': '1234',
                },
                'relationships': {'member': {'data': {'id': 'member1'}}},
            }
        ],
        'included': [
            {
                'id': 'member1',
                'type': 'members',
                'attributes': {'firstName': 'Alice', 'lastName': 'Smith', 'email': 'alice@example.com'},
            }
        ],
    }

    route = respx.get(f'{config.DC_HOST}/{config.DC_API_PATH}/membership-cards').mock(
        return_value=httpx.Response(200, json=fake_response)
    )

    cards = await fetch_membership_cards()

    assert route.called
    assert len(cards) == 1
    card = cards[0]
    assert isinstance(card, MembershipCard)
    assert card.first_name == 'Alice'
    assert card.status == MembershipCardStatus.ISSUED


@pytest.mark.asyncio
@respx.mock
async def test_fetch_membership_card_checks():
    fake_response = {
        'data': [
            {
                'id': 'check1',
                'attributes': {'checkedAt': '2025-01-01T12:00:00'},
                'relationships': {'card': {'data': {'id': 'card1'}}, 'checkedBy': {'data': {'id': 'admin1'}}},
            }
        ],
        'included': [
            {
                'id': 'card1',
                'type': 'membership-cards',
                'attributes': {'number': '1234'},
                'relationships': {'member': {'data': {'id': 'member1'}}},
            },
            {'id': 'member1', 'type': 'members', 'attributes': {'firstName': 'Bob', 'lastName': 'Jones'}},
        ],
    }

    route = respx.get(f'{config.DC_HOST}/{config.DC_API_PATH}/membership-card-checks').mock(
        return_value=httpx.Response(200, json=fake_response)
    )

    checks = await fetch_membership_card_checks()

    assert route.called
    assert len(checks) == 1
    check = checks[0]
    assert isinstance(check, MembershipCardCheck)
    assert check.checked_by == 'admin1'
    assert check.first_name == 'Bob'
    assert check.card_number == '1234'


@pytest.mark.asyncio
@respx.mock
async def test_set_membership_card_status():
    card_uuid = 'abc123'
    status = MembershipCardStatus.CANCELLED

    route = respx.patch(f'{config.DC_HOST}/{config.DC_API_PATH}/membership-cards/{card_uuid}').mock(
        return_value=httpx.Response(200)
    )

    await set_membership_card_status(card_uuid, status)

    assert route.called
    request = route.calls[0].request
    body = json.loads(request.content)
    assert body['data']['attributes']['status'] == str(status)


@pytest.mark.asyncio
@respx.mock
async def test_reissue_membership_card_valid_reason():
    card_uuid = 'xyz789'
    reason = MembershipCardStatus.LOST

    route = respx.post(f'{config.DC_HOST}/{config.DC_API_PATH}/membership-cards/{card_uuid}/-actions/reissue').mock(
        return_value=httpx.Response(200)
    )

    await reissue_membership_card(card_uuid, reason)

    assert route.called
    request = route.calls[0].request
    body = json.loads(request.content)
    assert body['action']['status'] == str(reason)


@pytest.mark.asyncio
async def test_reissue_membership_card_invalid_reason():
    with pytest.raises(AssertionError):
        await reissue_membership_card('bad123', MembershipCardStatus.ISSUED)
