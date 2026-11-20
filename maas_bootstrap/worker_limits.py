"""Install OCR serving container limits on the pinned GPUStack Docker worker."""

from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import PurePosixPath


WORKER_IMAGE_ID = "sha256:3bd2f66800ecf702f6c108f5237b2a77592d73457578c9e99315ea63203e2d26"
SOURCE = PurePosixPath("/usr/local/lib/python3.11/dist-packages/gpustack_runtime/deployer/docker.py")
ORIGINAL_SHA256 = "49ffc5e4b39f8956bde2ffca9543da911a4054ea6efc52f9850f577333ee997f"
ANCHOR = b"            # Create the container.\n"
# shortcut: match the validated worker and serving image digests until upstream exposes hard limits.
OVIS_INSERTION = b'''            if (
                c.profile == ContainerProfileEnum.RUN
                and c.name == "default"
                and c.image.endswith("@sha256:34ab50787af81b270477f49457ea8f8162eec231f7266ce9d6552c19a39e6b20")
                and create_options.get("entrypoint") == ["python3", "/opt/maas/ovisocr3_entrypoint.py"]
            ):
                create_options.update(
                    mem_limit=16 * 1024**3,
                    memswap_limit=16 * 1024**3,
                    nano_cpus=4_000_000_000,
                )

'''
INSERTION = OVIS_INSERTION + OVIS_INSERTION.replace(
    b"34ab50787af81b270477f49457ea8f8162eec231f7266ce9d6552c19a39e6b20",
    b"0a4ee2f9351225fe2ff91fe1d7f638b86972b9b2f704ab848325f2d0bc0d2fa6",
).replace(b"/opt/maas/ovisocr3_entrypoint.py", b"/opt/maas/resource_guard.py")


def runtime_source(current: bytes, *, enabled: bool = True) -> bytes:
    original = current
    for insertion in (INSERTION, OVIS_INSERTION):
        if insertion + ANCHOR in current:
            original = current.replace(insertion + ANCHOR, ANCHOR, 1)
            break
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256 or original.count(ANCHOR) != 1:
        raise ValueError("Unrecognized GPUStack Docker runtime source")
    return original.replace(ANCHOR, INSERTION + ANCHOR) if enabled else original


def docker(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ("docker", *args), input=data, capture_output=True, check=True, timeout=20,
    ).stdout


def install_before_start(container: str) -> None:
    """Update the recognized stopped worker before Docker launches it."""
    image, status = docker("inspect", "--format", "{{.Image}} {{.State.Status}}", container).decode().split()
    if image != WORKER_IMAGE_ID:
        return
    if status not in {"created", "exited"}:
        raise ValueError("GPUStack worker must be stopped before installing runtime limits")
    source = docker("cp", f"{container}:{SOURCE}", "-")
    try:
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:") as archive:
            member, = archive.getmembers()
            if not member.isfile() or member.name != SOURCE.name or member.size > 256 * 1024:
                raise ValueError("Unexpected GPUStack runtime archive")
            current = archive.extractfile(member).read()
    except tarfile.TarError as error:
        raise ValueError("Invalid GPUStack runtime archive") from error
    patched = runtime_source(current)
    if current == patched:
        return
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo(SOURCE.name)
        member.size = len(patched)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(patched))
    docker("cp", "-", f"{container}:{SOURCE.parent}", data=stream.getvalue())
