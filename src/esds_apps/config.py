import logging

from dotenv import dotenv_values

LOGGING_LEVEL = logging.DEBUG

DC_API_PATH = '/api/v1'
DC_SERVER = 'https://esds-test.dancecloud.xyz'
DC_POLL_INTERVAL_S = 86400
DC_GET_HEADERS = {
    'Authorization': f'Bearer {dotenv_values()['DC_API_TOKEN']}',
    'Accept': 'application/vnd.api+json'}
DC_PATCH_HEADERS = DC_GET_HEADERS.copy().update({
    'Content-Type': 'application/vnd.api+json'})
