"""No-network tests for original-direction fencing and safe hook migration."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/maas/runtime'))
from maas_platform.boundary_policy import CHAIN,REPLY_MATCH,rules
import boundary
import persist_boundary


def test_runtime_host_and_docker_only_accept_reply_direction():
 inv={'interface':'eth1','trusted_bridge':[]}
 for family in (4,6):
  rows=boundary.chain_rules(inv,family)
  for chain in ([boundary.HOST,boundary.DOCKER] if family==4 else [boundary.HOST]):
   selected=[r for r in rows if r.startswith('-A '+chain+' ')]
   reply=f'-A {chain} {REPLY_MATCH} -j RETURN'
   assert reply in selected
   assert selected.index(reply) < next(i for i,r in enumerate(selected) if '-j REJECT' in r)
   assert all('--ctdir REPLY' in r for r in selected if 'ESTABLISHED' in r)


def test_migration_installs_pre_dnat_hook_before_detaching_legacy(monkeypatch):
 calls=[];legacy={'iptables':True,'ip6tables':True}
 def run(*args,**kwargs):
  calls.append((args,kwargs))
  text='';rc=0
  if '-S' in args and args[-1]=='PREROUTING':text='-A PREROUTING -j '+CHAIN+'\n'
  if 'raw' in args and '-C' in args:rc=0 if legacy[args[0]] else 1
  if 'raw' in args and '-D' in args:legacy[args[0]]=False
  return subprocess.CompletedProcess(args,rc,text,'')
 monkeypatch.setattr(persist_boundary,'run',run)
 cfg={'interface':'eth1','bridge_subnets':['172.17.0.0/16']}
 persist_boundary.apply_raw(cfg,[])
 for binary in ('iptables','ip6tables'):
  installed=next(i for i,(a,k) in enumerate(calls) if a[0]==binary and 'mangle' in a and '-C' in a)
  removed=next(i for i,(a,k) in enumerate(calls) if a[0]==binary and 'raw' in a and '-D' in a)
  assert installed<removed
  restore=next(k['input'] for a,k in calls if a[0]==binary+'-restore')
  assert restore.startswith('*mangle\n') and REPLY_MATCH in restore
 assert not any('-F' in a or '-X' in a for a,k in calls)


def test_failed_replacement_keeps_legacy_fence_attached(monkeypatch):
 calls=[]
 def run(*args,**kwargs):
  calls.append(args)
  if args[0].endswith('-restore'):
   raise subprocess.CalledProcessError(1,args)
  return subprocess.CompletedProcess(args,0,'','')
 monkeypatch.setattr(persist_boundary,'run',run)
 with pytest.raises(subprocess.CalledProcessError):
  persist_boundary.apply_raw({'interface':'eth1','bridge_subnets':[]},[])
 assert not any('raw' in a and '-D' in a for a in calls)


@pytest.mark.parametrize('state', ['recreated', 'missing', 'wrong_driver', 'changed_subnet',
                                   'changed_gateway', 'missing_gateway', 'boot'])
def test_refresh_reconciles_only_approved_docker_bridge(monkeypatch, state):
    name = 'approved-app'
    new_bridge = 'br-123456789abc'
    client = {'network_name': name, 'interface': 'br-aaaaaaaaaaaa',
              'source_cidr': '172.18.0.0/16', 'listen_address': '172.18.0.1', 'port': 18831}
    cfg = {'node': 'XZ-31-001', 'interface': 'eth1', 'bridge_subnets': [],
           'container_http_clients': [client]}
    network = {
        'Id': '123456789abcdef', 'Name': name,
        'Driver': 'overlay' if state == 'wrong_driver' else 'bridge',
        'Scope': 'local', 'Options': {},
        'IPAM': {'Config': [{
            'Subnet': '172.18.0.0/24' if state == 'changed_subnet' else '172.18.0.0/16',
            'Gateway': '172.18.0.2' if state == 'changed_gateway' else '172.18.0.1',
        }]},
    }
    addresses = [{'family': 'inet', 'local': '172.18.0.1', 'prefixlen': 16}]
    link = {'ifname': new_bridge, 'linkinfo': {'info_kind': 'bridge'},
            'addr_info': [] if state == 'missing_gateway' else addresses}

    def run(*args, timeout):
        assert state != 'boot'
        if args[0] == 'docker':
            assert args[-1] == name
            if state == 'missing':
                raise subprocess.CalledProcessError(1, args)
            data = network
        else:
            assert args[-1] == new_bridge
            data = link
        return subprocess.CompletedProcess(args, 0, json.dumps([data]), '')

    monkeypatch.setattr(persist_boundary, 'run', run)
    resolved, unavailable = persist_boundary.resolve_container_clients(cfg, state != 'boot')
    base = {**cfg, 'container_http_clients': []}
    exceptions = set(rules(resolved, 4)) - set(rules(base, 4))
    if state == 'recreated':
        assert exceptions == {
            f'-A {CHAIN} -i {new_bridge} -s 172.18.0.0/16 -d 172.18.0.1/32 -p tcp --dport 18831 -j RETURN'
        }
    else:
        assert not exceptions
    assert unavailable == ([] if state in ('boot', 'recreated') else [name])
    assert rules(resolved, 6) == rules(base, 6)
    assert client['interface'] == 'br-aaaaaaaaaaaa'
