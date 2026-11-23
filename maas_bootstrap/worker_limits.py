"""Apply released inference container options on the pinned GPUStack Docker worker."""

from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import PurePosixPath
from .execution import CA_NAME, KEY_NAME, SERVING_DIRECTORY


WORKER_IMAGE_ID = "sha256:3bd2f66800ecf702f6c108f5237b2a77592d73457578c9e99315ea63203e2d26"
SOURCE = PurePosixPath("/usr/local/lib/python3.11/dist-packages/gpustack_runtime/deployer/docker.py")
ORIGINAL_SHA256 = "49ffc5e4b39f8956bde2ffca9543da911a4054ea6efc52f9850f577333ee997f"
ANCHOR = b"            # Create the container.\n"
# Compatibility limits for releases without a worker_runtime declaration.
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
UNLIMITED_INSERTION = OVIS_INSERTION.replace(
    b"34ab50787af81b270477f49457ea8f8162eec231f7266ce9d6552c19a39e6b20",
    b"0a4ee2f9351225fe2ff91fe1d7f638b86972b9b2f704ab848325f2d0bc0d2fa6",
).replace(b"/opt/maas/ovisocr3_entrypoint.py", b"/opt/maas/resource_guard.py")
BASE_INSERTION = OVIS_INSERTION + UNLIMITED_INSERTION
INSERTION = BASE_INSERTION + OVIS_INSERTION.replace(
    b"34ab50787af81b270477f49457ea8f8162eec231f7266ce9d6552c19a39e6b20",
    b"9e8e8270b98c3e9500926e65854747df6c5fd77c602897cf9eeac34f2101de42",
) + UNLIMITED_INSERTION.replace(
    b"0a4ee2f9351225fe2ff91fe1d7f638b86972b9b2f704ab848325f2d0bc0d2fa6",
    b"9139bd41a0476782acee4be89363a5c1188c9910ed9211ce84d06920ac582615",
)


def configured_insertion(config, execution_directory=None):
    limits = {}
    guards = {}
    for image in config['images']:
        match = (image['digest'], tuple(image['entrypoint']))
        if 'profile' in image:
            profile = config['profiles'][image['profile']]
            memory = profile['memory_mib'] * 1024**2
            limits[match] = dict(mem_limit=memory, memswap_limit=memory,
                                 nano_cpus=profile['cpu_millis'] * 1_000_000)
        if 'execution_env_prefix' in image:
            guards[match] = image['execution_env_prefix']
    options = ''
    if guards:
        if execution_directory is None:
            raise ValueError('Execution directory is required')
        files = {
            'KEY_FILE': (str(execution_directory / KEY_NAME), SERVING_DIRECTORY + '/' + KEY_NAME),
            'CA_FILE': (str(execution_directory / CA_NAME), SERVING_DIRECTORY + '/' + CA_NAME),
        }
        options = f'''                prefix = {guards!r}.get(maas_image)
                if prefix:
                    volumes = create_options.setdefault("mounts", [])
                    environment = create_options["environment"]
                    environment[prefix + "ORIGIN"] = {config['execution']['origin']!r}
                    for field, (source, target) in {files!r}.items():
                        if any(volume["Target"] == target for volume in volumes):
                            raise ValueError("MaaS execution mount target already in use")
                        volumes.append(docker.types.Mount(target, source, type="bind", read_only=True))
                        environment[prefix + field] = target
'''
    return (f'''            if c.profile == ContainerProfileEnum.RUN and c.name == "default":
                maas_image = (c.image.rpartition("@")[2], tuple(create_options.get("entrypoint") or ()))
                create_options.update({limits!r}.get(maas_image, {{}}))
''' + options + '\n').encode()


def runtime_source(current: bytes, *, enabled: bool = True, config=None, execution_directory=None) -> bytes:
    patch = INSERTION if config is None else configured_insertion(config, execution_directory)
    original = current
    for insertion in (patch, INSERTION, BASE_INSERTION, OVIS_INSERTION):
        if insertion + ANCHOR in current:
            original = current.replace(insertion + ANCHOR, ANCHOR, 1)
            break
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256 or original.count(ANCHOR) != 1:
        raise ValueError("Unrecognized GPUStack Docker runtime source")
    return original.replace(ANCHOR, patch + ANCHOR) if enabled else original


def docker(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ("docker", *args), input=data, capture_output=True, check=True, timeout=20,
    ).stdout


def install_before_start(container: str, *, config=None, execution_directory=None) -> None:
    """Update the recognized stopped worker before Docker launches it."""
    image, status = docker("inspect", "--format", "{{.Image}} {{.State.Status}}", container).decode().split()
    if image != WORKER_IMAGE_ID:
        if config is not None:
            raise ValueError("Unrecognized GPUStack worker image for released runtime policy")
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
    patched = runtime_source(current, config=config, execution_directory=execution_directory)
    if current == patched:
        return
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo(SOURCE.name)
        member.size = len(patched)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(patched))
    docker("cp", "-", f"{container}:{SOURCE.parent}", data=stream.getvalue())
