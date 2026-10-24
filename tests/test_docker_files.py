"""Guards that keep secrets out of the Docker image."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding='utf-8')


def test_dockerfile_does_not_copy_secrets_into_the_image():
    copies = re.findall(r'^\s*(?:COPY|ADD)\s+(.+)$', _read('Dockerfile'), flags=re.MULTILINE)

    assert copies, 'expected the Dockerfile to COPY something'
    for args in copies:
        assert '.env' not in args, f'secrets must be mounted at runtime, not copied: {args}'
        assert not re.search(r'\.json\b', args), f'credential files must not be copied into the image: {args}'


def test_compose_mounts_the_env_file_read_only_where_config_py_expects_it():
    compose = _read('docker-compose.yml')

    assert re.search(r'-\s*\./\.env:/app/\.env:ro\b', compose)


def test_dockerignore_keeps_secrets_out_of_the_build_context():
    ignored = {
        line.strip() for line in _read('.dockerignore').splitlines() if line.strip() and not line.startswith('#')
    }

    assert {'**/.env', 'esds-group-checker-sa.json', 'working/', '.git/'} <= ignored
    assert any(pattern.startswith('client_secret') for pattern in ignored)
