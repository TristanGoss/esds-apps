import hashlib

import pytest


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """Reduce PBKDF2 from 480,000 iterations to 1 for test speed."""
    _orig = hashlib.pbkdf2_hmac
    monkeypatch.setattr(
        hashlib,
        'pbkdf2_hmac',
        lambda name, pw, salt, iters, **kw: _orig(name, pw, salt, 1, **kw),
    )
