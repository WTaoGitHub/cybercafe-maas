from datetime import datetime

from maas_platform.gpu_time import BEIJING, report, save
from test_integration import env


def test_postgres_daily_and_monthly_reports(env):
    _, service, *_ = env
    start = int(datetime(2026, 11, 1, tzinfo=BEIJING).timestamp())
    session = '3ae80fae-a100-4fa4-a799-5fab9dd0d7c4'
    for offset, cards in [(-30, {'gpu-a': True}), (30, {'gpu-a': False}), (90, None)]:
        save(service.db, start + offset, session, cards)
    daily = report(service.db, '2026-11-01', start + 120)
    monthly = report(service.db, '2026-11', start + 120)
    assert daily['gpus'] == monthly['gpus'] == {
        'gpu-a': {'online_seconds': 30, 'not_ready_seconds': 0, 'unknown_seconds': 90},
    }
