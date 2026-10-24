"""Static checks on the nginx configuration that guard against mistakes nginx itself accepts silently."""

import re
from pathlib import Path

import pytest

CONF_DIR = Path(__file__).resolve().parents[1] / 'nginx' / 'conf.d'
REQUIRED_HEADERS = [
    'Strict-Transport-Security',
    'X-Content-Type-Options',
    'X-Frame-Options',
    'Referrer-Policy',
    'Content-Security-Policy',
    'Permissions-Policy',
]


def _strip_comments(text: str) -> str:
    return re.sub(r'#[^\n]*', '', text)


def _location_bodies(text: str) -> list[str]:
    """Return the body of every `location` block (including nested braces)."""
    bodies = []
    for match in re.finditer(r'\blocation\b[^{;]*\{', text):
        depth, i = 1, match.end()
        while depth:
            depth += {'{': 1, '}': -1}.get(text[i], 0)
            i += 1
        bodies.append(text[match.end() : i - 1])
    return bodies


@pytest.mark.parametrize('name', ['default.conf', 'proxy_app.inc'])
def test_no_add_header_inside_a_location(name):
    """An add_header in a location silently drops every server-level header (HSTS, CSP, ...) for that location."""
    text = _strip_comments((CONF_DIR / name).read_text(encoding='utf-8'))

    offenders = [body.strip()[:60] for body in _location_bodies(text) if 'add_header' in body]

    assert not offenders, f'add_header inside location block(s): {offenders}'


def test_proxy_include_is_only_used_inside_locations_and_has_no_uri_part():
    include = _strip_comments((CONF_DIR / 'proxy_app.inc').read_text(encoding='utf-8'))
    assert re.search(r'proxy_pass\s+http://esds-apps:8080;', include), (
        'a URI part on proxy_pass rewrites matched prefixes'
    )
    conf = _strip_comments((CONF_DIR / 'default.conf').read_text(encoding='utf-8'))
    assert conf.count('proxy_app.inc') == 2
    assert all('proxy_app.inc' in body for body in _location_bodies(conf) if 'limit_req' in body)


def test_security_headers_are_included_at_server_level_in_the_tls_server():
    conf = _strip_comments((CONF_DIR / 'default.conf').read_text(encoding='utf-8'))
    headers = _strip_comments((CONF_DIR / 'security_headers.inc').read_text(encoding='utf-8'))

    assert 'security_headers.inc' in conf
    assert not any('security_headers.inc' in body for body in _location_bodies(conf))
    for header in REQUIRED_HEADERS:
        assert re.search(rf'add_header\s+{header}\s+"[^"]+"\s+always;', headers), (
            f'{header} missing or without `always`'
        )


def test_csp_forbids_inline_scripts_and_third_party_origins_beyond_the_pinned_cdn():
    headers = (CONF_DIR / 'security_headers.inc').read_text(encoding='utf-8')
    csp = re.search(r'Content-Security-Policy\s+"([^"]+)"', headers).group(1)
    script_src = re.search(r'script-src ([^;]+)', csp).group(1).split()

    assert script_src == ["'self'", 'https://cdn.jsdelivr.net']
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp


def test_unknown_hosts_get_catch_all_servers_and_version_is_hidden():
    conf = _strip_comments((CONF_DIR / 'default.conf').read_text(encoding='utf-8'))

    assert 'listen 80 default_server' in conf
    assert 'listen 443 ssl default_server' in conf
    assert 'ssl_reject_handshake on' in conf
    assert conf.count('server_tokens off') == conf.count('server {')
