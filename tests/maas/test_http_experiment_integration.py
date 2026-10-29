"""Real temporary TCP/API/PG; no TLS, stub auth, mock OCR: tool validation only."""
import json
import socket
import httpx
from test_integration import env
from test_t3_integration import setup
from maas_bench.http_experiment import run


def test_disposable_http_accept_reject_drain_tool(env,monkeypatch,tmp_path):
    client,s,settings,_,obs,_=setup(env,monkeypatch,tmp_path);obs.close()
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    dsn=tmp_path/'dsn';dsn.write_text(settings.database_url);dsn.chmod(0o600)
    config={'tool_validation_only':True,'output_directory':str(tmp_path/'http-run'),'allow_reset_test_schema':True,
        'test_database_dsn_file':str(dsn),'gpustack_url':'http://auth.test','bindings_file':str(settings.bindings_file),
        'catalog_file':str(settings.catalog_file),'pepper_file':str(settings.pepper_file),'project':'a','revision':'ocr-r1',
        'customer_key_file':str(settings.bindings_file.parent/'key-a'),'api_port':port,'base_url':'http://127.0.0.1:'+str(port),'ca_file':True}
    run(config,test_auth_client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'data':[]}))))
    result=json.loads((tmp_path/'http-run/status.json').read_text())
    assert result['state']=='COMPLETE' and result['profile_id']=='tool-validation-auth-stub-no-capacity'
    assert len(result['acceptance'])==2 and all(p['rejected_without_task']>0 for p in result['rejection'])
    assert result['recovery'][0]['http_groups']['202']['count']==3
