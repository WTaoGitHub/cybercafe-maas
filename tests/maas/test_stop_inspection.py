import importlib.util,json
from pathlib import Path
import pytest
path=Path(__file__).resolve().parents[2]/'scripts/maas/runtime/inspect_stop.py'
spec=importlib.util.spec_from_file_location('inspect_stop',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


@pytest.mark.parametrize('kind',['worker','paused-model','gpu-container','gpu-process','empty'])
def test_observation_is_conservative_and_never_authorizes_poweroff(kind):
    names={'worker':'wta-gpustack-worker','paused-model':'model-abc-run-0','gpu-container':'standalone','gpu-process':'other','empty':'other'}
    c={'Name':'/'+names[kind],'Config':{},'HostConfig':{},'State':{'Paused':kind=='paused-model'}}
    if kind=='gpu-container':c['HostConfig']['DeviceRequests']=[{'Capabilities':[['gpu']]}]
    def run(args):
        if args[:2]==['docker','ps']:return 'abc'
        if args[:2]==['docker','inspect']:return json.dumps([c])
        return '123, python, 512\n' if kind=='gpu-process' else ''
    result=mod.inspect(run)
    assert result['backend_stop_observed']==(kind=='empty')
    assert result['safe_to_poweroff'] is False


def test_unavailable_gpu_inventory_never_becomes_empty():
    def run(args):
        if args[0]=='docker':return ''
        raise RuntimeError('device unavailable')
    with pytest.raises(RuntimeError):mod.inspect(run)
