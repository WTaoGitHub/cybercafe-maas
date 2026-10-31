"""Finite, read-only committed-success witness through measurement and drain."""
import threading
from .queue_policy import CompletionClock


class CompletionWitness:
    def __init__(self,observer,emit,on_error):
        self.observer=observer;self.emit=emit;self.on_error=on_error;self.clock=CompletionClock();self.halt=threading.Event();self.failure=None
        self.thread=threading.Thread(target=self._run,name='bench-committed-success-witness')

    def _observe(self):
        row=self.observer.snapshot(include_ids=True);new=self.clock.observe(row)
        self.emit({'kind':'completion_witness','observed_db_epoch':row['completion_observed_db_at'],'new_success_ids':new})

    def _run(self):
        try:
            while not self.halt.is_set():self._observe();self.halt.wait(.5)
        except Exception as exc:self.failure=exc;self.on_error(exc)

    def start(self):self.thread.start()

    def stop(self):
        self.halt.set()
        if self.thread.ident is not None:self.thread.join(30)
        if self.thread.is_alive():raise RuntimeError('Completion witness failed to stop')

    def verify(self,accepted):
        self.stop()
        if self.failure:raise self.failure
        self._observe();timings=self.observer.task_timings()
        if {r['id'] for r in timings}!=set(accepted) or len(timings)!=len(accepted):raise RuntimeError('Completion witness scope mismatch')
        elapsed,rows=self.clock.verify(timings);self.emit({'kind':'completion_witness_final','observations':rows})
        return {'task_deadline_seconds':120,'completion_observation_sample_seconds':.5,'observed_completion_max':max(elapsed,default=None),'all_accepted_observed_before_deadline':True,'clock_kind':'FIRST_COMMITTED_SUCCESS_OBSERVATION_UPPER_BOUND_NOT_CLIENT_LATENCY'}
