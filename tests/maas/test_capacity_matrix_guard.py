import copy,importlib.util,sys
from pathlib import Path
import pytest
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'scripts/maas/runtime'))
spec=importlib.util.spec_from_file_location('capacity_matrix',root/'scripts/maas/runtime/replica_capacity_matrix.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


def test_matrix_changes_only_replica_and_worker_placement():
    old={'id':18,'replicas':4,'worker_selector':{'os':'linux'},'env':{'claim':'2GiB'},'backend':'ocr'};calls=[]
    def api(path,data=None,method=None):
        if method=='PUT':calls.append(copy.deepcopy(data));return data
        return copy.deepcopy(old)
    result=mod.change_model(api,old,2,{'os':'linux','worker-name':'test'})
    assert result['env']==old['env'] and result['backend']==old['backend'] and len(calls)==1


@pytest.mark.parametrize('drift',['replicas','env'])
def test_matrix_refuses_external_changes_without_overwrite(drift):
    old={'id':18,'replicas':4,'worker_selector':{},'env':{'claim':'2GiB'}};changed=copy.deepcopy(old);changed[drift]=6 if drift=='replicas' else {'claim':'other'}
    def api(path,data=None,method=None):
        assert method!='PUT';return changed
    with pytest.raises(RuntimeError,match='externally'):mod.change_model(api,old,0,{})
