from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from esds_apps import config
from esds_apps.pass2u_interface import create_wallet_pass, void_wallet_pass_if_exists


@pytest.mark.asyncio
@respx.mock
@patch('esds_apps.pass2u_interface.MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE')
async def test_create_wallet_pass(mock_cache, sample_card):
    expected_pass_id = 'abcdef123456'
    respx.post(f'{config.PASS2U_HOST}/{config.PASS2U_API_PATH}/models/{config.PASS2U_MODEL_ID}/passes').mock(
        return_value=httpx.Response(200, json={'passId': expected_pass_id})
    )

    mock_cache.read.return_value = {}
    mock_cache.clear = MagicMock()
    mock_cache.write = MagicMock()

    pass_id = await create_wallet_pass(sample_card)

    assert pass_id == expected_pass_id
    mock_cache.clear.assert_called_once()
    mock_cache.write.assert_called_once()
    written = mock_cache.write.call_args[0][0]
    assert written[sample_card.card_number] == expected_pass_id


@pytest.mark.asyncio
@patch('esds_apps.pass2u_interface.MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE')
@patch('esds_apps.pass2u_interface.httpx.Client')
async def test_void_wallet_pass_if_exists_found(mock_httpx_client, mock_cache, sample_card):
    mock_client_instance = MagicMock()
    mock_httpx_client.return_value.__enter__.return_value = mock_client_instance
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client_instance.put.return_value = mock_response

    mock_cache.read.return_value = {str(sample_card.card_number): 'abcdef123456'}
    mock_cache.clear = MagicMock()
    mock_cache.write = MagicMock()

    await void_wallet_pass_if_exists(sample_card)

    assert mock_client_instance.put.called
    request_payload = mock_client_instance.put.call_args[1]['json']
    assert request_payload['voided'] is True

    mock_cache.clear.assert_called_once()
    mock_cache.write.assert_called_once()
    written = mock_cache.write.call_args[0][0]
    assert str(sample_card.card_number) not in written


@pytest.mark.asyncio
@patch('esds_apps.pass2u_interface.MAP_CARD_NUMBER_TO_WALLET_PASS_ID_CACHE')
@patch('esds_apps.pass2u_interface.httpx.Client')
async def test_void_wallet_pass_if_exists_not_found(mock_httpx_client, mock_cache, sample_card):
    mock_cache.read.return_value = {}  # Simulate no pass found

    await void_wallet_pass_if_exists(sample_card)

    assert not mock_httpx_client.called  # No HTTP request should be made
