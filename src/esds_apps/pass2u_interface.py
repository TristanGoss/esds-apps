import logging

import httpx
import pytz

from esds_apps import config
from esds_apps.classes import MembershipCard
from esds_apps.simple_cache import SimpleCache

log = logging.getLogger(__name__)

MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE = SimpleCache(
    'map_dc_card_number_to_pass2u_pass_id', config.FOREVER_CACHE_TIMEOUT_S
)


async def create_wallet_pass(card: MembershipCard) -> str:
    """Generate a Apple and Google pass for a membership card.

    Returns the passId.
    The page where the user can obtain the pass is https://www.pass2u.net/d/{passId}
    """
    # localise card expiry date if necessary, as the API spec requires a localised expiration date
    if card.expires_at.tzinfo is None:
        localised_expires_at = pytz.timezone('Europe/London').localize(card.expires_at)
    else:
        localised_expires_at = card.expires_at.astimezone(pytz.timezone('Europe/London'))

    # start by creating a new card within Pass2U
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f'{config.PASS2U_HOST}/{config.PASS2U_API_PATH}/models/{config.PASS2U_MODEL_ID}/passes',
            json={
                'expirationDate': localised_expires_at.isoformat(),
                'barcode': {'message': card.check_url, 'altText': 'QR code'},
                'fields': [
                    {'key': 'name', 'value': card.first_name + ' ' + card.last_name},
                    {'key': 'expiryDateStr', 'value': card.expires_at.strftime('%d %B %Y')},
                    {'key': 'cardNumber', 'value': str(card.card_number)},
                ],
            },
            headers={
                'x-api-key': config.SECRETS['PASS2U_API_KEY'],
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
        )
        response.raise_for_status()
        log.info(f'wallet pass for card number {card.card_number} created.')
        result = response.json()
        log.debug(f'created a new Pass within Pass2U, json response was {result}')

        # save the mapping of {card_number: pass_id} in a very long term cache
        # (yes, this should obviously be a db), as we will need it later to void cards.
        current_cache_content = MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.read()
        if current_cache_content:
            current_cache_content[card.card_number] = result['passId']
        else:
            current_cache_content = {card.card_number: result['passId']}
        MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.clear()
        MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.write(current_cache_content)

    return result['passId']


async def void_wallet_pass_if_exists(card: MembershipCard) -> None:
    card_number = card.card_number  # we only pass the whole card in for consistency
    current_cache_content = MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.read()
    if current_cache_content and str(card_number) in current_cache_content:
        pass_id_to_void = current_cache_content[str(card_number)]
        log.debug(f'about to void wallet pass for card number {card_number}, wallet pass Id {pass_id_to_void}')

        with httpx.Client() as client:
            response = client.put(
                f'{config.PASS2U_HOST}/{config.PASS2U_API_PATH}/models/{config.PASS2U_MODEL_ID}/passes/{pass_id_to_void}',
                json={
                    'voided': True,
                },
                headers={
                    'x-api-key': config.SECRETS['PASS2U_API_KEY'],
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                },
            )

        response.raise_for_status()
        log.info(f'wallet pass for card number {card_number} voided.')

        # remove voided wallet pass from cache
        del current_cache_content[str(card_number)]
        MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.clear()
        MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE.write(current_cache_content)
    else:
        log.info(
            f'Could not void wallet pass for card number {card_number} as no wallet pass was found in the local cache.'
        )
