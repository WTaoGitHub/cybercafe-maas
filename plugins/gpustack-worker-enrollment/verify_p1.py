"""Exercise enrollment against a fresh, disposable GPUStack v2.2.3 server."""

import asyncio
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

import httpx
from gpustack.config.config import Config
from gpustack.schemas.workers import WorkerStatusPublic
from gpustack.worker.worker_manager import WorkerManager


HOST = "127.0.0.1"
PORT = 30080
BASE = f"http://{HOST}:{PORT}"
ENROLL = "/internal/maas/worker-enrollment"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def check(response, status=200):
    assert response.status_code == status, (response.request.url.path, response.status_code, status)
    return response.json() if response.content else None


class SyntheticCollector:
    def __init__(self, folder, gpu_uuid):
        self.folder = folder
        self.gpu_uuid = gpu_uuid

    def timed_collect(self, **_):
        native_uuid = self.folder / "worker_uuid"
        return WorkerStatusPublic.model_validate({
            "hostname": "enrollment-lab-worker", "ip": HOST, "ifname": "lo", "port": 10150,
            "worker_uuid": native_uuid.read_text() if native_uuid.exists() else "",
            "status": {"gpu_devices": [{"index": 0, "vendor": "nvidia", "type": "cuda",
                                       "uuid": self.gpu_uuid, "name": "Synthetic NVIDIA"}]},
        })


async def register(folder, cluster_token, name, gpu_uuid, external_id=None):
    folder.mkdir()
    if external_id is not None:
        (folder / "external_id").write_text(external_id)
    assert not (folder / "worker_uuid").exists()
    cfg = Config(data_dir=str(folder), server_url=BASE, token=cluster_token, worker_name=name)
    collector = SyntheticCollector(folder, gpu_uuid)
    manager = WorkerManager(cfg=cfg, is_embedded=False, collector=collector)
    await manager.register_with_server()
    token = (folder / "worker_token").read_text().strip()
    with httpx.Client(base_url=BASE, headers=bearer(token), trust_env=False) as worker:
        rows = check(worker.get("/v2/workers", params={"me": "true"}))["items"]
        assert len(rows) == 1 and rows[0]["me"] is True
        check(worker.post("/v2/worker-status", json=collector.timed_collect().model_dump(mode="json")), 204)
    return rows[0]


def workers(admin, **params):
    return check(admin.get("/v2/workers", params=params))["items"]


def wait_for_row(admin, name):
    for _ in range(50):
        rows = workers(admin, name=name)
        if rows:
            assert len(rows) == 1
            return rows[0]
        time.sleep(0.1)
    raise AssertionError("The unread request did not commit")


async def main():
    with tempfile.TemporaryDirectory(prefix="worker-enrollment-") as tmp, httpx.Client(
        base_url=BASE, trust_env=False, timeout=10
    ) as admin:
        assert check(admin.get("/version"))["version"] == "v2.2.3"
        check(admin.post("/auth/login", data={"username": "admin",
                                             "password": os.environ["GPUSTACK_BOOTSTRAP_PASSWORD"]}))
        assert check(admin.get("/v2/clusters"))["items"] == [] and workers(admin) == [], "Fresh lab only"
        cluster = check(admin.post("/v2/clusters", json={"name": "enrollment-lab", "provider": "Docker"}))["id"]
        assert cluster == int(os.environ["MAAS_WORKER_ENROLLMENT_CLUSTER_ID"])
        cluster_token = check(admin.get(f"/v2/clusters/{cluster}/registration-token"))["token"]
        key = Path(os.environ["MAAS_WORKER_ENROLLMENT_KEY_FILE"]).read_text().strip()
        headers = bearer(key)
        gpu = f"GPU-{uuid4()}"
        body = {"cluster_id": cluster, "gpu_uuid": gpu}

        check(admin.post(ENROLL, json=body), 401)
        check(admin.post(ENROLL, json=body, headers=bearer(cluster_token)), 401)
        check(admin.post(ENROLL, json={**body, "cluster_id": cluster + 1}, headers=headers), 403)
        check(admin.post(ENROLL, json={**body, "gpu_uuid": "GPU-incomplete"}, headers=headers), 422)
        assert workers(admin) == []
        print("PASS service credential, scope and UUID validation", flush=True)

        async with httpx.AsyncClient(base_url=BASE, headers=headers, trust_env=False, timeout=10) as client:
            requests = [client.post(ENROLL, json={**body, "gpu_uuid": gpu.upper() if i % 2 else gpu})
                        for i in range(12)]
            results = [check(response) for response in await asyncio.gather(*requests)]
        first = results[0]
        assert all(result == first for result in results)
        assert first == {"cluster_id": cluster, "worker_id": first["worker_id"],
                         "worker_name": gpu, "external_id": gpu}
        row, = workers(admin, name=gpu)
        assert row["id"] == first["worker_id"] and row["state"] == "initializing"
        assert row["heartbeat_time"] is None and not row["status"]["gpu_devices"]
        print("PASS 12 concurrent enrollments produced one inactive record", flush=True)

        missing_response_gpu = f"GPU-{uuid4()}"
        lost_body = {**body, "gpu_uuid": missing_response_gpu}
        connection = HTTPConnection(HOST, PORT, timeout=5)
        try:
            connection.request("POST", ENROLL, json.dumps(lost_body), {**headers, "Content-Type": "application/json"})
            # Keep the request open until the server commits, then discard the unread response.
            committed = wait_for_row(admin, missing_response_gpu)
        finally:
            connection.close()
        recovered = check(admin.post(ENROLL, json=lost_body, headers=headers))
        assert recovered["worker_id"] == committed["id"]
        assert len(workers(admin, name=missing_response_gpu)) == 1
        print("PASS retry after the client disconnected returned the committed ID", flush=True)

        ordinary_gpu = f"GPU-{uuid4()}"
        ordinary = await register(Path(tmp) / "ordinary", cluster_token, ordinary_gpu, ordinary_gpu)
        assert ordinary["id"] != first["worker_id"] and ordinary["external_id"] is None
        check(admin.post(ENROLL, json={**body, "gpu_uuid": ordinary_gpu}, headers=headers), 409)
        assert check(admin.get(f"/v2/workers/{ordinary['id']}"))["external_id"] is None
        print("PASS ordinary native worker cannot consume a placeholder or be adopted by name", flush=True)

        registered = await register(Path(tmp) / "diskless", cluster_token, gpu, gpu, first["external_id"])
        assert registered["id"] == first["worker_id"] and registered["name"] == gpu
        assert registered["status"]["gpu_devices"][0]["uuid"] == gpu
        assert check(admin.post(ENROLL, json=body, headers=headers)) == first
        assert len(workers(admin)) == 3
        print("PASS native registration from an empty identity directory reused the enrolled ID", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
