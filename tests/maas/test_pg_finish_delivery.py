"""Run pytest on this file with MAAS_TEST_DATABASE_URL set to a disposable *_test DB.

Checks finish delivery to the normal callback using real PostgreSQL and loopback TLS.
The engine is synthetic. The separate GPU trial is recorded in issue #2.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import socket
import ssl
from threading import Thread
from urllib.error import URLError
from urllib.request import build_opener, HTTPSHandler, ProxyHandler

from fastapi import HTTPException
import pytest

from maas_platform.pp_ocr_execution import PATH
from maas_platform.receipt_reconcile import read_receipt
from test_execution_scope_integration import create_attempt, env, guarded, protected
from test_shutdown_transport_integration import certificate


def activity(service, token):
    with service.db.transaction() as conn:
        return conn.execute(
            "SELECT state, gpu_finished_at, gpu_instance FROM backend_activity WHERE token=%s",
            (token,),
        ).fetchone()


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
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    with HTTPServer(("127.0.0.1", 0), LoseFirstAck) as proxy:
        proxy.socket = tls.wrap_socket(proxy.socket, server_side=True)
        thread = Thread(target=proxy.serve_forever)
        thread.start()
        try:
            opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=cert)))
            monkeypatch.setattr(owner, "url", f"https://127.0.0.1:{proxy.server_port}{PATH}")
            monkeypatch.setattr(owner, "open", opener.open)
            owner.report_finished(attempt)
        finally:
            proxy.shutdown()
            thread.join()
            row = activity(service, token)
            assert row["state"] == "COMPLETED" and row["gpu_finished_at"] is not None
            assert committed_at and all(t == row["gpu_finished_at"] for t in committed_at)
    assert len(committed_at) == 2


@pytest.mark.parametrize("persist", [False, True], ids=["no-local-receipt", "worker-local-receipt"])
def test_finish_delivery_after_worker_replacement(protected, tmp_path, monkeypatch, persist):
    client, service, (owner, _), *_ = protected
    token, lose_result = create_attempt(protected, "task")
    attempt = "task." + token
    gpu = owner.identity["gpu_uuid"]
    key = json.loads(service.settings.pp_ocr_worker_keys_file.read_text())[gpu]
    owner.receipt_dir = tmp_path if persist else None
    owner.report(attempt, finished=False)

    def offline(request, timeout):
        raise URLError("test_agent_authority_offline")

    with monkeypatch.context() as patch:
        patch.setattr(owner, "open", offline)
        with pytest.raises(HTTPException, match="503"):
            owner.report_finished(attempt)
    lose_result()
    assert activity(service, token)["state"] == "UNKNOWN"

    replacement = type(owner)("https://test-agent-worker.test", key, gpu, owner.identity["route"])
    replacement.open = owner.open
    with pytest.raises(HTTPException, match="409"):
        replacement.report(attempt, finished=True)
    if not persist:
        assert not (tmp_path / attempt).exists()
        assert activity(service, token)["state"] == "UNKNOWN"
        return

    # Replay the signed original event through the API, without mounting worker storage on MaaS.
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        original = read_receipt(directory, attempt, gpu, key)
    finally:
        os.close(directory)
    assert original.instance == owner.identity["instance"] != replacement.identity["instance"]
    assert client.post(PATH, json=original.model_dump(), headers=replacement.headers).status_code == 204
    settled = activity(service, token)
    assert settled["state"] == "COMPLETED" and settled["gpu_finished_at"] is not None
    assert client.post(PATH, json=original.model_dump(), headers=replacement.headers).status_code == 204
    assert activity(service, token) == settled
    with service.db.transaction() as conn:
        task = conn.execute("SELECT t.state FROM tasks t JOIN backend_activity b ON b.task_id=t.id "
                            "WHERE b.token=%s", (token,)).fetchone()
        assert task["state"] == "FAILED"
        assert conn.execute("SELECT count(*) AS n FROM task_usage_events").fetchone()["n"] == 0
