from test_queue_tuning import load
import pytest

def rows(gains):
    return [{'repeat':repeat,'parameters':{'executors':e},'throughput_per_second':10*(1+gain if e==8 else 1),'state':'COMPLETE'}
        for repeat,gain in enumerate(gains,1) for e in (6,8)]


def test_extension_needs_repeatable_not_average_gain():
    m=load('extended_tuning.py')
    assert m.extension_condition(rows([.1,.07]))['trigger_met']
    assert not m.extension_condition(rows([.2,.01]))['trigger_met']
    with pytest.raises(ValueError):m.extension_condition(rows([.2]))
