import os
import pytest
import maas_platform.db as module


def test_packaged_sql_assets_required_before_connecting(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'__file__',str(tmp_path/'db.py'))
    with pytest.raises(FileNotFoundError):module.Database('never-connect').migrate()
    (tmp_path/'migrations').mkdir()
    with pytest.raises(RuntimeError,match='No database migration'):module.Database('never-connect').migrate()


def test_sorted_numeric_migrations_exclude_metadata(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'__file__',str(tmp_path/'db.py'));folder=tmp_path/'migrations';folder.mkdir()
    for name in ['002_b.sql','001_a.sql','._001_a.sql','notes.sql']:(folder/name).write_text('-- SQL')
    assert [p.name for p in module.migration_files()]==['001_a.sql','002_b.sql']


def test_unreadable_migration_directory_fails_closed(tmp_path,monkeypatch):
    if os.getuid()==0:pytest.skip('Permission check requires non-root user')
    monkeypatch.setattr(module,'__file__',str(tmp_path/'db.py'));folder=tmp_path/'migrations';folder.mkdir();(folder/'001_a.sql').write_text('-- SQL');folder.chmod(0)
    try:
        with pytest.raises(PermissionError):module.migration_files()
    finally:folder.chmod(0o700)
