import pytest

from scripts.maas.runtime.endurance_24h import Interrupted, Meter, lanes, require_storage


def test_nominal_cycle_and_weighted_occupancy():
    schedule = [lanes(80, index / 100, .4) for index in range(3000)]
    assert [sum(component) / len(schedule) for component in zip(*schedule)] == [4, 1.6, .8]
    meter = Meter()
    for mono, used in ((10, 6), (16, 7), (20, 7)):
        meter.push(dict(mono=mono, capacity=8, used=used, own=used))
    assert meter.result() == {'occupancy': .8, 'own': .8}
    with pytest.raises(Interrupted, match='SAMPLE_GAP'):
        meter.push(dict(mono=31, capacity=8, used=7, own=7))


def test_artifact_forecast_honors_logical_and_disk_limits():
    snapshot = dict(project_bytes=0, project_quota=2**31, bytes=0, quota=2**34, disk_reserve_bytes=2**33)
    require_storage(snapshot, 8192, 86400)
    with pytest.raises(Interrupted, match='STORAGE_FORECAST'):
        require_storage(snapshot, 65536, 86400)
    with pytest.raises(Interrupted, match='STORAGE_FORECAST'):
        require_storage({**snapshot, 'disk_reserve_bytes': 2**20}, 8192, 86400)
