"""Guards on third-party assets loaded by the page templates."""

import re
from pathlib import Path

import pytest

TEMPLATES = sorted(Path(__file__).resolve().parents[2].joinpath('templates').glob('*.html'))
_TAG = re.compile(r'<(?:script|link)\b[^>]*\b(?:src|href)="(https://[^"]+)"[^>]*>')
_ALLOWED_CDN = 'https://cdn.jsdelivr.net/npm/'
_EXACT_VERSION = re.compile(r'@\d+\.\d+\.\d+/')


def _asset_tags():
    for path in TEMPLATES:
        for match in _TAG.finditer(path.read_text(encoding='utf-8')):
            tag, url = match.group(0), match.group(1)
            if 'rel="stylesheet"' in tag or tag.startswith('<script'):
                yield pytest.param(path.name, url, tag, id=f'{path.name}:{url.rsplit("/", 1)[-1]}')


@pytest.mark.parametrize(('name', 'url', 'tag'), list(_asset_tags()))
def test_external_assets_are_pinned_and_have_integrity_hashes(name, url, tag):
    assert url.startswith(_ALLOWED_CDN), f'{name}: {url} is not served from the one allowed CDN'
    assert _EXACT_VERSION.search(url), f'{name}: {url} does not pin an exact version'
    assert re.search(r'integrity="sha384-[A-Za-z0-9+/]{64}"', tag), f'{name}: {url} has no SRI hash'
    assert 'crossorigin="anonymous"' in tag


def test_templates_load_some_external_assets():
    assert list(_asset_tags()), 'the guard above would pass vacuously'
