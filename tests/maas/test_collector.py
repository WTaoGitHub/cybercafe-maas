import copy,json,os,subprocess,sys,time
from pathlib import Path
import pytest
from maas_platform.collector import Collector,build_snapshot,inventory_command
from maas_platform.inventory import collect_inventory
from maas_platform.versioning import register_pin


def fixture():
    route={'id':11,'name':'ocr-p2-r2','access_policy':'authed'}
    targets=[{'model_id':12,'weight':100}]
    model={'id':12,'cluster_id':1,'backend':'OCR-custom','backend_version':'r2'}
    backend={'backend_name':'OCR-custom','version_configs':{'r2':{'image_name':'local/ocr:r2'}}}
    pin=register_pin('r2',route,targets,model,backend,'sha256:'+'a'*64,['001','002'])
    control={'route':route,'targets':targets,'model':model,'backend':backend,'version':{'version':'v2.2.3'},
        'workers':[{'name':name,'cluster_id':1,'state':'ready','unreachable':False} for name in pin.worker_names],
        'instances':[{'id':i,'worker_name':name,'worker_ip':'10.0.0.'+str(i),'port':40000+i,'state':'running'} for i,name in enumerate(pin.worker_names,1)]}
    inventory={name:{'worker':name,'image_name':pin.image_name,'image_id':pin.image_id,'running_image_ids':[pin.image_id],
        'paused':False,'observed_at':time.time()} for name in pin.worker_names}
    gateway={'policies':{'higress.io/proxy-next-upstream-tries':'2','higress.io/proxy-next-upstream':'error,timeout,http_503,http_502,non_idempotent'}}
    return pin,control,inventory,gateway


@pytest.mark.parametrize('fault',['offline','third-worker','runtime-image','paused','policy','drift','wrong-identity','slow'])
def test_collector_refuses_unsafe_observations(fault):
    pin,control,inventory,gateway=fixture();start=time.time()
    if fault=='offline':control['workers'][1]['state']='not_ready'
    if fault=='third-worker':control['workers'].append({'name':'003','cluster_id':1,'state':'ready'})
    if fault=='runtime-image':inventory['002']['running_image_ids']=['sha256:'+'b'*64]
    if fault=='paused':inventory['002']['paused']=True
    if fault=='policy':gateway['policies']['higress.io/proxy-next-upstream-tries']='0'
    if fault=='drift':control['model']['env']={'changed':'yes'}
    if fault=='wrong-identity':inventory['002']['worker']='001'
    if fault=='slow':start-=21
    with pytest.raises(Exception):build_snapshot(pin,control,inventory,gateway,start)


def test_failed_collection_revokes_previous_attestation_without_repinning(tmp_path):
    pin,control,inventory,gateway=fixture()
    path=tmp_path/'pin.json';path.write_text(json.dumps(pin.public()));before=path.read_bytes()
    collector=Collector({'root':str(tmp_path)},admin=lambda *a:None)
    collector.observe=lambda:(control,inventory,gateway,time.time())
    assert collector.collect_once()
    assert (tmp_path/'preflight.json').stat().st_mode & 0o077==0
    control['model']['env']={'drift':'detected'}
    assert not collector.collect_once()
    assert not (tmp_path/'preflight.json').exists()
    assert json.loads((tmp_path/'health.json').read_text())['code']=='MODEL_REVISION_CHANGED'
    assert path.read_bytes()==before


def test_inventory_filters_container_scope_and_exports_no_environment():
    seen=[];image='sha256:'+'a'*64
    def run(args):
        seen.append(args)
        if args[1:3]==['image','inspect']:return image
        if args[1]=='ps':return 'production-abc-run-0\npilot-abc-run-0\npilot-abc-pause\n'
        assert args[-1]=='pilot-abc-run-0'
        assert '.Config.Env' not in args[3]
        return json.dumps({'image_id':image,'running':True,'paused':False})
    result=collect_inventory('002','local/ocr:r2','pilot',run=run)
    assert result['running_image_ids']==[image]
    assert set(result)=={'worker','image_name','image_id','running_image_ids','paused','observed_at'}


def test_agent_rejects_arbitrary_command_before_inspecting_docker():
    root=Path(__file__).resolve().parents[2]
    env={**os.environ,'SSH_ORIGINAL_COMMAND':'touch /tmp/maas-forbidden','PYTHONPATH':str(root)}
    result=subprocess.run([sys.executable,str(root/'scripts/maas/runtime/inventory_agent.py')],env=env,capture_output=True)
    assert result.returncode==64 and not result.stdout


def test_inventory_connection_disables_ambient_config_and_forwarding():
    args=inventory_command({'host':'10.11.31.2','identity_file':'/private/key','known_hosts':'/private/hosts'})
    assert args[-1]=='inventory' and args[1:3]==['-F','/dev/null']
    assert 'StrictHostKeyChecking=yes' in args and 'ForwardAgent=no' in args
