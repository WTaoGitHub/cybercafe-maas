"""Expose EngineCore's terminal IDs for the single-GPU pilot."""
from vllm.v1.core.sched.scheduler import Scheduler


class OvisScheduler(Scheduler):
    def __init__(self, *, vllm_config, include_finished_set, **kwargs):
        parallel = vllm_config.parallel_config
        if (parallel.world_size != 1 or parallel.data_parallel_size != 1 or
                parallel.distributed_executor_backend != 'uni' or vllm_config.scheduler_config.async_scheduling or
                any(x is not None for x in (vllm_config.kv_transfer_config, vllm_config.ec_transfer_config,
                                            vllm_config.speculative_config))):
            raise ValueError('Ovis execution receipts require a serial, single-GPU EngineCore')
        super().__init__(vllm_config=vllm_config, include_finished_set=True, **kwargs)
