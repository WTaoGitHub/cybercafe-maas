"""Run pytest on this file with MAAS_TEST_DATABASE_URL set to a disposable *_test DB.

Checks finish delivery to the normal callback using real PostgreSQL and loopback TLS.
The engine is synthetic. The separate GPU trial is recorded in issue #2.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import socket
import ssl
import subprocess
import sys
from threading import Thread
from urllib.error import URLError
from urllib.request import build_opener, HTTPSHandler, ProxyHandler

from fastapi import HTTPException
import pytest

from maas_bootstrap import execution
from maas_bootstrap.client import LocalState, ReleaseVerifier, atomic_json
from maas_platform.pp_ocr_execution import PATH
from tests.bootstrap.test_client import setup as bootstrap_settings
from test_execution_scope_integration import create_attempt, env, guarded, protected
from test_shutdown_transport_integration import certificate


def activity(service, token):
    with service.db.transaction() as conn:
        return conn.execute(
            "SELECT state, gpu_finished_at, gpu_instance FROM backend_activity WHERE token=%s",
            (token,),
        ).fetchone()


@contextmanager
def tls_receiver(handler, cert, key):
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    with HTTPServer(("127.0.0.1", 0), handler) as server:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize("fault", ["return_503", "close_connection"])
def test_retry_after_committed_finish(protected, tmp_path, monkeypatch, fault):
    client, service, (owner, _), *_ = protected
    token, _ = create_attempt(protected, "task")
    attempt = "task." + token
    owner.report(attempt, finished=False)
    committed_at = []

    class LoseFirstAck(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            response = client.post(PATH, content=payload, headers={
                "Authorization": self.headers["Authorization"], "Content-Type": "application/json"})
            committed_at.append(activity(service, token)["gpu_finished_at"])
            status = response.status_code
            if status == 204 and len(committed_at) == 1:
                if fault == "close_connection":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                status = 503
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args):
            pass

    cert, key = certificate(tmp_path, "test_agent_finish")
    with tls_receiver(LoseFirstAck, cert, key) as origin:
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=cert)))
        monkeypatch.setattr(owner, "url", origin + PATH)
        monkeypatch.setattr(owner, "open", opener.open)
        owner.report_finished(attempt)
    row = activity(service, token)
    assert row["state"] == "COMPLETED" and row["gpu_finished_at"]
    assert committed_at == [row["gpu_finished_at"]] * 2


def configure_host(bootstrap_settings, tmp_path, gpu, key, origin, ca):
    settings, sign = bootstrap_settings
    settings.data.update(allowed_server_urls=["https://test-agent-native.invalid"],
                         worker_start_grant_file=str(tmp_path / "test_agent_grant"))
    config_file = tmp_path / "test_agent_host.json"
    atomic_json(config_file, settings.data)
    image = {"digest": "sha256:" + "a" * 64, "entrypoint": ["python3", "/opt/engine.py"],
             "execution_env_prefix": "MAAS_PP_OCR_EXECUTION_", "receipt_uid": os.geteuid()}
    manifest = dict(sign()["manifest"], server_url=settings.data["allowed_server_urls"][0],
                    worker_name_policy="gpu-external-id", worker_runtime={
                        "images": [image], "profiles": {}, "execution": {"origin": origin, "ca_pem": ca}})
    envelope = sign(manifest)
    del envelope["registration"]["token"]
    metadata = ReleaseVerifier().verify(settings, envelope)
    state = LocalState(settings.data["state_dir"])
    state.prepare(envelope, metadata)
    execution.prepare(settings, manifest, {"execution_key": key})
    atomic_json(state.root / "identity.json", {"gpu_uuid": gpu, "fingerprint": metadata["fingerprint"]})
    return config_file, execution.receipt_directory(execution.local_directory(settings), image)


@pytest.mark.parametrize("persist", [False, True], ids=["no-local-receipt", "worker-local-receipt"])
def test_finish_delivery_after_worker_replacement(protected, bootstrap_settings, tmp_path, monkeypatch, persist):
    client, service, (owner, _), *_ = protected
    token, lose_result = create_attempt(protected, "task")
    attempt = "task." + token
    gpu = owner.identity["gpu_uuid"]
    key = json.loads(service.settings.pp_ocr_worker_keys_file.read_text())[gpu]
    cert, private_key = certificate(tmp_path, "test_agent_worker_replay")
    requests = []

    class ReplayReceiver(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == PATH
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(payload)
            if len(requests) == 1:
                self.send_response(503)
            else:
                response = client.post(PATH, content=payload, headers={
                    "Authorization": self.headers["Authorization"], "Content-Type": "application/json"})
                if response.status_code == 204 and len(requests) == 2:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.send_response(response.status_code)
            self.end_headers()

        def log_message(self, *args):
            pass

    with tls_receiver(ReplayReceiver, cert, private_key) as origin:
        config_file, folder = configure_host(bootstrap_settings, tmp_path, gpu, key, origin, cert.read_text())
        owner.receipt_dir = folder if persist else None
        _finish_while_offline(owner, attempt, lose_result, monkeypatch)
        assert activity(service, token)["state"] == "UNKNOWN"
        replacement = type(owner)("https://test-agent-worker.test", key, gpu, owner.identity["route"])
        replacement.open = owner.open
        with pytest.raises(HTTPException, match="409"):
            replacement.report(attempt, finished=True)

        def host_tick():
            # Each activation gets a fresh process, with only the host configuration and files.
            return subprocess.check_output([sys.executable, "-B", "-m", "maas_bootstrap.receipt_replay",
                                            "--config", str(config_file)], text=True, timeout=15).strip()

        if not persist:
            assert host_tick() == "" and not requests
            assert activity(service, token)["state"] == "UNKNOWN"
            return
        receipt = folder / attempt
        _, original = receipt.read_bytes().split(b"\n", 1)
        assert json.loads(original)["instance"] == owner.identity["instance"] != replacement.identity["instance"]
        assert json.loads(host_tick()) == {"unavailable": 1}
        assert receipt.is_file() and activity(service, token)["state"] == "UNKNOWN"
        assert json.loads(host_tick()) == {"unavailable": 1}
        settled = activity(service, token)
        assert receipt.is_file() and settled["state"] == "COMPLETED" and settled["gpu_finished_at"]
        assert json.loads(host_tick()) == {"acknowledged": 1}
        assert not receipt.exists() and requests == [original] * 3
        assert activity(service, token) == settled
        assert host_tick() == ""
        replacement.receipt_dir = folder
        replacement.save_finish(attempt)
        invalid = folder / ("sync." + "f" * 32)
        invalid.write_bytes(b"corrupt-test-receipt")
        assert json.loads(host_tick()) == {"rejected": 1, "invalid_or_unreadable": 1}
        assert receipt.is_file() and invalid.is_file() and activity(service, token) == settled
        assert len(requests) == 4
    with service.db.transaction() as conn:
        task = conn.execute("SELECT t.state FROM tasks t JOIN backend_activity b ON b.task_id=t.id "
                            "WHERE b.token=%s", (token,)).fetchone()
        assert task["state"] == "FAILED"
        assert conn.execute("SELECT count(*) AS n FROM task_usage_events").fetchone()["n"] == 0


def _finish_while_offline(owner, attempt, lose_result, monkeypatch):
    owner.report(attempt, finished=False)

    def offline(request, timeout):
        raise URLError("test_agent_authority_offline")

    with monkeypatch.context() as patch:
        patch.setattr(owner, "open", offline)
        with pytest.raises(HTTPException, match="503"):
            owner.report_finished(attempt)
    lose_result()
