"""End-to-end tests of the Google token handling in ``auth`` (ID-token verification and service-account JWT).

Tokens are signed here with ``cryptography`` directly, so the fixtures do not depend on whichever JOSE library
``auth`` happens to use.
"""

import base64
import json
import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from esds_apps import auth, config

CLIENT_ID = 'client-id.apps.googleusercontent.com'
EMAIL = 'dancer@example.com'
GROUP = 'committee@example.com'
SA_EMAIL = 'sa@project.iam.gserviceaccount.com'
ADMIN_EMAIL = 'admin@example.com'
KID = 'test-key-1'


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _b64_int(n: int) -> str:
    return _b64(n.to_bytes((n.bit_length() + 7) // 8, 'big'))


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def _jwks(key: rsa.RSAPrivateKey, kid: str = KID) -> dict:
    nums = key.public_key().public_numbers()
    return {
        'keys': [{'kty': 'RSA', 'alg': 'RS256', 'use': 'sig', 'kid': kid, 'n': _b64_int(nums.n), 'e': _b64_int(nums.e)}]
    }


def _sign(header: dict, claims: dict, key: rsa.RSAPrivateKey) -> str:
    signing_input = f'{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}'
    sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f'{signing_input}.{_b64(sig)}'


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        'iss': 'https://accounts.google.com',
        'aud': CLIENT_ID,
        'email': EMAIL,
        'iat': now,
        'exp': now + 3600,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


@pytest.fixture(scope='module')
def google_key():
    return _new_key()


@pytest.fixture(scope='module')
def other_key():
    return _new_key()


@pytest.fixture(scope='module')
def sa_key():
    return _new_key()


@pytest.fixture
def secrets(tmp_path, sa_key):
    sa_file = tmp_path / 'sa.json'
    sa_file.write_text(json.dumps({'client_email': SA_EMAIL, 'private_key': _pem(sa_key)}))
    values = {
        'GOOGLE_CLIENT_ID': CLIENT_ID,
        'GOOGLE_CLIENT_SECRET': 'client-secret',
        'GOOGLE_OAUTH_REDIRECT_URI': 'https://apps.example.com/auth/callback',
        'GOOGLE_SERVICE_ACCOUNT_FILE': str(sa_file),
        'GOOGLE_ADMIN_IMPERSONATE_EMAIL': ADMIN_EMAIL,
        'GOOGLE_ALLOWED_GROUP_EMAIL': GROUP,
    }
    with patch.dict(config.SECRETS, values):
        yield values


def _request():
    request = MagicMock()
    request.query_params = {'code': 'auth-code', 'state': 'st'}
    request.cookies = {auth._OAUTH_STATE_COOKIE: 'st', auth._OAUTH_NEXT_COOKIE: '/membership-cards'}
    return request


def _mock_google(id_token: str, jwks: dict, is_member: bool = True):
    """Mock every Google endpoint the callback touches; returns (group_route, assertions list)."""
    assertions: list[str] = []

    def token_endpoint(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        if form['grant_type'] == ['authorization_code']:
            return httpx.Response(200, json={'id_token': id_token})
        assertions.append(form['assertion'][0])
        return httpx.Response(200, json={'access_token': 'sa-access-token'})

    respx.post(auth._GOOGLE_TOKEN_URL).mock(side_effect=token_endpoint)
    respx.get(auth._GOOGLE_CERTS_URL).mock(return_value=httpx.Response(200, json=jwks))
    group_route = respx.get(f'{auth._GOOGLE_ADMIN_API}/groups/{GROUP}/hasMember/{EMAIL}').mock(
        return_value=httpx.Response(200, json={'isMember': is_member})
    )
    return group_route, assertions


@respx.mock
async def test_valid_token_logs_in_and_sets_session_cookie(secrets, google_key):
    token = _sign({'alg': 'RS256', 'kid': KID, 'typ': 'JWT'}, _claims(), google_key)
    group_route, _ = _mock_google(token, _jwks(google_key))

    response = await auth.handle_oauth_callback(_request())

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers['location'] == '/membership-cards'
    cookie_headers = ' '.join(response.headers.getlist('set-cookie'))
    assert config.AUTH_COOKIE_NAME in cookie_headers
    assert group_route.called


@respx.mock
async def test_valid_token_but_not_in_group_is_refused(secrets, google_key):
    token = _sign({'alg': 'RS256', 'kid': KID}, _claims(), google_key)
    _mock_google(token, _jwks(google_key), is_member=False)

    response = await auth.handle_oauth_callback(_request())

    assert response.status_code == 403
    assert 'set-cookie' not in response.headers


@respx.mock
async def test_accounts_google_com_issuer_without_scheme_is_accepted(secrets, google_key):
    token = _sign({'alg': 'RS256', 'kid': KID}, _claims(iss='accounts.google.com'), google_key)
    _mock_google(token, _jwks(google_key))

    response = await auth.handle_oauth_callback(_request())

    assert response.status_code == 303


@respx.mock
async def test_audience_list_containing_client_id_is_accepted(secrets, google_key):
    token = _sign({'alg': 'RS256', 'kid': KID}, _claims(aud=[CLIENT_ID, 'someone-else']), google_key)
    _mock_google(token, _jwks(google_key))

    response = await auth.handle_oauth_callback(_request())

    assert response.status_code == 303


def _forged_tokens(google_key, other_key):
    """Yield (id, token) pairs that must all be rejected, given the genuine key is ``google_key``."""
    now = int(time.time())
    good_header = {'alg': 'RS256', 'kid': KID}
    pub_pem = google_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    signing_input = f'{_b64(json.dumps({"alg": "HS256", "kid": KID}).encode())}.{_b64(json.dumps(_claims()).encode())}'
    import hmac

    hs256 = f'{signing_input}.{_b64(hmac.new(pub_pem, signing_input.encode(), "sha256").digest())}'
    unsigned = f'{_b64(json.dumps({"alg": "none"}).encode())}.{_b64(json.dumps(_claims()).encode())}.'
    return {
        'wrong_audience': _sign(good_header, _claims(aud='another-client'), google_key),
        'wrong_issuer': _sign(good_header, _claims(iss='https://evil.example.com'), google_key),
        'expired': _sign(good_header, _claims(exp=now - 3600), google_key),
        'missing_exp': _sign(good_header, _claims(exp=None), google_key),
        'missing_audience': _sign(good_header, _claims(aud=None), google_key),
        'missing_issuer': _sign(good_header, _claims(iss=None), google_key),
        'missing_email': _sign(good_header, _claims(email=None), google_key),
        'signed_by_other_key': _sign(good_header, _claims(), other_key),
        'unknown_kid': _sign({'alg': 'RS256', 'kid': 'not-a-real-kid'}, _claims(), google_key),
        'alg_none': unsigned,
        'hs256_with_public_key_as_secret': hs256,
        'not_a_jwt': 'garbage',
    }


@pytest.mark.parametrize(
    'case',
    [
        'wrong_audience',
        'wrong_issuer',
        'expired',
        'missing_exp',
        'missing_audience',
        'missing_issuer',
        'missing_email',
        'signed_by_other_key',
        'unknown_kid',
        'alg_none',
        'hs256_with_public_key_as_secret',
        'not_a_jwt',
    ],
)
@respx.mock
async def test_forged_or_invalid_tokens_never_log_in(case, secrets, google_key, other_key):
    token = _forged_tokens(google_key, other_key)[case]
    group_route, _ = _mock_google(token, _jwks(google_key))

    response = await auth.handle_oauth_callback(_request())

    assert response.status_code == 403
    assert b'Sign-in failed' in response.body
    assert 'set-cookie' not in response.headers
    assert not group_route.called  # rejected before we ever asked whether this user is allowed in


@respx.mock
async def test_refused_code_exchange_shows_error_page(secrets):
    respx.post(auth._GOOGLE_TOKEN_URL).mock(return_value=httpx.Response(400, json={'error': 'invalid_grant'}))

    response = await auth.handle_oauth_callback(_request())

    assert response.status_code == 400
    assert b'Sign-in failed' in response.body
    assert b'/auth/login' in response.body
    assert 'set-cookie' not in response.headers


@respx.mock
def test_forged_token_through_the_real_app_returns_an_html_error_page(secrets, google_key, other_key):
    from esds_apps.main import app

    token = _sign({'alg': 'RS256', 'kid': KID}, _claims(), other_key)
    _mock_google(token, _jwks(google_key))
    client = TestClient(app, base_url='https://testserver', raise_server_exceptions=False)
    client.cookies.set(auth._OAUTH_STATE_COOKIE, 'st')

    response = client.get('/auth/callback', params={'code': 'auth-code', 'state': 'st'})

    assert response.status_code == 403
    assert response.headers['content-type'].startswith('text/html')
    assert 'Sign-in failed' in response.text
    assert config.AUTH_COOKIE_NAME not in response.cookies


@respx.mock
async def test_service_account_assertion_is_a_correct_rs256_jwt(secrets, google_key, sa_key):
    token = _sign({'alg': 'RS256', 'kid': KID}, _claims(), google_key)
    _, assertions = _mock_google(token, _jwks(google_key))

    await auth.handle_oauth_callback(_request())

    assert len(assertions) == 1
    header_b64, payload_b64, sig_b64 = assertions[0].split('.')
    header = json.loads(_unb64(header_b64))
    claims = json.loads(_unb64(payload_b64))
    assert header['alg'] == 'RS256'
    sa_key.public_key().verify(
        _unb64(sig_b64), f'{header_b64}.{payload_b64}'.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    assert claims['iss'] == SA_EMAIL
    assert claims['sub'] == ADMIN_EMAIL
    assert claims['aud'] == auth._GOOGLE_TOKEN_URL
    assert claims['scope'] == 'https://www.googleapis.com/auth/admin.directory.group.member.readonly'
    assert claims['exp'] - claims['iat'] == 3600
    assert abs(claims['iat'] - time.time()) < 30
