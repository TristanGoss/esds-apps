import time
from datetime import datetime

import pytest

from esds_apps.simple_cache import SimpleCache


def test_cache_write_and_read(tmp_path):
    cache = SimpleCache('testcache', max_age_s=60, cache_root=tmp_path)
    payload = {'hello': 'world'}
    cache.write(payload)

    result = cache.read()
    assert result == payload


def test_cache_expiry(tmp_path):
    cache = SimpleCache('testcache', max_age_s=0.1, cache_root=tmp_path)
    payload = {'foo': 'bar'}
    cache.write(payload)

    time.sleep(0.2)
    result = cache.read()
    assert result is None
    assert not list(tmp_path.glob('*.json'))  # cache was cleared


def test_cache_clear(tmp_path):
    cache = SimpleCache('testcache', max_age_s=60, cache_root=tmp_path)
    payload = [1, 2, 3]
    cache.write(payload)

    assert len(list(tmp_path.glob('*.json'))) == 1
    cache.clear()
    assert len(list(tmp_path.glob('*.json'))) == 0


def test_safe_timestamp_conversion_roundtrip_windows(monkeypatch):
    # Simulate Windows
    monkeypatch.setattr('esds_apps.simple_cache.os.name', 'nt')
    dt = datetime(2024, 4, 30, 15, 45, 0)
    iso = SimpleCache.to_os_safe_iso_timestamp(dt)
    assert ':' not in iso
    restored = SimpleCache.from_os_safe_iso_timestamp(iso)
    assert restored == dt


def test_safe_timestamp_conversion_roundtrip_unix(monkeypatch):
    # Simulate Unix
    monkeypatch.setattr('esds_apps.simple_cache.os.name', 'posix')
    dt = datetime(2024, 4, 30, 15, 45, 0)
    iso = SimpleCache.to_os_safe_iso_timestamp(dt)
    assert ':' in iso
    restored = SimpleCache.from_os_safe_iso_timestamp(iso)
    assert restored == dt


def test_invalid_timestamp_fails_cleanly():
    # Confirm ValueError bubbles up from datetime.fromisoformat
    with pytest.raises(ValueError):
        SimpleCache.from_os_safe_iso_timestamp('not-a-date')
