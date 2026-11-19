import hashlib
from enum import Enum
from textwrap import dedent
from types import SimpleNamespace

import pytest

from maas_bootstrap import worker, worker_limits


class Profiles(Enum):
    RUN = "run"
    INIT = "init"


def test_ovisocr3_creation_options_do_not_change_other_containers():
    pinned = "registry.example/maas/ovisocr3-vllm@sha256:34ab50787af81b270477f49457ea8f8162eec231f7266ce9d6552c19a39e6b20"
    guarded = ["python3", "/opt/maas/ovisocr3_entrypoint.py"]
    code = compile(dedent(worker_limits.INSERTION.decode()), "<limits>", "exec")
    for image, command, profile, name, applies in (
        (pinned, guarded, Profiles.RUN, "default", True),
        (pinned, guarded, Profiles.INIT, "default", False),
        (pinned, guarded, Profiles.RUN, "auxiliary", False),
        (pinned, ["vllm", "serve"], Profiles.RUN, "default", False),
        ("registry.example/maas/pp-ocr:latest", guarded, Profiles.RUN, "default", False),
    ):
        options = {"entrypoint": command, "shm_size": 4 * 1024**3}
        expected = options.copy()
        if applies:
            expected.update(mem_limit=16 * 1024**3, memswap_limit=16 * 1024**3, nano_cpus=4_000_000_000)
        exec(code, {"ContainerProfileEnum": Profiles,
                    "c": SimpleNamespace(image=image, profile=profile, name=name),
                    "create_options": options})
        assert options == expected


def test_patch_is_idempotent_reversible_and_pinned(monkeypatch):
    original = b"class DockerDeployer:\n    def create(self):\n        if True:\n" + worker_limits.ANCHOR + b"            pass\n"
    monkeypatch.setattr(worker_limits, "ORIGINAL_SHA256", hashlib.sha256(original).hexdigest())
    patched = worker_limits.runtime_source(original)
    compile(patched, "<runtime>", "exec")
    assert worker_limits.runtime_source(patched) == patched
    assert worker_limits.runtime_source(patched, enabled=False) == original
    with pytest.raises(ValueError, match="Unrecognized"):
        worker_limits.runtime_source(original + b"\n")


def test_bootstrap_applies_patch_before_start_and_stops_on_install_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(worker_limits, "install_before_start", lambda name: calls.append(("install", name)))
    monkeypatch.setattr(worker, "run", lambda args: calls.append(tuple(args)))
    worker.NativeDockerWorker().start()
    assert calls == [("install", worker.NAME), ("docker", "start", worker.NAME)]

    def broken(_name):
        raise ValueError("Unrecognized runtime")

    calls.clear()
    monkeypatch.setattr(worker_limits, "install_before_start", broken)
    with pytest.raises(worker.BootstrapError, match="WORKER_RUNTIME_LIMITS_INSTALL_FAILED"):
        worker.NativeDockerWorker().start()
    assert not calls
