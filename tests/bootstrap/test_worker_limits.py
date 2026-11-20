import hashlib
from enum import Enum
from textwrap import dedent
from types import SimpleNamespace

import pytest

from maas_bootstrap import worker, worker_limits


class Profiles(Enum):
    RUN = "run"
    INIT = "init"


def test_creation_limits_match_only_the_pinned_ocr_images_and_entrypoints():
    ovis = "registry.example/maas/ovisocr3-vllm@sha256:34ab50787af81b270477f49457ea8f8162eec231f7266ce9d6552c19a39e6b20"
    unlimited = "registry.example/maas/unlimited-ocr-2@sha256:0a4ee2f9351225fe2ff91fe1d7f638b86972b9b2f704ab848325f2d0bc0d2fa6"
    guarded_ovis = "registry.example/maas/ovisocr3-vllm@sha256:9e8e8270b98c3e9500926e65854747df6c5fd77c602897cf9eeac34f2101de42"
    guarded_unlimited = "registry.example/maas/unlimited-ocr-2@sha256:9139bd41a0476782acee4be89363a5c1188c9910ed9211ce84d06920ac582615"
    ovis_guard = ["python3", "/opt/maas/ovisocr3_entrypoint.py"]
    unlimited_guard = ["python3", "/opt/maas/resource_guard.py"]
    code = compile(dedent(worker_limits.INSERTION.decode()), "<limits>", "exec")
    for image, command, profile, name, applies in (
        (ovis, ovis_guard, Profiles.RUN, "default", True),
        (unlimited, unlimited_guard, Profiles.RUN, "default", True),
        (guarded_ovis, ovis_guard, Profiles.RUN, "default", True),
        (guarded_unlimited, unlimited_guard, Profiles.RUN, "default", True),
        (guarded_ovis, unlimited_guard, Profiles.RUN, "default", False),
        (unlimited, unlimited_guard, Profiles.INIT, "default", False),
        (unlimited, unlimited_guard, Profiles.RUN, "auxiliary", False),
        (ovis, unlimited_guard, Profiles.RUN, "default", False),
        (unlimited, ovis_guard, Profiles.RUN, "default", False),
        ("registry.example/maas/pp-ocr:latest", unlimited_guard, Profiles.RUN, "default", False),
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
    ovis_only = original.replace(worker_limits.ANCHOR, worker_limits.OVIS_INSERTION + worker_limits.ANCHOR)
    assert worker_limits.runtime_source(ovis_only) == patched
    base = original.replace(worker_limits.ANCHOR, worker_limits.BASE_INSERTION + worker_limits.ANCHOR)
    assert worker_limits.runtime_source(base) == patched
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
