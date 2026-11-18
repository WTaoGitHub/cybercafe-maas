"""Real local TLS to MaaS/PostgreSQL; the OS shutdown trigger remains simulated."""
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import uvicorn

from maas_bootstrap import shutdown_events as sender
from maas_bootstrap.client import atomic_json
from maas_bootstrap.shutdown_auth import issue
from maas_platform.api import create_app
from test_integration import env


def certificate(tmp_path, name):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'local-shutdown-test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        .sign(key,hashes.SHA256()))
    cert_path, key_path = tmp_path/(name+'.crt'), tmp_path/(name+'.key')
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    key_path.chmod(0o600)
    return cert_path,key_path


def test_real_https_sender_records_separate_events_and_rejects_wrong_ca(env,tmp_path,monkeypatch,capsys):
    _, service, settings, auth, _ = env
    master = tmp_path/'issuer-key';master.write_text(bytes(range(32)).hex());master.chmod(0o600)
    settings.shutdown_event_key_file = master
    cert, private_key = certificate(tmp_path,'maas')
    listener = socket.socket()
    listener.bind(('127.0.0.1',0));listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings,auth),host='127.0.0.1',port=port,
        ssl_certfile=str(cert),ssl_keyfile=str(private_key),log_config=None,log_level='critical',access_log=False))
    thread = threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.01)
        assert server.started
        root = tmp_path/'state';root.mkdir(mode=0o700)
        boot = tmp_path/'boot';boot.write_text(str(uuid4()))
        scope = dict(cluster_id=1,worker_id=42,gpu_uuid='GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                     boot_id=boot.read_text(),session_id=str(uuid4()))
        identity = {k:v for k,v in scope.items() if k != 'boot_id'}
        atomic_json(root/'identity.json',identity)
        config = dict(state_dir=str(root),shutdown_events={
            'url':f'https://127.0.0.1:{port}'+sender.PATH,'tls_certificate_file':str(cert)})
        config_file = tmp_path/'bootstrap.json';atomic_json(config_file,config)
        monkeypatch.setattr(sender,'BOOT_ID',boot)
        monkeypatch.setattr(sender,'guard_clone_host',lambda:None)
        monkeypatch.setattr(sender,'is_shutdown',lambda deadline:True)
        assert sender.prepare(SimpleNamespace(data=config),identity,issue(master,scope))['code']=='SHUTDOWN_EVENT_PREPARED'
        elapsed = []
        for _ in range(2):
            started = time.monotonic()
            assert sender.main(['--config',str(config_file)])==0
            elapsed.append(time.monotonic()-started)
            assert json.loads(capsys.readouterr().out)=={'code':'SHUTDOWN_EVENT_SENT'}
        with service.db.transaction() as conn:
            rows=conn.execute('SELECT * FROM worker_shutdown_events ORDER BY received_at').fetchall()
            assert len(rows)==2 and rows[0]['event_id']!=rows[1]['event_id']
            assert all(row['expires_at']-row['received_at']==timedelta(hours=48) for row in rows)
            assert conn.execute('SELECT count(*) n FROM tasks').fetchone()['n']==0
        assert max(elapsed)<1
        wrong_ca,_ = certificate(tmp_path,'other-ca')
        config['shutdown_events']['tls_certificate_file']=str(wrong_ca)
        atomic_json(config_file,config)
        sender.prepare(SimpleNamespace(data=config),identity,issue(master,scope))
        assert sender.main(['--config',str(config_file)])==0
        assert json.loads(capsys.readouterr().out)=={'code':'SHUTDOWN_EVENT_UNAVAILABLE'}
        with service.db.transaction() as conn:
            assert conn.execute('SELECT count(*) n FROM worker_shutdown_events').fetchone()['n']==2
    finally:
        server.should_exit=True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()
