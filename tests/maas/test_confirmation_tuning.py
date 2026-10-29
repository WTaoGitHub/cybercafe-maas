from test_queue_tuning import load
import pytest


def test_near_best_uses_joint_tested_tuple_and_prefers_lower_resources():
    m=load('confirmation_tuning.py');c=[{'parameters':{'replicas':8,'executors':8}},{'parameters':{'replicas':12,'executors':12}}]
    rows=[{'parameters':p['parameters'],'throughput_per_second':v} for p,values in zip(c,([20,20.5,20],[21,21,21])) for v in values]
    groups,chosen=m.summarize(c,rows)
    assert chosen['parameters']['replicas']==8 and len(groups)==2
    with pytest.raises(ValueError):m.summarize(c,rows[:-1])
