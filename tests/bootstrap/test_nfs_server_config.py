import ipaddress
from pathlib import Path
import re
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[2] / 'deploy/bootstrap/nfs-server'


def test_static_nfs_network_is_consistent_and_readonly():
    exports = [line for line in (ROOT / 'maas-models.exports.example').read_text().splitlines()
               if line and not line.startswith('#')]
    assert len(exports) == 1
    path, client = exports[0].split()
    assert path == '/root/maas/data/modelsWeights'
    source, options = client.split('(', 1)
    assert ipaddress.IPv4Network(source, strict=True) == ipaddress.IPv4Network('10.11.0.0/16')
    flags = set(options.rstrip(')').split(','))
    assert {'ro', 'subtree_check', 'root_squash', 'all_squash'} <= flags
    assert not {'rw', 'no_root_squash', 'no_all_squash', 'no_subtree_check'} & flags

    dropin = (ROOT / '20-maas-model-nfs-network.conf.example').read_text()
    assert re.findall(r'-C MAAS-MODEL-NFS -s (\S+) -j RETURN', dropin) == [source]
    assert '-C MAAS-MODEL-NFS -p tcp -j REJECT --reject-with tcp-reset' in dropin
    assert '-j ACCEPT' not in dropin
    for line in dropin.splitlines():
        if line.startswith('ExecStartPre='):
            argv = shlex.split(line.split('=', 1)[1])
            assert argv[:2] == ['/bin/sh', '-ec']
            subprocess.run(['/bin/sh', '-n', '-c', argv[2]], check=True, capture_output=True)
