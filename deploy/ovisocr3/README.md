# OvisOCR3 on GPUStack with NFS weights

The shared test service uses the `OvisOCR3-vLLM-engine-custom` backend, version `native-entry-r1`. GPUStack runs `vllm serve` with the existing model arguments. The historical guard entrypoint below belongs to the earlier pilot. See the [active configuration and acceptance results](../../docs/framework/DYNAMIC-CONCURRENCY-IMPLEMENTATION.md#三个现役模型切换).

Model revision: `5b252386ba615bd464a5a26597560eaba87dae5c`. Runtime base: official Linux amd64 `vllm/vllm-openai:v0.22.1`, manifest `sha256:55c9bcee9fc66644b139fddae8a7a03e4c0c8a25ab5c64b0ce614554a8abf5d5` (CUDA 13.0.2). Publish both the fixed base and the small derived runtime to the existing private Registry before deployment. Deploy the derived image by its verified manifest digest.

The model package includes all 37 upstream files, including 18 task prompts. Download outside the NFS export, verify sizes and LFS SHA-256 or Git blob IDs against the official fixed revision, then atomically publish `/root/maas/data/modelsWeights/ovisocr3-5b252386`. Do not overwrite a published version. Workers load `/srv/maas/models/ovisocr3-5b252386`; do not copy weights into their local model cache.

## Build and configure

Create a private build context containing this `Dockerfile`, `entrypoint.py`, `vllm_guard.py`, `ovis_scheduler.py`, the shared [resource_guard.py](../unlimited-ocr-2/resource_guard.py), and the shared [execution_guard.py](../../gpu-ocr/execution_guard.py). Pass `BASE_IMAGE` as a verified Registry reference with the fixed base manifest digest. The derived image adds the startup checks and optional MaaS execution guard without changing inference dependencies. Keep credentials outside the build context.

Fill the Registry, cluster and owner placeholders in [gpustack-template.json](gpustack-template.json), and verify the pinned runtime digest. Keep `worker_selector={}` and `gpu_selector=null`; GPUStack chooses placement. The 8GiB claim corresponds to the initial 0.50 GPU budget, not measured minimum memory. The initial profile uses BF16, one image/request, concurrency one, 16K context and at most 1440² pixels. Send at most 4096 output tokens for initial short-page requests.

The GPUStack v2.2.3 Docker worker installs the [creation-time limits hook](../../maas_bootstrap/worker_limits.py) through the MaaS bootstrap bundle. The hook matches this exact serving image digest and its guard entrypoint. It gives each new OvisOCR3 serving container 16 GiB RAM, a four-CPU quota, and zero swap. GPUStack configures shared memory at 4 GiB. The guard reads the memory and CPU cgroups before launching vLLM. This also applies when GPUStack schedules a replacement.

Confirm that eligible workers have the hook before scaling up. Verify the actual container assignment, read-only NFS mount, Docker `HostConfig` and cgroups. `Memory` and `MemorySwap` should both be `17179869184`, `NanoCpus` should be `4000000000`, and `memory.swap.max` should be `0`. Automatic error restart stays disabled. When upgrading either pinned image, validate the creation path with the new digest before deployment.

## Two replicas with automatic placement on 16 GiB GPUs

Each vLLM instance uses `--gpu-memory-utilization=0.32` (~5.12 GiB on a 16 GiB device) and claims 6 GiB in GPUStack. GPUStack can spread the replicas across workers or put both on one GPU when it has room for both claims. The claim is a scheduling reservation. Measure actual device memory during startup and inference. A 32% budget already served plain OCR in the two-GPU trial. [GPUStack model settings](gpustack-dual-4080.patch.json) · [Two-GPU trial](../../docs/framework/DYNAMIC-CONCURRENCY-IMPLEMENTATION.md#ovisocr3-双-gpu-定向实机验收)

1. Verify that Ready workers have the creation-time limits hook, the pinned image, and access to the read-only NFS model. Check allocatable VRAM per GPU and host capacity for the two containers, each with a 16 GiB RAM limit and four-CPU quota. A same-GPU trial needs 12 GiB of allocatable VRAM on one GPU. A cross-worker trial needs 6 GiB on each of two GPUs.
2. Apply the fields to an isolated `test-agent-ovis-dual-...` deployment with the model route disabled. Set `worker_selector={}` and `gpu_selector=null`, merge the VRAM claim into `env`, and add or replace the utilization flag in `backend_parameters`. Keep the pinned backend version, BF16, 16K context, one sequence per replica, and image settings. For the direct inference trial, omit `MAAS_OVIS_EXECUTION_*` from the isolated copy and access its endpoints through the private worker network. GPUStack appends model parameters to the custom command, so the model's utilization flag overrides the backend default. [GPUStack v2.2.3 command assembly](https://github.com/gpustack/gpustack/blob/v2.2.3/gpustack/worker/backends/custom.py#L152-L181)
3. Start with one replica and verify readiness before scaling to two. Record each running instance's worker, GPU UUID, port, and effective 0.32 utilization. Validate both placements using available capacity in the test cluster. Call both instance endpoints concurrently with the ordinary and dense fixtures and the model's plain OCR prompt. Check expected text and `finish_reason=stop`. Measure latency and device and per-process VRAM, and check engine logs for OOM. Replace one test instance through GPUStack and repeat the concurrent check. Remove the test deployment and verify its containers and listeners are gone. [OCR fixtures](../../tests/ovisocr3/README.md)
4. Before applying the profile to the MaaS deployment, drain OvisOCR3 work and save its current GPUStack model and MaaS capacity settings for rollback. Verify both guarded replicas, the original authenticated route, and the effective MaaS capacity before resuming traffic. MaaS counts a shared GPU only when its layout is in `shared_gpu_layouts` and applies the pool's `hard_limit`. Restore the saved settings if the cutover checks fail. [MaaS capacity policy](../maas/README.md#隔离环境启用动态准入)

On 2026-11-20, GPUStack automatically placed two isolated replicas on the same RTX 4080 SUPER once its previous workload was stopped. Both engines reported the 0.32 budget. Two pairs of overlapping requests on the 1440×1440 fixture returned the expected text with `finish_reason=stop` in 6.0–8.2 seconds per request. Across 33 worker metric samples the device reached 11,054 of 16,376 MiB. The first run's 50-second readiness window expired during cold startup. The second run used a longer window. Both test deployments, their three instances and their ports were cleaned up, and the test left no new model file records.

The daily `ovisocr3` deployment reached 2/2 Ready on separate workers with both selectors cleared. An overlapping OCR request to each instance and a separate request through the original authenticated route all returned the expected text and received GPU completion callbacks. OvisOCR3 submissions resumed with one effective MaaS slot. The active MaaS policy admits one PP-OCRv4 plus one OvisOCR3 as the only shared layout for this GPU. The same-card OvisOCR3 result above covers GPUStack placement and inference.

## MaaS execution guard pilot

For the daily route pilot, build this directory's Dockerfile on the previously deployed OvisOCR3 manifest `sha256:34ab50787af81b270477f49457ea8f8162eec231f7266ce9d6552c19a39e6b20`. The verified execution guard release is `sha256:9e8e8270b98c3e9500926e65854747df6c5fd77c602897cf9eeac34f2101de42`. Use the full registry reference with the digest and verify that the worker's creation hook recognizes it.

Set `MAAS_OVIS_EXECUTION_ORIGIN` to the private HTTPS authority, `MAAS_OVIS_EXECUTION_KEY_FILE` to the per-GPU credential file, and `MAAS_OVIS_EXECUTION_ROUTE` to the guarded MaaS upstream route. Use `MAAS_OVIS_EXECUTION_CA_FILE` for a private CA. The authority uses the existing `MAAS_PP_OCR_WORKER_KEYS_FILE` and activity tables. Enable `execution_guard` in the catalog only after every target behind the route runs the guarded profile. The existing template remains opt-in.

The guarded profile accepts one non-streaming completion per MaaS task attempt. It uses one GPU with serial scheduling on the pinned vLLM 0.22.1 runtime. A scheduler-terminal request ID from EngineCore drives the finish callback, including after client disconnect. Output-layer completion and abort enqueueing can occur before the EngineCore terminal event. A missing terminal event or failed receipt keeps the existing `UNKNOWN` protection. [vLLM output handling](https://github.com/vllm-project/vllm/blob/v0.22.1/vllm/v1/engine/output_processor.py) · [vLLM EngineCore scheduling](https://github.com/vllm-project/vllm/blob/v0.22.1/vllm/v1/engine/core.py)

## Existing workers with a separate NFS mount

Older workers may expose the shared export at `/srv/maas/models-nfs` while their GPUStack container mirrors a local `/srv/maas/models`. Use the [per-model mount example](compat-nfs-model.mount.example) to mount only the new model directory under the standard path. Derive the server from the already verified read-only NFS source. Refuse an existing target, wrong source, or writable mount; retain all local model directories.

Install the unit under the exact name returned by `systemd-escape --path --suffix=mount /srv/maas/models/ovisocr3-5b252386`. Start it and verify source/config/size. Existing private recursive binds do not see a later host submount. Refresh the GPUStack worker through the existing [controlled startup handoff](../bootstrap/clone/README.md#无盘-gpu-接续试点), with a verified stopped worker, a new scoped grant and preserved GPU UUID/worker ID. **Do not directly restart the old container: its single-use startup permit has already been consumed.** Verify the new worker namespace, management readiness and existing inference containers. Never reboot a VM. A worker whose standard model root is already NFS needs no compatibility mount. Enabling a unit is not evidence of a tested VM boot.

## Verify and remove

Verify `/health`, `/v1/models`, then a real image request through the authenticated gateway. Use the fixed prompt for the selected task, disable thinking, reject `finish_reason=length`, and validate task-specific JSON. Native JSON templates are not constrained decoding. This deployment does not add a MaaS adapter or cross-page Chromium orchestration.

The unchanged upstream image reports one `pip check` error: `pygobject 3.42.1 requires pycairo, which is not installed.` Keep that result explicit; do not claim a clean dependency check. Torch, Transformers, vLLM imports and native Qwen3.5 architecture registration are checked separately. No GTK dependency is installed or removed for OCR.

Before removing an unused model mount, stop and remove its model deployment through GPUStack, check that no other workload uses the directory, then disable/stop only the owned mount unit and remove its empty local target. Do not force-unmount NFS or remove shared weights/Registry blobs while they remain referenced.

The launcher check reuses the project's existing pytest environment:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/ovisocr3/test_entrypoint.py tests/ovisocr3/test_execution_guard.py -q -p no:cacheprovider
```

See [deployment research](../../docs/reports/ovisocr3-deployment-20261118.md) and [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11).
