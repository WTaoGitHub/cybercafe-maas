from pathlib import Path
import importlib.util


def module():
    path=Path(__file__).resolve().parents[2]/'scripts/maas/render_tuning_report.py';s=importlib.util.spec_from_file_location('tuning_report',path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def test_missing_and_short_soak_do_not_satisfy_final_gate():
    m=module();assert m.assess({})['state']=='INCOMPLETE'
    p={'replicas':8};data={k:{'state':'COMPLETE'} for k in m.FILES};data['T0-BASELINE']['parameters']=p
    data['T6-CONFIRM']['selected_candidate']={'parameters':p}
    data['T6-SOAK'].update(points=[{'point_id':'T6-SOAK-segment-'+str(i),'state':'COMPLETE','measurement_seconds':300} for i in range(24)],
        wall_seconds=7300,controlled_executor_drain=[{}],selected_parameters=p,restored_parameters=p)
    data['T3-EXTEND']['state']='SKIPPED_CONDITION_NOT_MET'
    assert m.assess(data)['state']=='COMPLETE'
    data['T6-SOAK']['points'][0]['measurement_seconds']=299
    assert m.assess(data)['state']=='INCOMPLETE' and m.assess(data)['selected_parameters'] is None


def test_final_tuple_must_be_the_joint_confirmed_tuple_and_runtime_restored():
    m=module();data={k:{'state':'COMPLETE'} for k in m.FILES}
    data['T6-CONFIRM']['selected_candidate']={'parameters':{'replicas':8}}
    data['T6-SOAK']['selected_parameters']={'replicas':12}
    assert not m.assess(data)['gates']['JOINT-TUPLE'] and not m.assess(data)['gates']['RESTORED']


def test_interruption_recovery_does_not_override_failed_selection_or_unrun_soak():
    m=module();p={'replicas':4,'executors':4}
    data={'T0-BASELINE':{'state':'COMPLETE','parameters':p},
        'T4-QUEUE':{'state':'INTERRUPTED_REQUIRES_INSPECTION'},
        'T6-SOAK':{'state':'NOT_RUN_DEPENDENCY_FAILED'},
        'RUNTIME-RESTORATION':{'state':'COMPLETE','restored_parameters':p,'paused':False,'unknown':0,'real_resume_probes_passed':2}}
    summary=m.assess(data)
    assert summary['runtime_restored_after_interruption']
    assert summary['interrupted_stages']==['T4-QUEUE'] and 'T6-SOAK' in summary['not_run_stages']
    assert summary['state']=='INCOMPLETE' and summary['selected_parameters'] is None
    assert not summary['gates']['T4-QUEUE'] and not summary['gates']['RESTORED']
    data['RUNTIME-RESTORATION']['unknown']=1
    assert not m.assess(data)['runtime_restored_after_interruption']
