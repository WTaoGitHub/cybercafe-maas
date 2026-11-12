"""Conservative public-model adapter for the GPUStack v2.2.3 write contract."""
from maas_replica_apply import ScaleUp
from maas_replica_native import binding


MODEL_UPDATE_FIELDS = frozenset("""
    source huggingface_repo_id huggingface_filename model_scope_model_id model_scope_file_path local_path
    name description meta replicas ready_replicas categories placement_strategy cpu_offloading
    distributed_inference_across_workers worker_selector gpu_selector backend backend_version
    backend_parameters image_name run_command env restart_on_error distributable extended_kv_cache
    speculative_config generic_proxy lora_list cluster_id owner_principal_id access_policy
""".split())
PUBLIC_ONLY = {'id', 'created_at', 'updated_at', 'has_stale_lora_instances'}


def model_settings(row):
    if not MODEL_UPDATE_FIELDS <= row.keys() or row.keys() - MODEL_UPDATE_FIELDS - PUBLIC_ONLY:
        raise ValueError('GPUStack deployment schema differs from the verified public model contract')
    # The scheduler owns readiness. GPUStack updates only explicitly set fields.
    return {key: row[key] for key in MODEL_UPDATE_FIELDS if key != 'ready_replicas'}


def deployment_binding(row):
    # ModelPublic strips internal LoRA names. Avoid round-tripping those settings.
    if row.get('lora_list'):
        raise ValueError('LoRA deployments are outside the MaaS Apply scope')
    return binding(row, serializer=model_settings)


def from_environment(*, policy=None):
    if policy is not None and getattr(policy, 'schema_version', 1) == 2:
        from .replica_writer_v2 import ReconcileWriter
        return ReconcileWriter.from_environment()
    return ScaleUp.from_environment(serializer=model_settings)
