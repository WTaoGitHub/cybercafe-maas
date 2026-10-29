import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from maas_bench.controller import PilotController


def test_balanced_pilot_shrink_rebuilds_model_in_owned_pause_not_assuming_native_removal_balance(tmp_path,monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/maas/runtime'))
    pool=importlib.import_module('executor_pool');replicas=importlib.import_module('replica_capacity_matrix')
    monkeypatch.setattr(pool,'LOCK',tmp_path/'lock');monkeypatch.setattr(pool,'LIVE',tmp_path)
    (tmp_path/'maintenance').mkdir();cfg=tmp_path/'cfg.json';journal=tmp_path/'journal.json'
    journal.write_text(json.dumps({'phase':'RESUMED'}));cfg.write_text(json.dumps({'journal':str(journal)}))
    (tmp_path/'maintenance/active.json').write_text(json.dumps({'config':str(cfg)}))
    model={'id':1,'replicas':8,'worker_selector':{'test':'yes'}};state={'model':copy.deepcopy(model),'workers':[1]*4+[2]*4};mutations=[];commands=[]
    snapshot={'generation':10,'model':model,'workers':[{'id':1},{'id':2}]}
    def api(path):
        return {'items':[{'state':'running','worker_id':w} for w in state['workers']]} if path.endswith('/instances') else state['model']
    def change(api,before,target,selector):
        mutations.append(target)
        # A direct shrink keeps an unbalanced prefix. A fresh spread is equal.
        state['workers']=state['workers'][:target] if target<len(state['workers']) else [1]*(target//2)+[2]*(target//2)
        state['model']={**before,'replicas':target};return state['model']
    def cli(command,*args):
        commands.append(command);return {'generation':11,'managed_requests_drained':True}
    monkeypatch.setattr(replicas,'change_model',change)
    c=object.__new__(PilotController);c.api=api;c.quiet=lambda:None;c.pool=SimpleNamespace(cli=cli)
    c.probe=SimpleNamespace(capture=lambda:copy.deepcopy(snapshot))
    c.observer=SimpleNamespace(snapshot=lambda:{'paused':True,'generation':11})
    c._model_replicas(snapshot,4)
    assert mutations==[0,4] and state['workers'].count(1)==state['workers'].count(2)==2
    assert commands[0]=='pause' and commands[-1]=='resume'
