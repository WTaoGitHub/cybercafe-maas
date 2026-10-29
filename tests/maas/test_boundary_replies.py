"""No-network tests for original-direction fencing and safe hook migration."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/maas/runtime'))
from maas_platform.boundary_policy import CHAIN,REPLY_MATCH
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
