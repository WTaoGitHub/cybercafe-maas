"""Offline checks for private SSH command loading; never contact a server."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def connector():
    spec = importlib.util.spec_from_file_location(
        'private_connection_loader', ROOT / 'service-validation/connect_retry.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_command_round_trip_without_connecting(tmp_path, connector, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Loading configuration must not connect')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    path = tmp_path / 'command.json'
    command = ['ssh', '-o', 'StrictHostKeyChecking=yes', 'controller-example']
    path.write_text(json.dumps(command))
    path.chmod(0o600)
    assert connector.load_command(path) == command


def test_group_readable_command_rejected(tmp_path, connector):
    path = tmp_path / 'command.json'
    path.write_text('["ssh", "controller-example"]')
    path.chmod(0o640)
    with pytest.raises(ValueError, match='mode 600'):
        connector.load_command(path)


@pytest.mark.parametrize('value', [
    'ssh controller-example', ['sh', '-c', 'echo forbidden'],
    ['ssh'], ['ssh', 42], ['ssh', 'invalid\x00argument'],
])
def test_invalid_command_format_rejected(tmp_path, connector, value):
    path = tmp_path / 'command.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    with pytest.raises(ValueError):
        connector.load_command(path)


def test_real_connection_files_ignored_and_not_tracked():
    private = ['.codex/config.toml', '.codex/ssh-servers.json',
               'ssh-config.proposed.json', 'codex-mcp.proposed.toml',
               'service-validation/ssh-jump-debug.log',
               '.maas/ssh/config', '.maas/ssh/controller-command.json']
    tracked = set(subprocess.check_output(
        ['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
    assert not tracked.intersection(private)
    result = subprocess.run(['git', 'check-ignore', '--no-index', *private],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    assert set(result.stdout.splitlines()) == set(private)
