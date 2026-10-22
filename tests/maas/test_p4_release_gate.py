"""The eight-hour release gate must not inherit a prior run's verification."""
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('p4_release',Path(__file__).resolve().parents[2]/'scripts/maas/render_p4_release.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

def test_only_complete_same_run_eight_hour_evidence_passes():
    end={'state':'COMPLETE','elapsed_seconds':28800,'duration_target_seconds':28800,'accepted':5760,'succeeded':5760,
         'fixtures':40,'errors':[],'run_id':'e6-8h-test','database_check':{'tasks':5760,'succeeded':5760,'usage_events':5760}}
    verification={'passed':True,'run_id':'e6-8h-test','target_seconds':28800}
    assert mod.eight_hour_passed(end,verification)
    assert not mod.eight_hour_passed({**end,'state':'RUNNING'},verification)
    assert not mod.eight_hour_passed({**end,'elapsed_seconds':12000},verification)
    assert not mod.eight_hour_passed(end,{**verification,'run_id':'old-run'})
    assert not mod.eight_hour_passed(end,{})
