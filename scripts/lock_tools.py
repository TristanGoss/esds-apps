"""Tools that derive things from ``poetry.lock``: the hash-pinned runtime requirements file, and a vulnerability audit.

The production image installs ``requirements.txt`` (not Poetry), so that file must always match the lock. Run
``export`` to rewrite it; the pre-commit hooks run ``export --check`` and ``audit`` whenever the lock changes.
"""

import argparse
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'poetry.lock'
REQUIREMENTS = ROOT / 'requirements.txt'

# The image is Debian-based CPython 3.13; both common server architectures are kept so either can build it.
_TARGETS = [
    {
        'sys_platform': 'linux',
        'platform_system': 'Linux',
        'os_name': 'posix',
        'platform_machine': machine,
        'platform_python_implementation': 'CPython',
        'implementation_name': 'cpython',
        'python_version': '3.13',
        'python_full_version': '3.13.0',
        'extra': '',
    }
    for machine in ('x86_64', 'aarch64')
]

_CONTINUATION = ' \x5c\n    '  # requirements-file line continuation: space, backslash, newline, indent

_HEADER = """\
# Generated from poetry.lock by scripts/lock_tools.py; do not edit by hand.
# Runtime ('main' group) dependencies for the Docker image, hash-pinned for Linux on CPython 3.13.
# Install with: pip install --require-hashes --no-deps -r requirements.txt
"""


def load_lock(path: Path = LOCK) -> list[dict]:
    """Return the ``[[package]]`` tables of a poetry lock file."""
    with path.open('rb') as f:
        return tomllib.load(f)['package']


def _marker_for(package: dict, group: str) -> str | None:
    """Return the environment marker restricting ``package`` within ``group``, if any."""
    markers = package.get('markers')
    if isinstance(markers, dict):
        return markers.get(group)  # a group missing from the table means "unconditional"
    return markers


def _wanted(package: dict, group: str) -> bool:
    if group not in package.get('groups', []):
        return False
    marker = _marker_for(package, group)
    return marker is None or any(Marker(marker).evaluate(env) for env in _TARGETS)


def render_requirements(packages: list[dict], group: str = 'main') -> str:
    """Render ``group``'s packages as a hash-pinned requirements file for the image's platform."""
    lines = [_HEADER]
    for package in sorted(packages, key=lambda p: p['name'].lower()):
        if not _wanted(package, group):
            continue
        hashes = sorted({f['hash'] for f in package['files']})
        pin = f'{package["name"]}=={package["version"]}'
        lines.append(_CONTINUATION.join([pin, *(f'--hash={h}' for h in hashes)]) + '\n')
    return '\n'.join(lines)


def render_pins(packages: list[dict]) -> str:
    """Render every locked package, regardless of group or platform, as plain pins for auditing."""
    return ''.join(f'{p["name"]}=={p["version"]}\n' for p in sorted(packages, key=lambda p: p['name'].lower()))


def export(check: bool) -> int:
    """Write ``requirements.txt`` from the lock, or with ``check`` only verify that it is already up to date."""
    expected = render_requirements(load_lock())
    current = REQUIREMENTS.read_text(encoding='utf-8') if REQUIREMENTS.exists() else None
    if current == expected:
        return 0
    if check:
        print('requirements.txt is out of date with poetry.lock; run: poetry run python scripts/lock_tools.py export')
        return 1
    REQUIREMENTS.write_text(expected, encoding='utf-8', newline='\n')
    print(f'wrote {REQUIREMENTS.name}')
    return 0


def audit() -> int:
    """Run pip-audit over every package pinned in the lock (all groups, all platforms); non-zero if any is flagged."""
    with tempfile.TemporaryDirectory() as tmp:
        pins = Path(tmp) / 'pins.txt'
        pins.write_text(render_pins(load_lock()), encoding='utf-8')
        command = [sys.executable, '-m', 'pip_audit', '-r', str(pins), '--no-deps', '--disable-pip']
        return subprocess.run(command, check=False).returncode  # noqa: S603


def main() -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    export_parser = sub.add_parser('export', help='write requirements.txt from poetry.lock')
    export_parser.add_argument('--check', action='store_true', help='fail instead of writing if out of date')
    sub.add_parser('audit', help='check every locked package against known vulnerability databases')
    args = parser.parse_args()
    return audit() if args.command == 'audit' else export(args.check)


if __name__ == '__main__':
    sys.exit(main())
