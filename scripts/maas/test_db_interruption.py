"""Run on controller; restart only our labeled disposable database, never GPUStack."""
import json
from pathlib import Path
import subprocess
import time

ROOT=Path('/root/wtaobj/maas-framework')
NAME='wta-maas-p1-test-db'


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True)


def main():
    info=json.loads(docker('inspect',NAME))[0]
    assert info['Config']['Labels'].get('cybercafe.role')=='p1-disposable-test'
    assert info['State']['Running']
    folder=ROOT/'p1-fault';folder.mkdir(exist_ok=True)
    for file in folder.glob('db.*'):file.unlink()
    marker=folder/'db'
    def wait(name):
        deadline=time.monotonic()+40
        while not marker.with_suffix('.'+name).exists():
            if process.poll() is not None:raise RuntimeError('Test exited before '+name)
            if time.monotonic()>deadline:raise TimeoutError(name)
            time.sleep(.1)
    with (ROOT/'p1-db-interruption.log').open('w') as log:
        process=subprocess.Popen(['docker','run','--rm','--network','host','--env-file',str(ROOT/'.secrets/test.env'),
            '-e','MAAS_DB_FAULT_MARKER=/work/p1-fault/db','-v',str(ROOT)+':/work','-w','/work',
            'python:3.11-slim','/work/.venv/bin/python','-m','pytest',
            'tests/maas/test_integration.py::test_real_postgres_interruption','-q',
            '--junitxml=/work/p1-db-interruption.xml'],stdout=log,stderr=subprocess.STDOUT)
        try:
            wait('ready')
            docker('stop','-t','1',NAME);marker.with_suffix('.stopped').touch()
            wait('observed')
            docker('start',NAME)
            for _ in range(100):
                if subprocess.run(['docker','exec',NAME,'pg_isready','-U','maas'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
                time.sleep(.1)
            else:raise RuntimeError('PostgreSQL restart not ready')
            marker.with_suffix('.restarted').touch()
            assert process.wait(timeout=40)==0,'See p1-db-interruption.log'
            print('PASS: real isolated PostgreSQL stop/start; API fails closed; task recovered once')
        finally:
            docker('start',NAME)
            if process.poll() is None:process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
