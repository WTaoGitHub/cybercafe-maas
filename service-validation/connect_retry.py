"""Establish a private reusable SSH channel without changing sshd.

Connection details belong in an ignored mode-600 JSON file, never in source.
See docs/handbooks/PRIVATE-CONNECTIONS.md. Importing does not connect.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import time


def load_command(path):
    path = Path(path).expanduser()
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('SSH command configuration must have mode 600')
    command = json.loads(path.read_text())
    if (not isinstance(command, list) or len(command) < 2 or command[0] != 'ssh'
            or any(not isinstance(arg, str) or '\x00' in arg for arg in command)):
        raise ValueError('Expected a private JSON array containing an SSH command')
    return command


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command-file', type=Path,
                        default=Path(os.environ.get('MAAS_CONTROLLER_SSH_COMMAND',
                                                   root.parent / '.maas/ssh/controller-command.json')))
    args = parser.parse_args()
    try:
        command = load_command(args.command_file)
    except (OSError, ValueError):
        parser.exit(2, 'Missing or invalid private SSH configuration; see PRIVATE-CONNECTIONS.md\n')
    sock = root / 'ssh-001.sock'
    for attempt in range(20):
        # SSH diagnostics can contain endpoints and usernames: keep logs private.
        fd = os.open(root / 'ssh-master.log', os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'a') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            for _ in range(30):
                if process.poll() is not None:
                    break
                if sock.exists():
                    check = subprocess.run(['ssh', '-S', str(sock), '-O', 'check', 'configured-controller'],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    if check.returncode == 0:
                        print('Foreground reusable controller SSH connection established.', flush=True)
                        return process.wait()
                time.sleep(.2)
            if process.poll() is None:
                process.terminate()
                process.wait()
        print('SSH capacity retry', attempt + 1, flush=True)
        time.sleep(4)
    parser.exit(1, 'Controller SSH connection limit persists\n')


if __name__ == '__main__':
    raise SystemExit(main())
