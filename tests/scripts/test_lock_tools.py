import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'lock_tools.py'
_spec = importlib.util.spec_from_file_location('lock_tools', _SCRIPT)
lock_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lock_tools)


def _pkg(name, version, groups=('main',), markers=None, hashes=('sha256:bb', 'sha256:aa')):
    package = {
        'name': name,
        'version': version,
        'groups': list(groups),
        'files': [{'file': 'f', 'hash': h} for h in hashes],
    }
    if markers is not None:
        package['markers'] = markers
    return package


def test_committed_requirements_match_the_lock():
    """If this fails, run: poetry run python scripts/lock_tools.py export (the Docker image installs that file)."""
    assert lock_tools.export(check=True) == 0


def test_only_the_requested_group_is_exported_with_sorted_hashes():
    packages = [
        _pkg('b-lib', '2.0'),
        _pkg('dev-tool', '1.0', groups=('dev',)),
        _pkg('a-lib', '1.0', groups=('main', 'dev')),
    ]

    text = lock_tools.render_requirements(packages)

    assert 'dev-tool' not in text
    assert text.index('a-lib==1.0') < text.index('b-lib==2.0')
    entry = text[text.index('b-lib==2.0') :]
    assert entry.index('sha256:aa') < entry.index('sha256:bb')
    assert text.endswith('\n')


@pytest.mark.parametrize(
    ('markers', 'included'),
    [
        (None, True),
        ('sys_platform == "linux"', True),
        ('sys_platform == "win32"', False),
        ('platform_system == "Darwin"', False),
        ('platform_python_implementation == "CPython"', True),
        ('platform_python_implementation != "CPython"', False),
        ('platform_machine == "aarch64"', True),  # either supported architecture is enough
        ('python_version < "3.13"', False),
    ],
)
def test_markers_are_evaluated_for_the_linux_image(markers, included):
    text = lock_tools.render_requirements([_pkg('thing', '1.0', markers=markers)])

    assert ('thing==1.0' in text) is included


def test_per_group_marker_tables_only_restrict_the_group_they_name():
    markers = {'dev': 'implementation_name == "pypy"'}  # as poetry writes it for a package used by two groups

    assert 'cffi==1.0' in lock_tools.render_requirements([_pkg('cffi', '1.0', groups=('main', 'dev'), markers=markers)])
    assert 'cffi==1.0' not in lock_tools.render_requirements(
        [_pkg('cffi', '1.0', groups=('main', 'dev'), markers=markers)], group='dev'
    )


def test_audit_pins_cover_every_package_regardless_of_platform():
    packages = [_pkg('winonly', '1.0', markers='sys_platform == "win32"'), _pkg('devonly', '2.0', groups=('dev',))]

    assert lock_tools.render_pins(packages) == 'devonly==2.0\nwinonly==1.0\n'


def test_export_check_fails_and_does_not_write_when_out_of_date(tmp_path, monkeypatch, capsys):
    stale = tmp_path / 'requirements.txt'
    stale.write_text('old\n')
    monkeypatch.setattr(lock_tools, 'REQUIREMENTS', stale)

    assert lock_tools.export(check=True) == 1
    assert stale.read_text() == 'old\n'
    assert 'out of date' in capsys.readouterr().out

    assert lock_tools.export(check=False) == 0
    assert lock_tools.export(check=True) == 0
