"""Exercise worker handoff and stale-client fencing in a disposable v2.2.3 server."""

import asyncio
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import httpx

from verify_p1 import BASE, ENROLL, SyntheticCollector, bearer, check, register, workers


HANDOFF = "/internal/maas/worker-handoff"
REGISTER = "/v2/workers"
HEARTBEAT = "/v2/worker-heartbeat"
STATUS = "/v2/worker-status"


def lab_sql(statement):
    subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", "gpustack", "-XAtqc", statement], check=True)


def offline(worker_id):
    lab_sql("UPDATE workers SET state = 'NOT_READY', "
            "heartbeat_time = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - interval '1 hour' "
            f"WHERE id = {worker_id}")


async def main():
    with tempfile.TemporaryDirectory(prefix="worker-handoff-") as tmp, httpx.Client(
        base_url=BASE, trust_env=False, timeout=10
    ) as admin:
        assert os.environ["MAAS_WORKER_HANDOFF_MODE"] == "required"
        assert check(admin.get("/version"))["version"] == "v2.2.3"
        check(admin.post("/auth/login", data={"username": "admin",
                                             "password": os.environ["GPUSTACK_BOOTSTRAP_PASSWORD"]}))
        assert check(admin.get("/v2/clusters"))["items"] == [] and workers(admin) == [], "Fresh lab only"
        cluster = check(admin.post("/v2/clusters", json={"name": "handoff-lab", "provider": "Docker"}))["id"]
        assert cluster == int(os.environ["MAAS_WORKER_ENROLLMENT_CLUSTER_ID"])
        cluster_token = check(admin.get(f"/v2/clusters/{cluster}/registration-token"))["token"]
        maas_key = Path(os.environ["MAAS_WORKER_ENROLLMENT_KEY_FILE"]).read_text().strip()
        maas = bearer(maas_key)

        def enroll(gpu):
            return check(admin.post(ENROLL, headers=maas, json={"cluster_id": cluster, "gpu_uuid": gpu}))["worker_id"]

        gpu, other_gpu = f"GPU-{uuid4()}", f"GPU-{uuid4()}"
        worker_id, other_id = enroll(gpu), enroll(other_gpu)
        old_folder, new_folder = Path(tmp) / "old", Path(tmp) / "new"
        payload = {**SyntheticCollector(old_folder, gpu).timed_collect().model_dump(mode="json"),
                   "name": gpu, "external_id": gpu, "labels": {}}

        def post(path, token, data=None, expected=200):
            with httpx.Client(base_url=BASE, trust_env=False, timeout=10) as peer:
                return check(peer.post(path, headers=bearer(token), json=data), expected)

        def issue(worker, session_id, previous=None, receipt=None, expected=200):
            return post(HANDOFF, maas_key, {"worker_id": worker, "session_id": session_id,
                                           "previous_session_id": previous, "fence_receipt": receipt}, expected)

        post(HANDOFF, cluster_token, {"worker_id": worker_id, "session_id": str(uuid4())}, 401)
        post(REGISTER, cluster_token, payload, 403)
        post(REGISTER + "/", cluster_token, payload, 403)

        contenders = [{"worker_id": worker_id, "session_id": str(uuid4())} for _ in range(2)]
        async with httpx.AsyncClient(base_url=BASE, headers=maas, trust_env=False, timeout=10) as client:
            responses = await asyncio.gather(*(client.post(HANDOFF, json=body) for body in contenders))
        assert sorted(response.status_code for response in responses) == [200, 409]
        first = next(response.json() for response in responses if response.status_code == 200)
        session_a, permit_a = first["session_id"], first["registration_token"]
        post(REGISTER, permit_a, {**payload, "name": other_gpu, "external_id": other_gpu}, 403)
        post(REGISTER, permit_a, {**payload, "cluster_id": cluster + 1}, 403)
        old = await register(old_folder, permit_a, gpu, gpu, gpu)
        assert old["id"] == worker_id
        assert workers(admin, name=gpu)[0]["state"] == "ready"
        token_a = (old_folder / "worker_token").read_text().strip()
        uuid_a = (old_folder / "worker_uuid").read_text().strip()
        old_status = SyntheticCollector(old_folder, gpu).timed_collect().model_dump(mode="json")
        with httpx.Client(base_url=BASE, trust_env=False) as peer:
            for path in ("registration-token", "manifests"):
                check(peer.get(f"/v2/clusters/{cluster}/{path}", headers=bearer(token_a)), 403)
            check(peer.put(f"/v2/workers/{worker_id}", headers=bearer(cluster_token), json={"name": gpu}), 403)
            check(peer.delete(f"/v2/workers/{worker_id}/", headers=bearer(cluster_token)), 403)
            check(peer.put(f"/v2/clusters/{cluster}", headers=bearer(cluster_token),
                           json={"name": "handoff-lab"}), 403)
        assert check(admin.get(f"/v2/clusters/{cluster}/registration-token"))["token"] == cluster_token
        print("PASS native system credentials cannot read the join secret or mutate protected bindings", flush=True)
        post(REGISTER, permit_a, payload, 403)
        print("PASS one permit for competing sessions; binding and replay enforced", flush=True)

        session_b = str(uuid4())
        issue(worker_id, session_b, session_a, "verified-lab-fence", expected=409)
        offline(worker_id)
        issue(worker_id, session_b, session_a, expected=409)
        post(HEARTBEAT, token_a, expected=204)
        issue(worker_id, session_b, session_a, "verified-lab-fence", expected=409)
        post(STATUS, token_a, old_status, 204)
        assert workers(admin, name=gpu)[0]["state"] == "ready"
        print("PASS active or recently heard worker blocks handoff; offline alone grants nothing", flush=True)

        offline(worker_id)
        second = issue(worker_id, session_b, session_a, "verified-lab-fence")
        permit_b = second["registration_token"]
        assert second["worker_id"] == worker_id
        post(HEARTBEAT, token_a, expected=401)
        post(STATUS, token_a, old_status, 401)
        with httpx.Client(base_url=BASE, trust_env=False) as old_peer:
            check(old_peer.post(HEARTBEAT, headers={"X-API-Key": token_a}), 401)
        post(REGISTER, cluster_token, payload, 403)
        post(REGISTER, cluster_token, {**payload, "external_id": None, "name": "old-name",
                                      "worker_uuid": uuid_a}, 403)
        post(REGISTER, cluster_token, {**payload, "external_id": None,
                                      "labels": {"gpustack.existence-check": "true"}}, 403)
        post(REGISTER, permit_a, payload, 403)
        issue(worker_id, str(uuid4()), session_a, "replayed-lab-fence", expected=409)

        new = await register(new_folder, permit_b, gpu, gpu, gpu)
        assert new["id"] == worker_id
        token_b = (new_folder / "worker_token").read_text().strip()
        uuid_b = (new_folder / "worker_uuid").read_text().strip()
        assert token_b != token_a and uuid_b != uuid_a
        post(HEARTBEAT, token_b, expected=204)
        post(STATUS, token_b, old_status, 409)
        post(HEARTBEAT, token_a, expected=401)
        post(STATUS, token_a, old_status, 401)
        post(REGISTER, permit_b, payload, 403)
        post(REGISTER, token_a, payload, 403)
        assert workers(admin, name=gpu)[0]["state"] == "ready"
        print("PASS old token, shared cluster token and legacy registration cannot reclaim the worker", flush=True)

        expiring_session = str(uuid4())
        expiring = issue(other_id, expiring_session)
        lab_sql(f"UPDATE maas_worker_handoffs SET expires_at = now() - interval '1 second' WHERE worker_id = {other_id}")
        post(REGISTER, expiring["registration_token"], {**payload, "name": other_gpu, "external_id": other_gpu}, 403)
        issue(other_id, str(uuid4()), expected=409)
        print("PASS expiry requires explicit reconciliation", flush=True)

        next_permit = issue(other_id, str(uuid4()), expiring_session, "unused-lab-permit-reviewed")["registration_token"]
        other_payload = {**payload, "name": other_gpu, "external_id": other_gpu}
        async with httpx.AsyncClient(base_url=BASE, headers=bearer(next_permit), trust_env=False, timeout=10) as client:
            attempts = await asyncio.gather(*(client.post(REGISTER, json=other_payload) for _ in range(2)))
        assert sorted(response.status_code for response in attempts) == [200, 403]
        assert next(response.json() for response in attempts if response.status_code == 200)["id"] == other_id
        print("PASS concurrent native requests cannot both consume a permit", flush=True)

        ordinary = await register(Path(tmp) / "ordinary", cluster_token, "ordinary-lab-worker", f"GPU-{uuid4()}")
        assert ordinary["id"] not in {worker_id, other_id} and ordinary["external_id"] is None
        print("PASS ordinary worker registration and reporting remain available", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
