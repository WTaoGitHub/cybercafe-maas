import json
import pytest
from maas_platform.config import Settings


def env(monkeypatch,tmp_path):
 values={'MAAS_DATABASE_URL':'postgresql://test/maas_test','MAAS_ARTIFACT_ROOT':str(tmp_path/'artifacts'),
         'MAAS_GPUSTACK_URL':'http://gpustack.internal/','MAAS_BINDINGS_FILE':str(tmp_path/'bindings'),
         'MAAS_CATALOG_FILE':str(tmp_path/'catalog'),'MAAS_PEPPER_FILE':str(tmp_path/'pepper')}
 for k,v in values.items():monkeypatch.setenv(k,v)


def test_configurable_pilot_limits(monkeypatch,tmp_path):
 env(monkeypatch,tmp_path);monkeypatch.setenv('MAAS_MAX_QUEUED','40');monkeypatch.setenv('MAAS_MAX_PROJECT_QUEUED','10')
 monkeypatch.setenv('MAAS_HEARTBEAT_SECONDS','0.5');monkeypatch.setenv('MAAS_LEASE_SECONDS','5')
 c=Settings.from_env();assert c.max_queued==40 and c.max_project_queued==10 and c.heartbeat_seconds==.5


@pytest.mark.parametrize('name,value',[
 ('MAAS_MAX_RUNNING','0'),('MAAS_MAX_QUEUED','-1'),('MAAS_HEARTBEAT_SECONDS','nan'),
 ('MAAS_HEARTBEAT_SECONDS','30'),('MAAS_MAX_PROJECT_RUNNING','3'),('MAAS_MAX_INPUT_BYTES','bad'),
 ('MAAS_MIN_FREE_BYTES','-1'),('MAAS_RESULT_TTL_SECONDS','0'),('MAAS_MAX_STORAGE_BYTES','0'),
 ('MAAS_MAX_PROJECT_STORAGE_BYTES','999999999999'),('MAAS_ORPHAN_GRACE_SECONDS','0'),('MAAS_CLEANUP_BATCH_SIZE','0'),
 ('MAAS_CLEANUP_TIME_BUDGET_MS','0'),('MAAS_CLEANUP_INTERVAL_SECONDS','-1')])
def test_invalid_limits_rejected(monkeypatch,tmp_path,name,value):
 env(monkeypatch,tmp_path);monkeypatch.setenv(name,value)
 with pytest.raises(ValueError):Settings.from_env()


def test_reject_non_postgres_database(monkeypatch,tmp_path):
 env(monkeypatch,tmp_path);monkeypatch.setenv('MAAS_DATABASE_URL','sqlite:///tasks.db')
 with pytest.raises(ValueError):Settings.from_env()


def test_gc_budget_and_interval_from_env(monkeypatch,tmp_path):
 env(monkeypatch,tmp_path);monkeypatch.setenv('MAAS_CLEANUP_TIME_BUDGET_MS','75');monkeypatch.setenv('MAAS_CLEANUP_INTERVAL_SECONDS','10')
 c=Settings.from_env();assert c.cleanup_time_budget_ms==75 and c.cleanup_interval_seconds==10


def test_invalid_model_runtime_rejected(monkeypatch,tmp_path):
 env(monkeypatch,tmp_path);c=Settings.from_env()
 c.catalog_file.write_text(json.dumps({'mock.echo':{'driver':'mock','revision':'1','max_runtime_s':-10}}))
 with pytest.raises(ValueError):c.catalog()


def test_network_catalog_needs_no_collector_files(monkeypatch,tmp_path):
 env(monkeypatch,tmp_path);c=Settings.from_env()
 model={'driver':'gpustack.ocr','revision':'ocr-r1','max_runtime_s':30,'upstream_model':'registered-ocr'}
 c.catalog_file.write_text(json.dumps({'ocr.default':model}))
 assert c.catalog()['ocr.default']==model
 model.pop('upstream_model');c.catalog_file.write_text(json.dumps({'ocr.default':model}))
 with pytest.raises(ValueError):c.catalog()
